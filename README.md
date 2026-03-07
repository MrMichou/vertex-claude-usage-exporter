# Vertex Claude Usage Exporter

Export Claude/Anthropic model usage metrics from Google Cloud Vertex AI to Prometheus, with cost estimation and Grafana dashboards.

## Overview

This tool queries **GCP Cloud Logging** audit logs to track Claude API usage on Vertex AI, estimates costs based on calibrated token averages, and exports metrics to **Prometheus** via Pushgateway for visualization in **Grafana**.

```
Cloud Logging --> Python Exporter --> Pushgateway --> Prometheus --> Grafana
```

### Features

- Per-user and per-model request tracking
- Cost estimation with calibrated per-model token averages (derived from real GCP billing)
- Prometheus metrics export via Pushgateway
- Pre-built Grafana dashboards (overview, per-user, token breakdown)
- Helm chart for Kubernetes deployment
- Supports all Claude model families (Opus, Sonnet, Haiku)

## Prerequisites

- **GCP Project** with [Data Access Audit Logs](https://cloud.google.com/logging/docs/audit/configure-data-access) enabled for `aiplatform.googleapis.com`
- **GCP credentials** (ADC or service account with `roles/logging.viewer`)
- **Python 3.11+**

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Generate a CSV report for yesterday
python scripts/claude_usage_with_cost.py -p <GCP_PROJECT_ID>

# Push metrics to Prometheus Pushgateway
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> -g localhost:9091

# Dry run (show metrics without pushing)
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> --dry-run
```

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/grafana_push_metrics.py` | Main exporter: push metrics to Prometheus Pushgateway |
| `scripts/grafana_push_metrics_standalone.py` | Standalone version (no external dependencies beyond stdlib) |
| `scripts/claude_usage_with_cost.py` | Generate CSV/JSON cost reports |
| `scripts/claude_usage_report.py` | Basic usage report (request counts only) |
| `scripts/grafana_push_metrics_gcloud.sh` | Bash version using `gcloud` CLI |
| `scripts/grafana_push_metrics_simple.sh` | Lightweight `sh`-compatible version using `curl` + `jq` |

## Prometheus Metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `claude_vertex_requests_total` | user, model, date, project | Requests per user/model |
| `claude_vertex_estimated_cost_usd` | user, model, date, project | Estimated cost (USD) |
| `claude_vertex_estimated_input_tokens` | user, model, date, project | Estimated input tokens |
| `claude_vertex_estimated_output_tokens` | user, model, date, project | Estimated output tokens |
| `claude_vertex_total_requests` | model, date, project | Total requests per model |
| `claude_vertex_total_cost_usd` | model, date, project | Total cost per model |
| `claude_vertex_unique_users` | date, project | Unique user count |

## Cost Estimation

Since Vertex AI audit logs don't include token counts, costs are estimated using **calibrated per-model token averages** derived from actual GCP billing data:

| Model | Avg Input Tokens | Avg Output Tokens | Input $/1M | Output $/1M |
|-------|------------------|-------------------|------------|-------------|
| claude-opus-4-5/4-6 | 8,871 | 3,548 | $5.00 | $25.00 |
| claude-sonnet-4-5 | 4,820 | 1,928 | $3.00 | $15.00 |
| claude-sonnet-4 | 3,309 | 1,323 | $3.00 | $15.00 |
| claude-haiku-4-5 | 840 | 336 | $1.00 | $5.00 |
| claude-3-5-haiku | 382 | 153 | $1.00 | $5.00 |

Override with global averages:

```bash
python scripts/grafana_push_metrics.py -p <PROJECT> \
    --avg-input-tokens 5000 --avg-output-tokens 2000

# Or disable calibration entirely
python scripts/grafana_push_metrics.py -p <PROJECT> --no-calibrated
```

## Deployment

### Docker

```bash
docker build -t vertex-claude-usage-exporter .
docker run --rm \
    -v ~/.config/gcloud:/home/appuser/.config/gcloud:ro \
    vertex-claude-usage-exporter \
    -p <GCP_PROJECT_ID> -g pushgateway:9091
```

### Helm (Kubernetes)

```bash
helm install claude-metrics ./helm/vertex-claude-usage-exporter \
    --set config.gcpProjectId=<GCP_PROJECT_ID> \
    --set config.pushgatewayUrl=pushgateway:9091
```

See [helm/vertex-claude-usage-exporter/README.md](helm/vertex-claude-usage-exporter/README.md) for all configuration options.

### Raw Kubernetes Manifests

If you prefer not to use Helm, raw manifests are available in `k8s/`.

### Cron (bare metal)

```bash
# Push daily at 2 AM
0 2 * * * /path/to/venv/bin/python /path/to/scripts/grafana_push_metrics.py \
    -p PROJECT_ID -g pushgateway:9091
```

## Grafana Dashboards

Import the JSON files from `dashboards/` into Grafana:

| Dashboard | File | Description |
|-----------|------|-------------|
| Overview | `grafana_dashboard.json` | Requests and cost by model, daily trends |
| Users | `grafana_dashboard_users.json` | Per-user breakdown, pivot tables by date |
| Tokens | `grafana_dashboard_tokens.json` | Input/output token usage over time |

### Setup

1. Deploy Prometheus Pushgateway (included in Helm chart)
2. Configure Prometheus to scrape Pushgateway with `honor_labels: true`
3. Add Prometheus as a Grafana data source
4. Import dashboards from `dashboards/`

## Releasing

Versions follow [Semantic Versioning](https://semver.org/). To create a new release:

```bash
# Bump version (updates Chart.yaml, commits, and creates a git tag)
./scripts/bump-version.sh patch   # 0.1.0 -> 0.1.1
./scripts/bump-version.sh minor   # 0.1.1 -> 0.2.0
./scripts/bump-version.sh major   # 0.2.0 -> 1.0.0
./scripts/bump-version.sh 1.2.3   # explicit version

# Push the commit and tag to trigger the release pipeline
git push origin main v0.1.1
```

The release pipeline will automatically:
- Build and push the Docker image to `ghcr.io` (tagged with version + `latest`)
- Package the Helm chart and attach it to the GitHub Release
- Generate release notes from commit history

## License

MIT
