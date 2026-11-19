"""The provisioning engine: orchestrates the instance/binding lifecycle
against the OSB client, writing state transitions to the database. This is
the only module that mutates ServiceInstance/ServiceBinding status."""

import logging
import threading
import time

from catalog import osb
from catalog.models import ServiceBinding, ServiceInstance

logger = logging.getLogger(__name__)

# poll cadence: how often to ask the broker "done yet?" for async operations
POLL_INTERVAL = 3  # seconds
POLL_TIMEOUT = 600  # give up after 10 minutes


def sync_catalog(broker):
    """Fetch the broker's /v2/catalog and upsert classes + plans.
    Existing records are matched by external_id; new ones are created;
    removed ones are left (the admin deletes explicitly — silent removal
    would be a trap)."""
    from catalog.models import ServiceClass, ServicePlan

    services = osb.fetch_catalog(broker)
    counts = {"classes": 0, "plans": 0}
    for svc in services:
        cls, created = ServiceClass.objects.update_or_create(
            broker=broker,
            external_id=svc["id"],
            defaults={
                "name": svc.get("name", svc["id"]),
                "description": svc.get("description", ""),
                "bindable": svc.get("bindable", True),
                "tags": svc.get("tags", []),
                "metadata": svc.get("metadata", {}),
            },
        )
        if created:
            counts["classes"] += 1
        for plan in svc.get("plans", []):
            _, p_created = ServicePlan.objects.update_or_create(
                service_class=cls,
                external_id=plan["id"],
                defaults={
                    "name": plan.get("name", plan["id"]),
                    "description": plan.get("description", ""),
                    "free": plan.get("free", True),
                    "bindable": plan.get("bindable", True),
                    "metadata": plan.get("metadata", {}),
                },
            )
            if p_created:
                counts["plans"] += 1
    return counts


def provision_instance(instance):
    """Send the OSB provision call and handle the response:
    sync (201) → ready; async (202) → start a background poll thread."""
    broker = instance.service_class.broker
    try:
        sync, dashboard_url = osb.provision(
            broker,
            instance.broker_instance_id,
            instance.service_class.external_id,
            instance.service_plan.external_id,
            instance.parameters,
        )
    except osb.OSBError as err:
        instance.status = "failed"
        instance.error_message = str(err)
        instance.save()
        return

    if dashboard_url:
        instance.dashboard_url = dashboard_url

    if sync:
        instance.status = "ready"
        instance.save()
    else:
        instance.save()  # keep status=provisioning
        _start_poll_thread(instance)


def deprovision_instance(instance):
    """Send the OSB deprovision call; the caller deletes the DB record
    after the broker confirms."""
    broker = instance.service_class.broker
    try:
        osb.deprovision(
            broker,
            instance.broker_instance_id,
            instance.service_class.external_id,
            instance.service_plan.external_id,
        )
    except osb.OSBError as err:
        # 410 (gone) is fine — the broker already cleaned up
        if err.status != 410:
            instance.status = "failed"
            instance.error_message = str(err)
            instance.save()
            return False
    return True


def create_binding(binding):
    """Send the OSB bind call; sync response carries the credentials."""
    broker = binding.service_instance.service_class.broker
    try:
        sync, credentials = osb.bind(
            broker,
            binding.service_instance.broker_instance_id,
            binding.broker_binding_id,
            binding.service_instance.service_class.external_id,
            binding.service_instance.service_plan.external_id,
            binding.parameters,
        )
    except osb.OSBError as err:
        binding.status = "failed"
        binding.save()
        raise

    if sync and credentials:
        binding.credentials = credentials
        binding.status = "ready"
        binding.save()
    else:
        binding.save()
        # OSB bind is almost always synchronous; if the broker went async,
        # poll (rare path)
        _start_bind_poll_thread(binding)


def delete_binding(binding):
    """Send the OSB unbind call; the caller deletes the DB record."""
    broker = binding.service_instance.service_class.broker
    try:
        osb.unbind(
            broker,
            binding.broker_binding_id,
            binding.service_instance.service_class.external_id,
            binding.service_instance.service_plan.external_id,
        )
    except osb.OSBError as err:
        if err.status != 410:
            binding.status = "failed"
            binding.save()
            return False
    return True


# --- background poll threads (the simplest async that works single-process) ---


def _start_poll_thread(instance):
    """Poll the broker's last_operation until succeeded/failed."""
    t = threading.Thread(
        target=_poll_instance,
        args=(instance.pk,),
        daemon=True,
    )
    t.start()


def _poll_instance(instance_pk):
    """The poll loop: ask the broker every POLL_INTERVAL seconds until the
    operation terminal or POLL_TIMEOUT. Updates the DB record in place."""
    instance = ServiceInstance.objects.get(pk=instance_pk)
    broker = instance.service_class.broker
    deadline = time.monotonic() + POLL_TIMEOUT

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            state, description = osb.poll_operation(
                broker,
                instance.broker_instance_id,
                instance.service_class.external_id,
                instance.service_plan.external_id,
            )
        except osb.OSBError as err:
            logger.warning("poll error for %s: %s", instance, err)
            continue

        if state == "succeeded":
            instance.status = "ready"
            instance.save()
            return
        if state == "failed":
            instance.status = "failed"
            instance.error_message = description or "broker reported failure"
            instance.save()
            return
        # else: still in progress, keep polling

    instance.status = "failed"
    instance.error_message = f"timed out after {POLL_TIMEOUT}s"
    instance.save()


def _start_bind_poll_thread(binding):
    """Poll for async bind completion (rare — most brokers are sync)."""
    t = threading.Thread(
        target=_poll_binding,
        args=(binding.pk,),
        daemon=True,
    )
    t.start()


def _poll_binding(binding_pk):
    """The bind poll loop — simpler than instance (OSB has no
    last_operation for bindings; re-PUT and check)."""
    binding = ServiceBinding.objects.get(pk=binding_pk)
    broker = binding.service_instance.service_class.broker
    deadline = time.monotonic() + POLL_TIMEOUT

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            sync, credentials = osb.bind(
                broker,
                binding.service_instance.broker_instance_id,
                binding.broker_binding_id,
                binding.service_instance.service_class.external_id,
                binding.service_instance.service_plan.external_id,
                binding.parameters,
            )
            if credentials:
                binding.credentials = credentials
                binding.status = "ready"
                binding.save()
                return
        except osb.OSBError:
            continue

    binding.status = "failed"
    binding.save()
