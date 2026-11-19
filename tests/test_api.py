"""API tests: exercise the full REST surface against an in-memory broker
mock. Each test creates the prerequisite chain (tenant → broker → class →
plan → instance) through the API, verifying the models are wired."""

from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from catalog.models import Broker, ServiceBinding, ServiceClass, ServiceInstance, ServicePlan, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def tenant(client):
    resp = client.post("/api/v1/tenants/", {"name": "team-a", "display_name": "Team A"})
    assert resp.status_code == 201
    return Tenant.objects.get(name="team-a")


@pytest.fixture
def broker(client):
    resp = client.post(
        "/api/v1/brokers/",
        {"name": "test-broker", "url": "http://broker.example", "username": "u", "password": "p"},
    )
    assert resp.status_code == 201
    return Broker.objects.get(name="test-broker")


@pytest.fixture
def catalog(broker):
    """Sync a fake catalog into the broker."""
    fake_catalog = {
        "services": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "redis",
                "description": "Redis cache",
                "bindable": True,
                "tags": ["cache"],
                "plans": [
                    {"id": "22222222-2222-2222-2222-222222222222", "name": "small", "description": "256Mi"},
                    {"id": "33333333-3333-3333-3333-333333333333", "name": "medium", "description": "1Gi"},
                ],
            }
        ]
    }
    with mock.patch("requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = fake_catalog
        resp = APIClient().post(f"/api/v1/brokers/{broker.pk}/sync/")
    assert resp.status_code == 200
    return ServiceClass.objects.get(external_id="11111111-1111-1111-1111-111111111111")


class TestTenantCRUD:
    def test_create_list_delete(self, client):
        client.post("/api/v1/tenants/", {"name": "t1"})
        assert len(client.get("/api/v1/tenants/").json()) == 1
        client.delete("/api/v1/tenants/1/")
        assert len(client.get("/api/v1/tenants/").json()) == 0


class TestBrokerSync:
    def test_sync_populates_classes_and_plans(self, client, broker, catalog):
        classes = client.get("/api/v1/classes/").json()
        assert len(classes) == 1
        assert classes[0]["name"] == "redis"
        assert len(classes[0]["plans"]) == 2
        assert "small" in [p["name"] for p in classes[0]["plans"]]


class TestInstanceLifecycle:
    def test_provision_sync_201(self, client, tenant, broker, catalog):
        plan = catalog.plans.first()
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {}
            resp = client.post(
                "/api/v1/instances/",
                {
                    "tenant": tenant.pk,
                    "service_class": catalog.pk,
                    "service_plan": plan.pk,
                    "name": "my-redis",
                },
            )
        assert resp.status_code == 201
        assert resp.json()["status"] == "ready"

    def test_deprovision_deletes(self, client, tenant, broker, catalog):
        plan = catalog.plans.first()
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {}
            client.post(
                "/api/v1/instances/",
                {"tenant": tenant.pk, "service_class": catalog.pk, "service_plan": plan.pk, "name": "x"},
            )
        inst = ServiceInstance.objects.first()
        with mock.patch("requests.delete") as delete:
            delete.return_value.status_code = 200
            resp = client.delete(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == 204
        assert ServiceInstance.objects.count() == 0


class TestBindingLifecycle:
    def test_bind_returns_credentials(self, client, tenant, broker, catalog):
        plan = catalog.plans.first()
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {}
            client.post(
                "/api/v1/instances/",
                {"tenant": tenant.pk, "service_class": catalog.pk, "service_plan": plan.pk, "name": "x"},
            )
        inst = ServiceInstance.objects.first()
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {
                "credentials": {"host": "db", "password": "secret"}
            }
            resp = client.post(
                "/api/v1/bindings/",
                {"tenant": tenant.pk, "service_instance": inst.pk, "name": "b1"},
            )
        assert resp.status_code == 201
        assert resp.json()["status"] == "ready"
        # credentials are stored encrypted; reveal via the action
        binding = ServiceBinding.objects.first()
        cred_resp = client.get(f"/api/v1/bindings/{binding.pk}/credentials/")
        assert cred_resp.status_code == 200
        assert cred_resp.json()["credentials"]["host"] == "db"
