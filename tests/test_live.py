"""Tests for live-mode behaviour of grafana_push_metrics.main()."""

import sys
from unittest import mock

import pytest

import grafana_push_metrics


def _run_main(argv):
    with mock.patch.object(sys, "argv", ["grafana_push_metrics.py", *argv]):
        with pytest.raises(SystemExit) as exc:
            grafana_push_metrics.main()
    return exc.value.code


@mock.patch.object(grafana_push_metrics, "clear_live_metrics")
@mock.patch.object(grafana_push_metrics, "fetch_logs", return_value=[])
def test_live_without_usage_clears_group(_fetch, clear):
    assert _run_main(["-p", "proj", "-g", "pgw:9091", "--live"]) == 0
    clear.assert_called_once_with("pgw:9091")


@mock.patch.object(grafana_push_metrics, "clear_live_metrics")
@mock.patch.object(grafana_push_metrics, "fetch_logs", return_value=[])
def test_live_dry_run_without_usage_does_not_clear(_fetch, clear):
    assert _run_main(["-p", "proj", "--live", "--dry-run"]) == 0
    clear.assert_not_called()


@mock.patch.object(grafana_push_metrics, "clear_live_metrics")
@mock.patch.object(grafana_push_metrics, "fetch_logs", return_value=[])
def test_daily_without_usage_does_not_clear(_fetch, clear):
    assert _run_main(["-p", "proj", "--date", "2026-06-01"]) == 0
    clear.assert_not_called()


@mock.patch.object(
    grafana_push_metrics, "clear_live_metrics", side_effect=OSError("down")
)
@mock.patch.object(grafana_push_metrics, "fetch_logs", return_value=[])
def test_live_clear_failure_exits_1(_fetch, _clear):
    assert _run_main(["-p", "proj", "--live"]) == 1
