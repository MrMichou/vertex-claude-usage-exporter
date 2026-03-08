# CLAUDE.md

## Project Overview

Python scripts to generate usage reports of Claude/Anthropic models on GCP Vertex AI. Queries Cloud Logging, aggregates API calls per user, and estimates costs. Metrics are pushed to Prometheus Pushgateway for Grafana dashboards.

## Project Structure

- `scripts/` - Python scripts: `claude_usage_report.py`, `claude_usage_with_cost.py`, `grafana_push_metrics.py`, `grafana_push_metrics_standalone.py`
- `dashboards/` - Grafana dashboard JSON files
- `k8s/` - Kubernetes manifests (pushgateway, cronjob, servicemonitor)
- `helm/vertex-claude-usage-exporter/` - Helm chart (version in `Chart.yaml` must match release tags)
- `.github/workflows/` - CI (`ci.yml`: ruff, pytest, helm-lint, docker build) and Release (`release.yml`: multi-arch Docker, Helm OCI, GitHub Release)
- `.github/release.yml` - Release notes categorization by PR labels

## Development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt        # ruff + pytest
PYTHONPATH=scripts pytest tests/ -v        # run tests
ruff check scripts/ tests/                 # lint
ruff format --check scripts/ tests/        # format check
```

## Architecture

All scripts follow the same pattern:
1. Build a Cloud Logging filter for Vertex AI predict methods
2. Fetch log entries, filter for Claude/Anthropic model calls
3. Extract user email from `authenticationInfo.principalEmail`
4. Aggregate counts and generate output

Key implementation details:
- `fetch_logs()` has retry logic (3 attempts, exponential backoff 2s/4s)
- Model matching: `sorted(keys, key=len, reverse=True)` for longest-key-first
- Streaming dedup: skip entries where `operation.last=True` and `operation.first` absent
- `estimate_cost()` uses independent `if` blocks (not `elif`) for default fallback
- Calibrated per-model token averages from Jan 2026 billing (override with `--no-calibrated`)

## Pricing (per million tokens)

| Model | Input | Output |
|-------|-------|--------|
| claude-3-5-haiku / claude-haiku-4-5 | $1.00 | $5.00 |
| claude-sonnet-4 / claude-sonnet-4-5 | $3.00 | $15.00 |
| claude-opus-4-5 / claude-opus-4-6 | $5.00 | $25.00 |
| claude-opus-4 | $15.00 | $75.00 |

## Releases

1. Update `version` in `helm/vertex-claude-usage-exporter/Chart.yaml`
2. `git tag v<version> && git push origin v<version>`

Publishes: Docker image to `ghcr.io/mrmichou/vertex-claude-usage-exporter`, Helm chart to `oci://ghcr.io/mrmichou/charts/vertex-claude-usage-exporter`, GitHub Release with `.tgz`.

## GCP Prerequisites

- `google-cloud-logging` library, GCP credentials (ADC or SA)
- Data Access Audit Logs enabled for `aiplatform.googleapis.com`
