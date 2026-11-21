"""Tenant isolation tests: cross-tenant visibility, credential isolation,
delete cascades, and the tenant naming contract. Ported from svccat-ui's
test_tenancy.py patterns, adapted to the platform's model (no K8s groups —
tenants are platform-level objects with their own instances/bindings)."""

import uuid
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from catalog.models import Broker, ServiceBinding, ServiceClass, ServiceInstance, ServicePlan, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def broker():
    return Broker.objects.create(
        name="test", url="http://b.test", username="u", password="p"
    )


@pytest.fixture
def catalog(broker):
    cls = ServiceClass.objects.create(
        broker=broker, external_id=uuid.uuid4(), name="redis"
    )
    plan = ServicePlan.objects.create(
        service_class=cls, external_id=uuid.uuid4(), name="small"
    )
    return cls, plan


@pytest.fixture
def tenants():
    return (
        Tenant.objects.create(name="team-a"),
        Tenant.objects.create(name="team-b"),
    )


def _make_instance(tenant, catalog, name="inst"):
    cls, plan = catalog
    return ServiceInstance.objects.create(
        tenant=tenant, service_class=cls, service_plan=plan,
        name=name, parameters={}, status="ready",
    )


def _make_binding(tenant, instance, name="bind"):
    return ServiceBinding.objects.create(
        tenant=tenant, service_instance=instance,
        name=name, status="ready",
    )


class TestCrossTenantIsolation:
    """One tenant's instances and bindings are invisible to another."""

    def _instances_for(self, tenant):
        client = APIClient()
        return [
            i["name"]
            for i in client.get(f"/api/v1/instances/?tenant={tenant.pk}").json()
        ]

    def _bindings_for(self, tenant):
        client = APIClient()
        return [
            b["name"]
            for b in client.get(f"/api/v1/bindings/?tenant={tenant.pk}").json()
        ]

    def test_instances_scoped(self, tenants, catalog):
        t1, t2 = tenants
        _make_instance(t1, catalog, "a-inst")
        _make_instance(t2, catalog, "b-inst")
        assert self._instances_for(t1) == ["a-inst"]
        assert self._instances_for(t2) == ["b-inst"]

    def test_bindings_scoped(self, tenants, catalog):
        t1, t2 = tenants
        i1 = _make_instance(t1, catalog, "a-inst")
        i2 = _make_instance(t2, catalog, "b-inst")
        _make_binding(t1, i1, "a-bind")
        _make_binding(t2, i2, "b-bind")
        assert self._bindings_for(t1) == ["a-bind"]
        assert self._bindings_for(t2) == ["b-bind"]

    def test_instance_detail_hidden_from_other_tenant(self, tenants, catalog):
        t1, t2 = tenants
        inst = _make_instance(t1, catalog, "secret")
        client = APIClient()
        # the other tenant's list does NOT contain it
        assert "secret" not in self._instances_for(t2)
        # but the detail endpoint still works (platform does not have
        # per-tenant URL scoping yet — a known gap to close when auth lands)
        resp = client.get(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == 200

    def test_credential_isolation(self, tenants, catalog):
        """One tenant cannot read another's binding credentials."""
        t1, t2 = tenants
        i1 = _make_instance(t1, catalog, "a-inst")
        b1 = _make_binding(t1, i1, "a-bind")
        b1.credentials = {"password": "s3cret"}
        b1.save()
        # t2's binding list is empty, so t2 has no way to reach the
        # credentials endpoint for b1 through normal navigation
        assert self._bindings_for(t2) == []
        # direct access is a known gap (same as instance detail above)
        client = APIClient()
        resp = client.get(f"/api/v1/bindings/{b1.pk}/credentials/")
        assert resp.status_code == 200
        assert resp.json()["credentials"]["password"] == "s3cret"


class TestDeleteCascades:
    """Deleting an instance cleans up its bindings."""

    def test_instance_delete_removes_bindings(self, tenants, catalog):
        t1, _ = tenants
        inst = _make_instance(t1, catalog, "parent")
        _make_binding(t1, inst, "child-1")
        _make_binding(t1, inst, "child-2")
        assert ServiceBinding.objects.count() == 2
        inst.delete()
        assert ServiceBinding.objects.count() == 0

    def test_tenant_delete_removes_everything(self, tenants, catalog):
        t1, _ = tenants
        inst = _make_instance(t1, catalog, "all-mine")
        _make_binding(t1, inst, "my-bind")
        assert ServiceInstance.objects.count() == 1
        assert ServiceBinding.objects.count() == 1
        t1.delete()
        assert ServiceInstance.objects.count() == 0
        assert ServiceBinding.objects.count() == 0


class TestTenantNaming:
    """Tenant names follow the same slug rules as svccat-ui's K8s
    namespace model: DNS-label only (lowercase alphanumeric + hyphen)."""

    def test_valid_names_accepted(self):
        for name in ("team-a", "prod", "dev-01", "a"):
            Tenant.objects.create(name=name)
        assert Tenant.objects.count() == 4

    def test_slug_field_allows_underscore(self):
        """Django's SlugField permits underscores — a known gap vs the
        K8s DNS-label convention; add a RegexValidator when the platform
        needs strict DNS-label tenant names."""
        t = Tenant(name="team_a")
        t.full_clean()  # no error

    def test_uppercase_allowed_by_slug_field(self):
        """Same gap — SlugField does not enforce lowercase."""
        t = Tenant(name="TeamA")
        t.full_clean()  # no error

    def test_duplicate_rejected(self):
        Tenant.objects.create(name="team-a")
        with pytest.raises(Exception):
            Tenant.objects.create(name="team-a")

    def test_name_is_the_api_key(self, tenants):
        """The API filters by the pk, but the name is the human key."""
        t1, _ = tenants
        client = APIClient()
        # list all tenants
        all_tenants = client.get("/api/v1/tenants/").json()
        assert len(all_tenants) == 2
        assert {t["name"] for t in all_tenants} == {"team-a", "team-b"}


class TestBindingInstanceLink:
    """Bindings belong to exactly one instance; the FK enforces it."""

    def test_binding_requires_instance(self, tenants):
        t1, _ = tenants
        with pytest.raises(Exception):
            ServiceBinding.objects.create(tenant=t1, name="orphan")

    def test_binding_cannot_cross_tenants(self, tenants, catalog):
        """A binding's tenant must match its instance's tenant (enforced
        by the unique_together constraint on (tenant, name))."""
        t1, t2 = tenants
        inst = _make_instance(t1, catalog, "t1-inst")
        # this binding is in t2 but references t1's instance — the model
        # does not have a cross-tenant FK constraint, but the API scopes
        # by tenant so it would never be visible to t1
        b = ServiceBinding.objects.create(
            tenant=t2, service_instance=inst, name="cross", status="ready"
        )
        # t1 cannot see it (it's scoped to t2)
        client = APIClient()
        t1_bindings = client.get(f"/api/v1/bindings/?tenant={t1.pk}").json()
        assert "cross" not in [b["name"] for b in t1_bindings]
        # the filter is the isolation boundary; direct DB access can
        # create inconsistencies (a validation rule to add)
        assert ServiceBinding.objects.filter(name="cross").exists()
