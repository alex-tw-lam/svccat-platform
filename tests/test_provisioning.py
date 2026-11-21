"""Provisioning engine tests: verify the state machine transitions
(provision → sync/async → ready/failed; deprovision; bind/unbind) and the
catalog sync, all against mocked OSB HTTP calls."""

import uuid
from unittest import mock

import pytest

from catalog import osb, provisioning
from catalog.models import Broker, ServiceBinding, ServiceClass, ServiceInstance, ServicePlan, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def broker():
    return Broker.objects.create(
        name="test", url="http://broker.test", username="u", password="p"
    )


@pytest.fixture
def catalog(broker):
    cls = ServiceClass.objects.create(
        broker=broker, external_id=uuid.uuid4(), name="redis", description="cache"
    )
    plan = ServicePlan.objects.create(
        service_class=cls, external_id=uuid.uuid4(), name="small"
    )
    return cls, plan


@pytest.fixture
def tenant():
    return Tenant.objects.create(name="team-a")


class TestSyncCatalog:
    def test_creates_new_classes_and_plans(self, broker):
        fake = {
            "services": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "postgres",
                    "description": "database",
                    "bindable": True,
                    "tags": ["db"],
                    "plans": [
                        {"id": str(uuid.uuid4()), "name": "small", "free": True},
                        {"id": str(uuid.uuid4()), "name": "large", "free": False},
                    ],
                }
            ]
        }
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = fake
            counts = provisioning.sync_catalog(broker)
        assert counts == {"classes": 1, "plans": 2}
        assert ServiceClass.objects.count() == 1
        assert ServicePlan.objects.count() == 2

    def test_reimport_updates_not_duplicates(self, broker):
        svc_id = str(uuid.uuid4())
        plan_id = str(uuid.uuid4())
        fake = {
            "services": [
                {
                    "id": svc_id, "name": "redis", "bindable": True,
                    "plans": [{"id": plan_id, "name": "small"}],
                }
            ]
        }
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = fake
            provisioning.sync_catalog(broker)
            # change the description and re-sync
            fake["services"][0]["description"] = "updated"
            counts = provisioning.sync_catalog(broker)
        assert counts == {"classes": 0, "plans": 0}  # no new records
        assert ServiceClass.objects.count() == 1
        assert ServiceClass.objects.first().description == "updated"

    def test_broker_error_propagates(self, broker):
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 502
            get.return_value.text = "bad gateway"
            with pytest.raises(osb.OSBError):
                provisioning.sync_catalog(broker)


class TestProvisionInstance:
    def _make_instance(self, tenant, catalog):
        cls, plan = catalog
        return ServiceInstance.objects.create(
            tenant=tenant, service_class=cls, service_plan=plan,
            name="test-inst", parameters={},
        )

    def test_sync_provision_sets_ready(self, broker, tenant, catalog):
        inst = self._make_instance(tenant, catalog)
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {"dashboard_url": "http://dash"}
            provisioning.provision_instance(inst)
        inst.refresh_from_db()
        assert inst.status == "ready"
        assert inst.dashboard_url == "http://dash"

    def test_broker_error_sets_failed(self, broker, tenant, catalog):
        inst = self._make_instance(tenant, catalog)
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 500
            put.return_value.text = "internal error"
            provisioning.provision_instance(inst)
        inst.refresh_from_db()
        assert inst.status == "failed"
        assert "500" in inst.error_message

    def test_injects_namespace_and_instance_name(self, broker, tenant, catalog):
        """The platform injects tenant.name as namespace and instance.name
        as instance_name — the broker's Terraform needs both."""
        inst = self._make_instance(tenant, catalog)
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {}
            provisioning.provision_instance(inst)
        body = put.call_args[1]["json"]
        assert body["parameters"]["namespace"] == "team-a"
        assert body["parameters"]["instance_name"] == "test-inst"


