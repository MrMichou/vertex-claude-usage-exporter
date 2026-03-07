"""Tests for log filtering."""

from datetime import datetime, timezone, timedelta

from vertex_claude_exporter.logs import build_filter


class TestBuildFilter:
    def test_basic_filter(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        f = build_filter(dt)
        assert 'timestamp >= "2026-01-15T00:00:00Z"' in f
        assert 'timestamp < "2026-01-16T00:00:00Z"' in f
        assert "aiplatform.googleapis.com" in f

    def test_ignores_time_component(self):
        dt = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        f = build_filter(dt)
        assert 'timestamp >= "2026-03-01T00:00:00Z"' in f
        assert 'timestamp < "2026-03-02T00:00:00Z"' in f

    def test_naive_datetime(self):
        dt = datetime(2026, 6, 15)
        f = build_filter(dt)
        assert 'timestamp >= "2026-06-15T00:00:00Z"' in f

    def test_non_utc_timezone_converts(self):
        """A date in UTC+5 should still produce UTC timestamps."""
        tz_plus5 = timezone(timedelta(hours=5))
        dt = datetime(2026, 1, 15, 3, 0, 0, tzinfo=tz_plus5)
        f = build_filter(dt)
        # 2026-01-15 03:00 UTC+5 = 2026-01-14 22:00 UTC -> midnight = 2026-01-14
        assert 'timestamp >= "2026-01-14T00:00:00Z"' in f
        assert 'timestamp < "2026-01-15T00:00:00Z"' in f

    def test_filter_contains_predict_methods(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        f = build_filter(dt)
        assert "rawPredict" in f
        assert "streamRawPredict" in f
        assert "Predict" in f
