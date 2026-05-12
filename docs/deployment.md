# Deployment

How to run qx services across the dev → staging → production path.

## Local development

`deploy/docker-compose.yaml` brings up the supporting infrastructure your
service needs:

- Postgres 16 (data store)
- Redis 7 (cache, idempotency, rate limit)
- NATS 2.10 with JetStream (message broker)
- Grafana + Prometheus + Jaeger (observability stack)
- Mailhog (SMTP capture for testing email flows)
- MinIO (S3-compatible object storage)

```bash
docker compose -f deploy/docker-compose.yaml up -d
```

Your service runs on the host, connecting to these containers via
localhost ports. Env vars to point at them:

```bash
export QX_DB__URL="postgresql+asyncpg://postgres:postgres@localhost:5432/qx"
export QX_CACHE__URL="redis://localhost:6379/0"
export QX_NATS__SERVERS='["nats://localhost:4222"]'
```

Run the service:

```bash
uv run uvicorn myservice.main:app --reload --port 8000
```

Tail the logs in Grafana, traces in Jaeger (port 16686), metrics in
Prometheus (port 9090).

## Container build

Two-stage Dockerfile per service:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services/myservice ./services/myservice
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "myservice.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Worker (background) services use the same image with a different command:

```dockerfile
CMD ["python", "-m", "myservice.worker"]
```

## Kubernetes

The `deploy/k8s/` directory has manifest skeletons:

- `deployment-api.yaml` — the HTTP-serving deployment.
- `deployment-worker.yaml` — the integration-event worker.
- `deployment-outbox-relay.yaml` — the outbox relay.
- `service.yaml` — ClusterIP for the API deployment.
- `hpa.yaml` — horizontal autoscaler for the API based on CPU.
- `configmap.yaml` — non-secret env.
- `external-secret.yaml` — pulls secrets from your secret manager via ESO.
- `servicemonitor.yaml` — Prometheus operator service monitor pointing at
  `/metrics`.

Probes are pre-configured to call the framework endpoints:

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /readyz
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
```

Important: run **at most one** outbox relay per service. The
`DistributedLock` enforces this even if you accidentally scale the
deployment > 1, but you should set `replicas: 1` explicitly.

## Helm chart

`deploy/helm/qx-service/` is a parametric chart that wraps the K8s
manifests. Values you typically override per service:

```yaml
service:
  name: identity
  image:
    repository: ghcr.io/qx/identity
    tag: "v1.4.2"
  apiReplicas: 3
  workerReplicas: 2
  env:
    QX_DB__URL: { from: { secretKeyRef: { name: identity-db, key: url } } }
  resources:
    api:
      requests: { cpu: "100m", memory: "256Mi" }
      limits:   { cpu: "500m", memory: "512Mi" }
```

## Migrations

Run database migrations as a pre-deploy Kubernetes Job, not inside the
service container at startup. Reasons:

- Multiple replicas racing to migrate is a great way to lose data.
- Failures during migration shouldn't roll back the service deployment in
  flight (you want to discover them *before* new pods come up).
- Migration logs deserve their own discoverable surface.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: identity-migrate-{{ .Values.service.image.tag }}
spec:
  template:
    spec:
      containers:
        - name: migrate
          image: "{{ .Values.service.image.repository }}:{{ .Values.service.image.tag }}"
          command: ["alembic", "upgrade", "head"]
          envFrom: [{ secretRef: { name: identity-db } }]
      restartPolicy: Never
```

Wire this as a Helm pre-install / pre-upgrade hook.

## Configuration sources

The settings layer (`qx-core.QxSettings`) reads from:

1. Environment variables (highest priority)
2. `.env` files in order: `.env`, `.env.{environment}`, `.env.local`
3. Defaults from the model definitions

In production: use env vars sourced from your secret manager via
External-Secrets-Operator or equivalent. Don't bake secrets into ConfigMaps.

## Observability hookups

- **Logs**: send to your aggregator (Loki, Cloudwatch, Datadog) via a
  log driver. Logs are JSON; pre-parsed fields land as structured columns.
- **Metrics**: Prometheus scrapes `/metrics`. Recording rules and alerts
  in `deploy/observability/prometheus-rules/`.
- **Traces**: OTLP/gRPC to an OpenTelemetry collector or directly to your
  backend (Tempo, Jaeger, Honeycomb).

## Rollouts

- **Blue/green** for API services: deploy new version alongside, switch
  ingress on success, keep old running for 1 hour as cheap rollback.
- **Rolling** for workers: workers are stateless and idempotent (events
  are at-least-once); rolling is fine.
- **Recreate** for the outbox relay: there's only one instance anyway;
  a brief gap is OK (events sit in the outbox until the new relay picks
  them up).

Alembic migrations run as a pre-deploy hook; service rollout is gated on
the migration job's success.

## Operational runbook

- **Outbox table growing**: relay is failing to publish. Check the
  `last_error` column. Common causes: NATS connectivity, JetStream
  misconfiguration, schema mismatch (consumer expected `payload.email`,
  got `payload.user_email`).
- **Worker lag growing**: consumer can't keep up. Scale worker replicas
  (each pulls a disjoint subset thanks to JetStream durables).
- **High error rate after deploy**: roll back via the previous Helm
  revision. Pre-deploy DB migrations are designed to be backward-compat,
  so the old version should run fine against the new schema.
