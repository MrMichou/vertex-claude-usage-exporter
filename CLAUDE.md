# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains Python scripts for generating usage reports of Claude/Anthropic models on Google Cloud Vertex AI. The scripts query Cloud Logging to aggregate API calls by user and estimate costs.

## Project Structure

```
gcp_vertex/
├── CLAUDE.md                 # This file
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container image for K8s deployment
├── .github/
│   ├── release.yml                      # Release notes categorization
│   └── workflows/
│       ├── ci.yml                       # CI: lint, test, helm-lint, docker build
│       └── release.yml                  # Release: Docker push, Helm OCI, GitHub Release
├── scripts/                  # Python and Shell scripts
│   ├── claude_usage_report.py           # Basic usage report
│   ├── claude_usage_with_cost.py        # Report with cost estimation
│   ├── grafana_push_metrics.py          # Push metrics to Pushgateway
│   ├── grafana_push_metrics_standalone.py
│   ├── grafana_push_metrics_gcloud.sh
│   ├── grafana_push_metrics_simple.sh
│   ├── generate_daily_reports.sh
│   └── daily_usage_cron.sh
├── dashboards/               # Grafana dashboard JSON files
│   ├── grafana_dashboard.json           # Main dashboard (models, costs)
│   └── grafana_dashboard_users.json     # User-focused dashboard
├── k8s/                      # Kubernetes manifests
│   ├── namespace.yaml
│   ├── pushgateway.yaml
│   ├── servicemonitor.yaml
│   ├── metrics-pusher-configmap.yaml
│   └── metrics-pusher-cronjob.yaml
├── helm/                     # Helm chart
│   └── vertex-claude-usage-exporter/
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
└── reports/                  # Generated CSV reports (gitignored)
```

## Scripts

- `scripts/claude_usage_report.py` - Basic usage report: counts API calls per user, outputs CSV or JSON
- `scripts/claude_usage_with_cost.py` - Extended report with cost estimation: includes model breakdown and estimated USD costs
- `scripts/grafana_push_metrics.py` - Push metrics to Prometheus Pushgateway for Grafana visualization