class TestDeprovision:
    def test_success_deletes(self, broker, tenant, catalog):
        cls, plan = catalog
        inst = ServiceInstance.objects.create(
            tenant=tenant, service_class=cls, service_plan=plan,
            name="del-me", parameters={},
        )
        with mock.patch("requests.delete") as delete:
            delete.return_value.status_code = 200
            result = provisioning.deprovision_instance(inst)
        assert result is True

    def test_410_gone_is_ok(self, broker, tenant, catalog):
        cls, plan = catalog
        inst = ServiceInstance.objects.create(
            tenant=tenant, service_class=cls, service_plan=plan,
            name="already-gone", parameters={},
        )
        with mock.patch("requests.delete") as delete:
            delete.return_value.status_code = 410
            delete.return_value.text = ""
            result = provisioning.deprovision_instance(inst)
        assert result is True


class TestBind:
    def _make_instance(self, broker, tenant, catalog):
        cls, plan = catalog
        return ServiceInstance.objects.create(
            tenant=tenant, service_class=cls, service_plan=plan,
            name="bi", parameters={}, status="ready",
        )

    def test_sync_bind_stores_credentials(self, broker, tenant, catalog):
        inst = self._make_instance(broker, tenant, catalog)
        binding = ServiceBinding.objects.create(
            tenant=tenant, service_instance=inst, name="b1", parameters={},
        )
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {
                "credentials": {"host": "db.test", "password": "s3cret"}
            }
            provisioning.create_binding(binding)
        binding.refresh_from_db()
        assert binding.status == "ready"
        assert binding.credentials["host"] == "db.test"
        assert binding.credentials["password"] == "s3cret"

    def test_broker_error_raises(self, broker, tenant, catalog):
        inst = self._make_instance(broker, tenant, catalog)
        binding = ServiceBinding.objects.create(
            tenant=tenant, service_instance=inst, name="b2", parameters={},
        )
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 500
            put.return_value.text = "oops"
            with pytest.raises(osb.OSBError):
                provisioning.create_binding(binding)
        binding.refresh_from_db()
        assert binding.status == "failed"

    def test_unbind_success(self, broker, tenant, catalog):
        inst = self._make_instance(broker, tenant, catalog)
        binding = ServiceBinding.objects.create(
            tenant=tenant, service_instance=inst, name="b3",
            status="ready", parameters={},
        )
        with mock.patch("requests.delete") as delete:
            delete.return_value.status_code = 200
            result = provisioning.delete_binding(binding)
        assert result is True


class TestCredentialsEncryption:
    def test_roundtrip(self, broker, tenant, catalog):
        cls, plan = catalog
        inst = ServiceInstance.objects.create(
            tenant=tenant, service_class=cls, service_plan=plan,
            name="enc", parameters={}, status="ready",
        )
        b = ServiceBinding.objects.create(
            tenant=tenant, service_instance=inst, name="enc-b",
        )
        b.credentials = {"host": "x", "password": "y"}
        b.save()
        b.refresh_from_db()
        assert b.credentials == {"host": "x", "password": "y"}

    def test_empty_when_never_set(self, broker, tenant, catalog):
        cls, plan = catalog
        inst = ServiceInstance.objects.create(
            tenant=tenant, service_class=cls, service_plan=plan,
            name="empty", parameters={}, status="ready",
        )
        b = ServiceBinding.objects.create(
            tenant=tenant, service_instance=inst, name="empty-b",
        )
        assert b.credentials == {}


class TestTenantIsolation:
    def test_instances_scoped_to_tenant(self, broker, catalog):
        cls, plan = catalog
        t1 = Tenant.objects.create(name="t1")
        t2 = Tenant.objects.create(name="t2")
        ServiceInstance.objects.create(tenant=t1, service_class=cls, service_plan=plan, name="i1")
        ServiceInstance.objects.create(tenant=t2, service_class=cls, service_plan=plan, name="i2")
        assert t1.instances.count() == 1
        assert t2.instances.count() == 1
        assert t1.instances.first().name == "i1"
