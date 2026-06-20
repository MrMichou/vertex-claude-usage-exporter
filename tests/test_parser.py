"""Tests for log entry parsing."""

from unittest.mock import MagicMock

from vertex_claude_exporter.parser import (
    aggregate_usage,
    aggregate_usage_by_hour,
    extract_hour,
    extract_model_name,
    parse_entry,
)


class TestExtractModelName:
    def test_standard_model(self):
        assert (
            extract_model_name(
                "projects/my-project/locations/us-east5/publishers/anthropic/models/claude-sonnet-4-5"
            )
            == "claude-sonnet-4-5"
        )

    def test_model_with_version(self):
        assert (
            extract_model_name(
                "projects/p/locations/l/publishers/anthropic/models/claude-haiku-4-5@20260101"
            )
            == "claude-haiku-4-5"
        )

    def test_count_tokens(self):
        assert (
            extract_model_name(
                "projects/p/locations/l/publishers/anthropic/models/count-tokens"
            )
            == "count-tokens"
        )

    def test_no_model(self):
        assert extract_model_name("projects/p/locations/l") == "unknown"

    def test_empty_string(self):
        assert extract_model_name("") == "unknown"


def _make_entry(
    resource_name, email="user@example.com", operation=None, timestamp=None
):
    """Helper to create a mock log entry."""
    api_repr = {
        "protoPayload": {
            "authenticationInfo": {"principalEmail": email},
            "resourceName": resource_name,
        },
    }
    if operation is not None:
        api_repr["operation"] = operation
    if timestamp is not None:
        api_repr["timestamp"] = timestamp
    entry = MagicMock()
    entry.to_api_repr.return_value = api_repr
    return entry


class TestParseEntry:
    def test_claude_model(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4-5",
            email="alice@example.com",
            timestamp="2026-06-20T14:35:12.123Z",
        )
        result = parse_entry(entry)
        assert result == {
            "email": "alice@example.com",
            "model": "claude-sonnet-4-5",
            "hour": "14",
        }

    def test_missing_timestamp_yields_unknown_hour(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4-5",
        )
        assert parse_entry(entry)["hour"] == "unknown"

    def test_non_claude_model_returns_none(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/google/models/gemini-pro"
        )
        assert parse_entry(entry) is None

    def test_streaming_dedup_skips_last_only(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
            operation={"last": True},
        )
        assert parse_entry(entry) is None

    def test_streaming_keeps_first_and_last(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
            operation={"first": True, "last": True},
        )
        result = parse_entry(entry)
        assert result is not None
        assert result["model"] == "claude-sonnet-4"

    def test_streaming_keeps_first_only(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
            operation={"first": True},
        )
        assert parse_entry(entry) is not None

    def test_count_tokens_is_filtered_out(self):
        """count-tokens is a utility endpoint, not an actual model call."""
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/count-tokens"
        )
        assert parse_entry(entry) is None

    def test_broken_entry_returns_none(self):
        entry = MagicMock()
        entry.to_api_repr.side_effect = Exception("broken")
        assert parse_entry(entry) is None


class TestAggregateUsage:
    def test_basic_aggregation(self):
        entries = [
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                email="alice@ex.com",
            ),
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                email="alice@ex.com",
            ),
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-haiku-4-5",
                email="bob@ex.com",
            ),
        ]
        usage = aggregate_usage(entries)
        assert usage == {
            ("alice@ex.com", "claude-sonnet-4"): 2,
            ("bob@ex.com", "claude-haiku-4-5"): 1,
        }

    def test_skips_non_claude(self):
        entries = [
            _make_entry(
                "projects/p/locations/l/publishers/google/models/gemini-pro",
                email="alice@ex.com",
            ),
        ]
        usage = aggregate_usage(entries)
        assert usage == {}

    def test_empty_entries(self):
        assert aggregate_usage([]) == {}


class TestExtractHour:
    def test_utc_timestamp_zero_padded(self):
        assert extract_hour({"timestamp": "2026-06-20T09:05:00Z"}) == "09"

    def test_afternoon_hour(self):
        assert extract_hour({"timestamp": "2026-06-20T23:59:59.999Z"}) == "23"

    def test_offset_timestamp_converted_to_utc(self):
        # 01:30 at +02:00 is 23:30 UTC the previous day
        assert extract_hour({"timestamp": "2026-06-20T01:30:00+02:00"}) == "23"

    def test_falls_back_to_receive_timestamp(self):
        assert extract_hour({"receiveTimestamp": "2026-06-20T07:00:00Z"}) == "07"

    def test_missing_timestamp(self):
        assert extract_hour({}) == "unknown"

    def test_unparseable_timestamp(self):
        assert extract_hour({"timestamp": "not-a-date"}) == "unknown"


class TestAggregateUsageByHour:
    def test_buckets_by_model_and_hour(self):
        entries = [
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                timestamp="2026-06-20T09:10:00Z",
            ),
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                timestamp="2026-06-20T09:45:00Z",
            ),
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                timestamp="2026-06-20T10:05:00Z",
            ),
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-haiku-4-5",
                timestamp="2026-06-20T09:30:00Z",
            ),
        ]
        usage = aggregate_usage_by_hour(entries)
        assert usage == {
            ("claude-sonnet-4", "09"): 2,
            ("claude-sonnet-4", "10"): 1,
            ("claude-haiku-4-5", "09"): 1,
        }

    def test_ignores_user_dimension(self):
        entries = [
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                email="alice@ex.com",
                timestamp="2026-06-20T09:10:00Z",
            ),
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                email="bob@ex.com",
                timestamp="2026-06-20T09:55:00Z",
            ),
        ]
        usage = aggregate_usage_by_hour(entries)
        assert usage == {("claude-sonnet-4", "09"): 2}

    def test_skips_non_claude(self):
        entries = [
            _make_entry(
                "projects/p/locations/l/publishers/google/models/gemini-pro",
                timestamp="2026-06-20T09:10:00Z",
            ),
        ]
        assert aggregate_usage_by_hour(entries) == {}

    def test_streaming_dedup_applies(self):
        entries = [
            _make_entry(
                "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4",
                operation={"last": True},
                timestamp="2026-06-20T09:10:00Z",
            ),
        ]
        assert aggregate_usage_by_hour(entries) == {}

    def test_empty_entries(self):
        assert aggregate_usage_by_hour([]) == {}
