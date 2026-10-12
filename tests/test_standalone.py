"""Tests for the standalone (ConfigMap-deployed) pusher's Pushgateway grouping."""

import sys
from unittest import mock

import pytest

import grafana_push_metrics_standalone as standalone


class TestPushUrl:
    @mock.patch.object(standalone.urllib.request, "urlopen")
    def test_daily_date_in_grouping_key(self, urlopen):
        standalone.push_to_gateway("pgw:9091", "claude_vertex_usage", "", "2026-10-11")
        req = urlopen.call_args[0][0]
        assert req.full_url == (
            "http://pgw:9091/metrics/job/claude_vertex_usage/date/2026-10-11"
        )

    @mock.patch.object(standalone.urllib.request, "urlopen")
    def test_no_date_single_group(self, urlopen):
        standalone.push_to_gateway("pgw:9091", "claude_vertex_live", "")
        req = urlopen.call_args[0][0]
        assert req.full_url == "http://pgw:9091/metrics/job/claude_vertex_live"


def _run_main(argv):
    """Run main() with one fake Claude call and return push_to_gateway's args."""
    usage_daily = {("a@ex.com", "claude-sonnet-4-6"): 1}
    usage_live = {("claude-sonnet-4-6", "09"): 1}
    with (
        mock.patch.object(sys, "argv", ["standalone", *argv]),
        mock.patch.object(standalone, "fetch_logs", return_value=[object()]),
        mock.patch.object(standalone, "aggregate_usage", return_value=usage_daily),
        mock.patch.object(
            standalone, "aggregate_usage_by_hour", return_value=usage_live
        ),
        mock.patch.object(standalone, "push_to_gateway") as push,
    ):
        try:
            standalone.main()
        except SystemExit as e:
            assert not e.code
    return push.call_args[0]


def test_daily_push_uses_date_grouping_key():
    """Regression: a fixed job without date key made each push wipe prior days."""
    args = _run_main(["-p", "proj", "-g", "pgw:9091", "--date", "2026-10-11"])
    assert args[1] == "claude_vertex_usage"
    assert args[3] == "2026-10-11"


def test_live_push_has_no_date_key():
    args = _run_main(["-p", "proj", "-g", "pgw:9091", "--live"])
    assert args[1] == standalone.LIVE_JOB_NAME
    assert args[3] is None


@pytest.mark.parametrize("live", [True, False])
def test_push_signature_has_four_args(live):
    argv = ["-p", "proj", "--live"] if live else ["-p", "proj", "--date", "2026-10-11"]
    assert len(_run_main(argv)) == 4
