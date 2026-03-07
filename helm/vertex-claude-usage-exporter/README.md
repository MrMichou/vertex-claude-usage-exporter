# vertex-claude-usage-exporter Helm Chart

## Installation

```bash
# Create a secret with your GCP service account key
kubectl create secret generic gcp-credentials \
    --from-file=key.json=/path/to/sa-key.json \
    -n <namespace>

# Install the chart
helm install claude-metrics ./helm/vertex-claude-usage-exporter \
    --set config.gcpProjectId=my-gcp-project \
    --set gcpCredentials.existingSecret=gcp-credentials
```

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `config.gcpProjectId` | GCP project ID (required) | `""` |
| `config.pushgatewayUrl` | Pushgateway address | `"pushgateway:9091"` |
| `config.httpsProxy` | HTTPS proxy URL | `""` |
| `config.httpProxy` | HTTP proxy URL | `""` |
| `config.noProxy` | No-proxy list | `"localhost,.cluster.local,..."` |
| `cronjob.schedule` | Cron schedule | `"0 2 * * *"` |
| `cronjob.image.repository` | Container image | `python` |
| `cronjob.image.tag` | Image tag | `"3.11-slim"` |
| `cronjob.resources` | Resource requests/limits | See `values.yaml` |
| `gcpCredentials.existingSecret` | Existing secret name for SA key | `""` |
| `gcpCredentials.secretKey` | Key name within the secret | `"key.json"` |
| `pushgateway.enabled` | Deploy Pushgateway | `true` |
| `pushgateway.persistence.enabled` | Enable PVC for Pushgateway | `false` |
| `serviceMonitor.enabled` | Create ServiceMonitor (Prometheus Operator) | `false` |
| `serviceMonitor.interval` | Scrape interval | `"30s"` |

## Behind a Corporate Proxy

```bash
helm install claude-metrics ./helm/vertex-claude-usage-exporter \
    --set config.gcpProjectId=my-project \
    --set config.httpsProxy=http://proxy:3128 \
    --set config.httpProxy=http://proxy:3128
```

## With ServiceMonitor (Prometheus Operator)

```bash
helm install claude-metrics ./helm/vertex-claude-usage-exporter \
    --set config.gcpProjectId=my-project \
    --set serviceMonitor.enabled=true
```
