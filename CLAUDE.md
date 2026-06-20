# CLAUDE.md

## Project Overview

Python scripts to generate usage reports of Claude/Anthropic models on GCP Vertex AI. Queries Cloud Logging, aggregates API calls per user, and estimates costs. Metrics are pushed to Prometheus Pushgateway for Grafana dashboards.

## Project Structure

- `scripts/` - Python scripts: `claude_usage_report.py`, `claude_usage_with_cost.py`, `grafana_push_metrics.py`, `grafana_push_metrics_standalone.py`
- `dashboards/` - Grafana dashboard JSON files (incl. `grafana_dashboard_live.json` for intra-day/live)
- `k8s/` - Kubernetes manifests (pushgateway, cronjob, live cronjob, servicemonitor)
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
- Calibrated per-model token averages from Jun 2026 billing, incl. cache tokens (override with `--no-calibrated`)

## Live (intra-day) metrics

Two layers, kept strictly separate (distinct metric names + Pushgateway jobs):
- **Daily batch** (`claude_vertex_*`, labels `user/model/date/project`): nightly CronJob
  `claude-metrics-pusher` (02:00 UTC), re-pushes the trailing 3 days. Canonical source.
- **Live** (`claude_vertex_live_*`, labels `model/hour/date/project`): hourly CronJob
  `claude-metrics-pusher-live` (`k8s/metrics-pusher-live-cronjob.yaml`) runs
  `grafana_push_metrics.py --live --date <today>`. Each run re-queries the current day
  (00:00 UTC → now via the same `build_filter`), buckets by hour
  (`aggregate_usage_by_hour`, model+hour only, no `user`), and re-pushes every
  hour-so-far under the fixed job `claude_vertex_live` — idempotent, self-healing, rotates
  to the new day on the first post-midnight run. Dashboard: `dashboards/grafana_dashboard_live.json`
  (refresh 1m, bar charts by hour, no `$date` variable).

## Pricing (per million tokens)

| Model | Input | Output | Cache write (1.25x) | Cache read (0.1x) |
|-------|-------|--------|---------------------|-------------------|
| claude-3-5-haiku / claude-haiku-4-5 | $1.00 | $5.00 | $1.25 | $0.10 |
| claude-sonnet-4 / claude-sonnet-4-5 / claude-sonnet-4-6 | $3.00 | $15.00 | $3.75 | $0.30 |
| claude-opus-4-5 / claude-opus-4-6 / claude-opus-4-7 / claude-opus-4-8 | $5.00 | $25.00 | $6.25 | $0.50 |
| claude-fable-5 | $10.00 | $50.00 | $12.50 | $1.00 |
| claude-opus-4 | $15.00 | $75.00 | $18.75 | $1.50 |

Prompt caching: cache token averages calibrated from June 2026 billing (cache SKU
breakdown). Calibrated averages absorb the account's billed-vs-list factor (CAD +
premium) so estimates match the invoice. Override per run with
`--avg-cache-write-tokens` / `--avg-cache-read-tokens`.

## Releases

1. Update `version` in `helm/vertex-claude-usage-exporter/Chart.yaml`
2. `git tag v<version> && git push origin v<version>`

Publishes: Docker image to `ghcr.io/mrmichou/vertex-claude-usage-exporter`, Helm chart to `oci://ghcr.io/mrmichou/charts/vertex-claude-usage-exporter`, GitHub Release with `.tgz`.

## GCP Prerequisites

- `google-cloud-logging` library, GCP credentials (ADC or SA)
- Data Access Audit Logs enabled for `aiplatform.googleapis.com`
