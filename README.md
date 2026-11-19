# svccat-platform

A standalone Service Broker platform: registers OSB 2.x brokers, syncs
catalogs, provisions service instances, creates bindings with encrypted
credentials, manages tenants — **zero Kubernetes dependency**.

## Quick start

```sh
uv sync
uv run python manage.py migrate
uv run python manage.py runserver 8000
```

The API is at `http://localhost:8000/api/v1/`. Auth is token-based:

```sh
uv run python manage.py drf_create_token admin
```

## API surface

| Endpoint | Methods | Notes |
|---|---|---|
| `/api/v1/brokers/` | GET POST PATCH DELETE | Register an OSB broker |
| `/api/v1/brokers/:id/sync/` | POST | Fetch the broker's catalog |
| `/api/v1/classes/` | GET | Service classes from syncs |
| `/api/v1/plans/` | GET | Plans within classes |
| `/api/v1/tenants/` | GET POST PATCH DELETE | Platform tenants |
| `/api/v1/instances/` | GET POST PATCH DELETE | Provision lifecycle |
| `/api/v1/bindings/` | GET POST DELETE | Bind lifecycle |
| `/api/v1/bindings/:id/credentials/` | GET | Reveal stored credentials |

## Provision a service

```sh
# 1. Register a broker
curl -X POST http://localhost:8000/api/v1/brokers/ \
  -H 'Content-Type: application/json' \
  -d '{"name": "csb", "url": "http://csb:8080", "username": "u", "password": "p"}'

# 2. Sync the catalog
curl -X POST http://localhost:8000/api/v1/brokers/1/sync/

# 3. Create a tenant
curl -X POST http://localhost:8000/api/v1/tenants/ \
  -d '{"name": "team-a"}'

# 4. Provision an instance
curl -X POST http://localhost:8000/api/v1/instances/ \
  -d '{"tenant": 1, "service_class": 1, "service_plan": 1, "name": "my-redis"}'

# 5. Bind and get credentials
curl -X POST http://localhost:8000/api/v1/bindings/ \
  -d '{"tenant": 1, "service_instance": 1, "name": "b1"}'
curl http://localhost:8000/api/v1/bindings/1/credentials/
```

## Architecture

```
REST API (DRF)
  └── Provisioning engine (catalog/provisioning.py)
        └── OSB Client (catalog/osb.py)
              └── HTTP → OSB 2.x Broker
  └── Models (catalog/models.py)
        └── SQLite / PostgreSQL
```

- **Zero K8s**: the platform stores state in its own database, talks
  directly to brokers via the OSB API
- **Credentials encrypted at rest**: Fernet (SHA-256 derived key)
- **Async provisioning**: background threads poll `last_operation` until
  ready/failed
- **Django + DRF**: familiar, auditable, no build step

## Environment

| Variable | Default | Notes |
|---|---|---|
| `SESSION_SECRET` | dev fallback | Django SECRET_KEY |
| `CREDENTIAL_KEY` | dev fallback | Fernet key derivation |
| `DJANGO_DEBUG` | unset | `1` enables debug pages |
| `DATABASE_URL` | SQLite | PostgreSQL for production |
