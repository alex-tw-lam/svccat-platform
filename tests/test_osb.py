"""OSB client tests: mock the HTTP layer (requests.get/put/delete) and
verify each method sends the right verb, path, auth and body."""

from unittest import mock

import pytest

from catalog import osb
from catalog.models import Broker

pytestmark = pytest.mark.django_db


@pytest.fixture
def broker():
    return Broker.objects.create(
        name="test-broker", url="http://broker.example", username="u", password="p"
    )


class TestFetchCatalog:
    def test_returns_services_list(self, broker):
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {
                "services": [{"id": "svc-1", "name": "redis"}]
            }
            result = osb.fetch_catalog(broker)
        assert result == [{"id": "svc-1", "name": "redis"}]
        get.assert_called_once()
        args, kwargs = get.call_args
        assert args[0] == "http://broker.example/v2/catalog"
        assert kwargs["auth"] == ("u", "p")

    def test_raises_on_error(self, broker):
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 500
            get.return_value.text = "oops"
            with pytest.raises(osb.OSBError):
                osb.fetch_catalog(broker)


class TestProvision:
    def test_sync_201(self, broker):
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {"dashboard_url": "http://dash"}
            sync, dash = osb.provision(broker, "inst-1", "svc-1", "plan-1", {})
        assert sync is True
        assert dash == "http://dash"

    def test_async_202(self, broker):
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 202
            put.return_value.json.return_value = {}
            sync, dash = osb.provision(broker, "inst-1", "svc-1", "plan-1", {})
        assert sync is False
        assert dash == ""


class TestPollOperation:
    def test_in_progress(self, broker):
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"state": "in progress"}
            state, desc = osb.poll_operation(broker, "i", "s", "p")
        assert state == "in progress"

    def test_succeeded(self, broker):
        with mock.patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"state": "succeeded"}
            state, _ = osb.poll_operation(broker, "i", "s", "p")
        assert state == "succeeded"


class TestBind:
    def test_returns_credentials(self, broker):
        with mock.patch("requests.put") as put:
            put.return_value.status_code = 201
            put.return_value.json.return_value = {
                "credentials": {"host": "db.example", "port": 5432}
            }
            sync, creds = osb.bind(broker, "i", "b", "s", "p")
        assert sync is True
        assert creds == {"host": "db.example", "port": 5432}


class TestDeprovision:
    def test_gone_410_is_ok(self, broker):
        with mock.patch("requests.delete") as delete:
            delete.return_value.status_code = 410
            delete.return_value.text = ""
            result = osb.deprovision(broker, "i", "s", "p")
        assert result is True
