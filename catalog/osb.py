"""OSB 2.x API client: talks directly to service brokers (no Kubernetes).
Each method maps to one OSB endpoint; all calls use basic auth against the
broker's declared credentials. Pure HTTP — no state, no side effects on the
database (that's provisioning.py's job)."""

import requests


class OSBError(Exception):
    """A broker call failed — the message is safe to show the user."""

    def __init__(self, status, broker_message=""):
        self.status = status
        self.broker_message = broker_message
        super().__init__(
            f"broker returned {status}"
            + (f": {broker_message}" if broker_message else "")
        )


def _auth(broker):
    return (broker.username, broker.password)


def _headers(broker):
    return {
        "X-Broker-API-Version": "2.16",
        "Content-Type": "application/json",
    }


def fetch_catalog(broker):
    """GET /v2/catalog — the broker's full list of services and plans."""
    resp = requests.get(
        f"{broker.url}/v2/catalog",
        auth=_auth(broker),
        headers=_headers(broker),
        timeout=30,
    )
    if resp.status_code != 200:
        raise OSBError(resp.status_code, resp.text[:200])
    return resp.json().get("services", [])


def provision(broker, instance_id, service_id, plan_id, parameters, context=None):
    """PUT /v2/service_instances/:id — provision (or update) an instance.
    Returns (accepted, dashboard_url): accepted=True means the broker
    acknowledged; the operation may still be in flight (202)."""
    body = {
        "service_id": str(service_id),
        "plan_id": str(plan_id),
        "parameters": parameters or {},
        "context": context or {},
    }
    resp = requests.put(
        f"{broker.url}/v2/service_instances/{instance_id}",
        auth=_auth(broker),
        headers=_headers(broker),
        json=body,
        timeout=60,
    )
    if resp.status_code not in (200, 201, 202):
        raise OSBError(resp.status_code, resp.text[:200])
    data = resp.json() if resp.content else {}
    return resp.status_code != 202, data.get("dashboard_url", "")


def poll_operation(broker, instance_id, service_id, plan_id, operation=None):
    """GET /v2/service_instances/:id/last_operation — the async poll.
    Returns (state, description): state is 'in progress', 'succeeded' or
    'failed'."""
    params = {
        "service_id": str(service_id),
        "plan_id": str(plan_id),
    }
    if operation:
        params["operation"] = operation
    resp = requests.get(
        f"{broker.url}/v2/service_instances/{instance_id}/last_operation",
        auth=_auth(broker),
        headers=_headers(broker),
        params=params,
        timeout=30,
    )
    if resp.status_code != 200:
        raise OSBError(resp.status_code, resp.text[:200])
    data = resp.json()
    return data.get("state", ""), data.get("description", "")


def deprovision(broker, instance_id, service_id, plan_id):
    """DELETE /v2/service_instances/:id — deprovision.
    Returns accepted (True for sync-complete, False for async-202)."""
    resp = requests.delete(
        f"{broker.url}/v2/service_instances/{instance_id}",
        auth=_auth(broker),
        headers=_headers(broker),
        params={"service_id": str(service_id), "plan_id": str(plan_id)},
        timeout=60,
    )
    if resp.status_code not in (200, 202, 410):
        raise OSBError(resp.status_code, resp.text[:200])
    return resp.status_code != 202


def bind(broker, instance_id, binding_id, service_id, plan_id, parameters=None, context=None):
    """PUT /v2/service_bindings/:id — bind to an instance.
    Returns (accepted, credentials): credentials is a dict from the
    broker (empty for async)."""
    body = {
        "service_id": str(service_id),
        "plan_id": str(plan_id),
        "bind_resource": {"app_guid": str(binding_id)},
    }
    if parameters:
        body["parameters"] = parameters
    if context:
        body["context"] = context
    resp = requests.put(
        f"{broker.url}/v2/service_bindings/{binding_id}",
        auth=_auth(broker),
        headers=_headers(broker),
        json=body,
        timeout=60,
    )
    if resp.status_code not in (200, 201, 202):
        raise OSBError(resp.status_code, resp.text[:200])
    data = resp.json() if resp.content else {}
    return resp.status_code != 202, data.get("credentials", {})


def unbind(broker, binding_id, service_id, plan_id):
    """DELETE /v2/service_bindings/:id — unbind.
    Returns accepted (True for sync-complete, False for async-202)."""
    resp = requests.delete(
        f"{broker.url}/v2/service_bindings/{binding_id}",
        auth=_auth(broker),
        headers=_headers(broker),
        params={"service_id": str(service_id), "plan_id": str(plan_id)},
        timeout=60,
    )
    if resp.status_code not in (200, 202, 410):
        raise OSBError(resp.status_code, resp.text[:200])
    return resp.status_code != 202