## Setup and Running

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Basic usage report (yesterday's data)
python scripts/claude_usage_report.py -p <GCP_PROJECT_ID>

# Usage report with cost estimation
python scripts/claude_usage_with_cost.py -p <GCP_PROJECT_ID> --avg-input-tokens 11000 --avg-output-tokens 4000

# Specify a date
python scripts/claude_usage_with_cost.py -p <GCP_PROJECT_ID> -d 2025-11-26
```

## GCP Prerequisites

- Requires `google-cloud-logging` Python library
- GCP credentials must be configured (ADC or service account)
- Data Access Audit Logs must be enabled for `aiplatform.googleapis.com` in the target project

## Architecture Notes

All scripts follow the same pattern:
1. Build a Cloud Logging filter targeting Vertex AI predict methods
2. Fetch log entries and filter for Claude/Anthropic model calls
3. Extract user email from `authenticationInfo.principalEmail`
4. Aggregate counts and generate output

The cost estimation script uses hardcoded pricing (in `PRICING` dict) and **calibrated per-model token averages** to estimate costs, as actual token counts aren't available in audit logs. The calibration is based on January 2026 GCP billing data and matches real costs within 0.01%.

## Pricing Reference (per million tokens)

| Model | Input | Output |
|-------|-------|--------|
| claude-3-5-haiku | $1.00 | $5.00 |
| claude-sonnet-4/4-5 | $3.00 | $15.00 |
| claude-opus-4-5 | $5.00 | $25.00 |
| claude-opus-4 | $15.00 | $75.00 |
| claude-opus-4-6 | $5.00 | $25.00 |
| claude-haiku-4-5 | $1.00 | $5.00 |

## Cost Estimation Calibration

The script uses **calibrated per-model token averages** derived from actual January 2026 GCP billing:

| Model | Avg Input Tokens | Avg Output Tokens |
|-------|------------------|-------------------|
| claude-opus-4-5 | 8,871 | 3,548 |
| claude-sonnet-4-5 | 4,820 | 1,928 |
| claude-sonnet-4 | 3,309 | 1,323 |
| claude-haiku-4-5 | 840 | 336 |
| claude-3-5-haiku | 382 | 153 |

By default, the script uses these calibrated values. To override with global averages:
```bash
# Use global token averages instead of per-model calibration
python scripts/grafana_push_metrics.py -p <PROJECT> --avg-input-tokens 5000 --avg-output-tokens 2000

# Disable calibration entirely (use defaults)
python scripts/grafana_push_metrics.py -p <PROJECT> --no-calibrated
```

## Grafana Dashboard Setup

### Prerequisites

1. **Prometheus Pushgateway** - Deploy via Kubernetes (see `k8s/pushgateway.yaml`)

2. **Prometheus** - Configure to scrape from Pushgateway with `honor_labels: true`

3. **Grafana** - Add Prometheus as a data source

### Pushing Metrics

```bash
# Push yesterday's metrics (default)
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> -g localhost:9091

# Push specific date with job name
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> -d 2026-01-26 \
    -g localhost:9091 -j "claude_vertex_2026-01-26"

# Dry run (shows metrics without pushing)
python scripts/grafana_push_metrics.py -p <GCP_PROJECT_ID> --dry-run
```

### Importing Dashboards

1. In Grafana, go to Dashboards > Import
2. Upload `dashboards/grafana_dashboard.json` (main) or `dashboards/grafana_dashboard_users.json` (users)
3. Select your Prometheus data source
4. Click Import

### Available Dashboards

| Dashboard | Description |
|-----------|-------------|
| `grafana_dashboard.json` | Overview: requests/cost by model, daily trends, pie charts |
| `grafana_dashboard_users.json` | User focus: per-user breakdown, **token usage charts (input/output over time)**, pivot tables by date |

### Metrics Exposed

| Metric | Description | Labels |
|--------|-------------|--------|
| `claude_vertex_requests_total` | Requests per user/model | user, model, date, project |
| `claude_vertex_estimated_cost_usd` | Cost per user/model | user, model, date, project |
| `claude_vertex_estimated_input_tokens` | Estimated input tokens | user, model, date, project |
| `claude_vertex_estimated_output_tokens` | Estimated output tokens | user, model, date, project |
| `claude_vertex_total_requests` | Total requests per model | model, date, project |
| `claude_vertex_total_cost_usd` | Total cost per model | model, date, project |
| `claude_vertex_unique_users` | Unique user count | date, project |

### Kubernetes Deployment

```bash
# Deploy pushgateway and cronjob
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pushgateway.yaml
kubectl apply -f k8s/servicemonitor.yaml
kubectl apply -f k8s/metrics-pusher-configmap.yaml
kubectl apply -f k8s/metrics-pusher-cronjob.yaml
```

### Automation with Cron

```bash
# Add to crontab to push daily at 2 AM
0 2 * * * /path/to/venv/bin/python /path/to/scripts/grafana_push_metrics.py -p PROJECT_ID -g pushgateway:9091
```

## Releases and Packages

### CI/CD Workflows

- **CI** (`.github/workflows/ci.yml`) - Runs on push/PR to `main`: lint (ruff), tests (pytest), Helm lint, Docker build
- **Release** (`.github/workflows/release.yml`) - Triggered by pushing a `v*` tag

### Release Process

1. Update the version in `helm/vertex-claude-usage-exporter/Chart.yaml` (must match the tag)
2. Commit and push to `main`
3. Tag and push:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

The release workflow will automatically:
- Verify the Helm chart version matches the tag
- Build and push a **multi-arch Docker image** (amd64 + arm64) to GHCR
- Package and push the **Helm chart as OCI artifact** to GHCR
- Create a **GitHub Release** with auto-generated release notes and the Helm `.tgz` attached

### Published Artifacts

| Artifact | Location |
|----------|----------|
| Docker image | `ghcr.io/mrmichou/vertex-claude-usage-exporter:<version>` |
| Helm chart (OCI) | `oci://ghcr.io/mrmichou/charts/vertex-claude-usage-exporter` |
| Helm chart (.tgz) | GitHub Release attachment |

### Installing from Packages

```bash
# Docker
docker pull ghcr.io/mrmichou/vertex-claude-usage-exporter:0.1.0

# Helm (OCI)
helm pull oci://ghcr.io/mrmichou/charts/vertex-claude-usage-exporter --version 0.1.0
helm install my-release oci://ghcr.io/mrmichou/charts/vertex-claude-usage-exporter
```

### Release Notes Configuration

Release notes are auto-generated from merged PRs, categorized by labels (configured in `.github/release.yml`):
- **Breaking Changes** (`breaking-change`)
- **New Features** (`enhancement`, `feature`)
- **Bug Fixes** (`bug`, `fix`)
- **Infrastructure & CI** (`ci`, `infrastructure`, `helm`, `docker`)
- **Documentation** (`documentation`)
