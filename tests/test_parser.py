"""Tests for log entry parsing."""

from unittest.mock import MagicMock

from vertex_claude_exporter.parser import (
    aggregate_usage,
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


def _make_entry(resource_name, email="user@example.com", operation=None):
    """Helper to create a mock log entry."""
    api_repr = {
        "protoPayload": {
            "authenticationInfo": {"principalEmail": email},
            "resourceName": resource_name,
        },
    }
    if operation is not None:
        api_repr["operation"] = operation
    entry = MagicMock()
    entry.to_api_repr.return_value = api_repr
    return entry


class TestParseEntry:
    def test_claude_model(self):
        entry = _make_entry(
            "projects/p/locations/l/publishers/anthropic/models/claude-sonnet-4-5",
            email="alice@example.com",
        )
        result = parse_entry(entry)
        assert result == {"email": "alice@example.com", "model": "claude-sonnet-4-5"}

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
