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
- **Live (intra-day) metrics**: an hourly `--live` mode that buckets the current day by hour, for near-real-time dashboards (separate `claude_vertex_live_*` family, kept apart from the daily batch)
- Pre-built Grafana dashboards (overview, per-user, token breakdown, live)
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

# Push metrics to Prometheus Pushgateway (daily: defaults to yesterday)
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> -g localhost:9091

# Push LIVE metrics for the current day so far, bucketed by hour (run hourly)
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> -g localhost:9091 --live

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
| `scripts/vertex_claude_exporter/` | Shared library (config, parsing, cost estimation) |

## Prometheus Metrics

### Daily (batch)

Per-user and per-model gauges, labelled by `date` (YYYY-MM-DD). Pushed by the nightly job.

| Metric | Labels | Description |
|--------|--------|-------------|
| `claude_vertex_requests_total` | user, model, date, project | Requests per user/model |
| `claude_vertex_estimated_cost_usd` | user, model, date, project | Estimated cost (USD) |
| `claude_vertex_estimated_input_tokens` | user, model, date, project | Estimated input tokens |
| `claude_vertex_estimated_output_tokens` | user, model, date, project | Estimated output tokens |
| `claude_vertex_estimated_cache_write_tokens` | user, model, date, project | Estimated prompt cache write tokens |
| `claude_vertex_estimated_cache_read_tokens` | user, model, date, project | Estimated prompt cache read tokens |
| `claude_vertex_total_requests` | model, date, project | Total requests per model |
| `claude_vertex_total_cost_usd` | model, date, project | Total cost per model |
| `claude_vertex_total_input_tokens` | model, date, project | Total input tokens per model |
| `claude_vertex_total_output_tokens` | model, date, project | Total output tokens per model |
| `claude_vertex_total_cache_write_tokens` | model, date, project | Total cache write tokens per model |
| `claude_vertex_total_cache_read_tokens` | model, date, project | Total cache read tokens per model |
| `claude_vertex_unique_users` | date, project | Unique user count |

### Live (intra-day)

A separate family pushed by the hourly `--live` job, bucketed by `hour` (`"00"`..`"23"`, UTC) at
model+hour granularity (no `user` dimension). Each run re-queries the current day and re-pushes
every hour-so-far under the fixed Pushgateway job `claude_vertex_live` (idempotent, self-healing,
rotates to the new day after midnight).

| Metric | Labels | Description |
|--------|--------|-------------|
| `claude_vertex_live_total_requests` | model, hour, date, project | Requests per model per hour |
| `claude_vertex_live_total_cost_usd` | model, hour, date, project | Estimated cost per hour |
| `claude_vertex_live_total_input_tokens` | model, hour, date, project | Estimated input tokens per hour |
| `claude_vertex_live_total_output_tokens` | model, hour, date, project | Estimated output tokens per hour |
| `claude_vertex_live_total_cache_write_tokens` | model, hour, date, project | Cache write tokens per hour |
| `claude_vertex_live_total_cache_read_tokens` | model, hour, date, project | Cache read tokens per hour |

## Cost Estimation

Since Vertex AI audit logs don't include token counts, costs are estimated using **calibrated per-model token averages** derived from actual GCP billing (June 2026 invoice, incl. prompt-cache SKUs). The averages absorb the account's billed-vs-list factor so estimates match the invoice. Cache write is billed at 1.25× input, cache read at 0.1× input.

| Model | Avg Input | Avg Output | Avg Cache Write | Avg Cache Read | Input $/1M | Output $/1M |
|-------|-----------|------------|-----------------|----------------|------------|-------------|
| claude-opus-4-8 | 207 | 1,097 | 15,491 | 105,548 | $5.00 | $25.00 |
| claude-opus-4-5/4-6 | 449 | 701 | 10,090 | 133,090 | $5.00 | $25.00 |
| claude-sonnet-4-6 | 1,833 | 988 | 13,388 | 109,720 | $3.00 | $15.00 |
| claude-sonnet-4-5 | 14,089 | 641 | 12,218 | 67,746 | $3.00 | $15.00 |
| claude-haiku-4-5 | 524 | 411 | 4,241 | 57,766 | $1.00 | $5.00 |
| claude-3-5-haiku | 382 | 153 | 0 | 0 | $1.00 | $5.00 |

See `scripts/vertex_claude_exporter/config.py` for the full per-model table. Override the cache averages per run with `--avg-cache-write-tokens` / `--avg-cache-read-tokens`.

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

If you prefer not to use Helm, raw manifests are available in `k8s/`:

- `k8s/metrics-pusher-cronjob.yaml` — daily batch CronJob (`0 2 * * *`)
- `k8s/metrics-pusher-live-cronjob.yaml` — hourly live CronJob (`0 * * * *`, runs `--live`)

Both mount the same script ConfigMap, config and credentials.

### Cron (bare metal)

```bash
# Daily batch at 2 AM (yesterday's full day)
0 2 * * * /path/to/venv/bin/python /path/to/scripts/grafana_push_metrics.py \
    -p PROJECT_ID -g pushgateway:9091

# Live: every hour, the current day so far bucketed by hour
0 * * * * /path/to/venv/bin/python /path/to/scripts/grafana_push_metrics.py \
    -p PROJECT_ID -g pushgateway:9091 --live
```

## Grafana Dashboards

Import the JSON files from `dashboards/` into Grafana:

| Dashboard | File | Description |
|-----------|------|-------------|
| Overview | `grafana_dashboard.json` | Requests and cost by model, daily trends |
| Users | `grafana_dashboard_users.json` | Per-user breakdown, pivot tables by date |
| Tokens | `grafana_dashboard_tokens.json` | Input/output token usage over time |
| Live | `grafana_dashboard_live.json` | Current day, bucketed by hour (refresh 1m); reads `claude_vertex_live_*` |

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
