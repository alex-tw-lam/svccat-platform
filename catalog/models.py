"""Data model: Broker, ServiceClass, ServicePlan, Tenant, ServiceInstance,
ServiceBinding. Pure persistence — the OSB client and provisioning engine
live in osb.py and provisioning.py."""

import json
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models
from django.utils import timezone


class Broker(models.Model):
    """A registered OSB 2.x service broker (the platform talks to it
    directly — no Kubernetes in between)."""

    name = models.SlugField(max_length=100, unique=True)
    url = models.URLField()
    username = models.CharField(max_length=200)
    password = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServiceClass(models.Model):
    """A service offering from a broker's catalog (maps to OSB
    service definition)."""

    broker = models.ForeignKey(Broker, on_delete=models.CASCADE, related_name="classes")
    external_id = models.UUIDField()  # the OSB catalog's service id
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    bindable = models.BooleanField(default=True)
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("broker", "external_id")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServicePlan(models.Model):
    """A plan within a service class (maps to OSB plan)."""

    service_class = models.ForeignKey(ServiceClass, on_delete=models.CASCADE, related_name="plans")
    external_id = models.UUIDField()
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    free = models.BooleanField(default=True)
    bindable = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("service_class", "external_id")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.service_class.name}/{self.name}"


class Tenant(models.Model):
    """A platform-level tenant (not a K8s namespace — the platform owns
    isolation)."""

    name = models.SlugField(max_length=100, unique=True)
    display_name = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# Instance lifecycle: provisioning → ready | failed; deprovisioning → gone
INSTANCE_IN_FLIGHT = ("provisioning", "updating", "deprovisioning")


class ServiceInstance(models.Model):
    """A provisioned service instance. The platform sends the OSB provision
    call and tracks the async state via last_operation polling."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="instances")
    service_class = models.ForeignKey(ServiceClass, on_delete=models.PROTECT)
    service_plan = models.ForeignKey(ServicePlan, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("provisioning", "Provisioning"),
            ("ready", "Ready"),
            ("updating", "Updating"),
            ("failed", "Failed"),
            ("deprovisioning", "Deprovisioning"),
        ],
        default="provisioning",
    )
    # the ID we send to the broker on PUT /v2/service_instances/:id
    broker_instance_id = models.UUIDField(default=uuid.uuid4, unique=True)
    dashboard_url = models.URLField(blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.name}/{self.name}"

    @property
    def in_flight(self):
        return self.status in INSTANCE_IN_FLIGHT


class ServiceBinding(models.Model):
    """A binding to a service instance — the platform calls the broker's
    bind endpoint and stores the returned credentials (encrypted)."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="bindings")
    service_instance = models.ForeignKey(
        ServiceInstance, on_delete=models.CASCADE, related_name="bindings"
    )
    name = models.CharField(max_length=200)
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("creating", "Creating"),
            ("ready", "Ready"),
            ("failed", "Failed"),
            ("deleting", "Deleting"),
        ],
        default="creating",
    )
    broker_binding_id = models.UUIDField(default=uuid.uuid4, unique=True)
    _credentials_encrypted = models.BinaryField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.name}/{self.name}"

    @property
    def in_flight(self):
        return self.status in ("creating", "deleting")

    @property
    def credentials(self):
        """Decrypt and return the binding credentials (a dict)."""
        if not self._credentials_encrypted:
            return {}
        f = Fernet(settings.CREDENTIAL_KEY.encode() if isinstance(settings.CREDENTIAL_KEY, str) else settings.CREDENTIAL_KEY)
        return json.loads(f.decrypt(bytes(self._credentials_encrypted)))

    @credentials.setter
    def credentials(self, value):
        """Encrypt and store the binding credentials."""
        f = Fernet(settings.CREDENTIAL_KEY.encode() if isinstance(settings.CREDENTIAL_KEY, str) else settings.CREDENTIAL_KEY)
        self._credentials_encrypted = f.encrypt(
            json.dumps(value).encode()
        )
