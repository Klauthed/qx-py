# qx-service Helm chart

A parametric Helm chart for any [Qx](https://qx.dev) service. Wires
up the API deployment, worker, outbox relay, service, HPA, ConfigMap, secret
sync (via External Secrets Operator), ServiceMonitor (Prometheus Operator),
and a pre-deploy Alembic migration Job.

## Quick start

```bash
helm install identity-service ./deploy/helm/qx-service \
  --set image.repository=ghcr.io/qx/identity-service \
  --set image.tag=v1.4.2 \
  --set service.name=identity-service \
  --set service.packageName=identity_service
```

For different services, copy `values.yaml` to a per-service overrides file and
adjust the secret-store mappings.

## What gets deployed

| Resource | Replicas | Notes |
|---|---|---|
| Deployment `<name>-api` | scaled via HPA | rolling update, security-hardened |
| Deployment `<name>-worker` | configurable | NATS consumer |
| Deployment `<name>-outbox-relay` | **1** | singleton, Recreate strategy |
| Service `<name>` | n/a | ClusterIP fronting the API |
| HorizontalPodAutoscaler `<name>-api` | n/a | CPU+memory based |
| ConfigMap `<name>-config` | n/a | non-secret env |
| ExternalSecret `<name>-secrets` | n/a | synced from secret store |
| ServiceMonitor `<name>` | n/a | Prometheus scraping |
| Job `<name>-migrate` | n/a | runs `alembic upgrade head` pre-deploy |

## Customization

The `values.yaml` is heavily commented. Common overrides:

- **Disable the worker** for read-only services: `--set worker.enabled=false`.
- **Disable the outbox relay** for stateless services with no events:
  `--set outboxRelay.enabled=false`.
- **Different secret backend**: change `externalSecrets.secretStoreRef` and the
  `data[*].remoteRef.key` paths.

## Prerequisites

- [External Secrets Operator](https://external-secrets.io/) if
  `externalSecrets.enabled=true` (default).
- [Prometheus Operator](https://prometheus-operator.dev/) if
  `serviceMonitor.enabled=true` (default).

If either is unavailable, set the corresponding flag to `false`; the chart
will skip that template.
