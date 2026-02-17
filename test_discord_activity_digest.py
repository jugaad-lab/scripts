#!/usr/bin/env python3
"""
Tests for discord-activity-digest.py
Run: python3 -m pytest test_discord_activity_digest.py -v
"""

import json
import os
import subprocess
import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import importlib
digest = importlib.import_module("discord-activity-digest")


# --- Fixtures ---

def make_message(msg_id: str, author: str, content: str, author_id: str = "123", mentions: list = None) -> dict:
    """Build a fake Discord message."""
    return {
        "id": msg_id,
        "content": content,
        "author": {"id": author_id, "username": author},
        "mentions": mentions or [],
        "timestamp": "2026-02-16T10:00:00Z",
    }


# --- Unit Tests ---

class TestDatetimeToSnowflake:
    """Test snowflake generation from datetime."""

    def test_produces_string(self):
        dt = datetime(2026, 2, 16, tzinfo=timezone.utc)
        result = digest.datetime_to_snowflake(dt)
        assert isinstance(result, str)
        assert int(result) > 0

    def test_later_datetime_gives_larger_snowflake(self):
        dt1 = datetime(2026, 2, 15, tzinfo=timezone.utc)
        dt2 = datetime(2026, 2, 16, tzinfo=timezone.utc)
        assert int(digest.datetime_to_snowflake(dt2)) > int(digest.datetime_to_snowflake(dt1))


class TestAnalyzeMessages:
    """Test message analysis logic."""

    def test_empty_messages(self):
        result = digest.analyze_messages([], "bot123")
        assert result["count"] == 0
        assert result["authors"] == {}
        assert result["mentions_bot"] == []

    def test_counts_messages_and_authors(self):
        messages = [
            make_message("1", "alice", "hello"),
            make_message("2", "alice", "world"),
            make_message("3", "bob", "hi"),
        ]
        result = digest.analyze_messages(messages, "bot123")
        assert result["count"] == 3
        assert result["authors"] == {"alice": 2, "bob": 1}

    def test_detects_bot_mention_via_mentions_array(self):
        messages = [
            make_message("1", "alice", "hey @bot", mentions=[{"id": "bot123"}]),
        ]
        result = digest.analyze_messages(messages, "bot123")
        assert len(result["mentions_bot"]) == 1
        assert result["mentions_bot"][0]["author"] == "alice"

    def test_detects_bot_mention_via_content(self):
        messages = [
            make_message("1", "alice", "hey <@bot123> help me"),
        ]
        result = digest.analyze_messages(messages, "bot123")
        assert len(result["mentions_bot"]) == 1

    def test_no_false_positive_mentions(self):
        messages = [
            make_message("1", "alice", "hey everyone"),
            make_message("2", "bob", "no mentions here"),
        ]
        result = digest.analyze_messages(messages, "bot123")
        assert len(result["mentions_bot"]) == 0

    def test_truncates_mention_content(self):
        long_content = "x" * 300
        messages = [
            make_message("1", "alice", long_content, mentions=[{"id": "bot123"}]),
        ]
        result = digest.analyze_messages(messages, "bot123")
        assert len(result["mentions_bot"][0]["content"]) == 200

    def test_handles_none_bot_id(self):
        messages = [
            make_message("1", "alice", "hello", mentions=[{"id": "bot123"}]),
        ]
        result = digest.analyze_messages(messages, None)
        assert len(result["mentions_bot"]) == 0  # can't detect without bot ID


class TestChannelConfig:
    """Verify channel configuration."""

    def test_channels_configured(self):
        assert len(digest.CHANNEL_NAMES) > 0

    @patch.dict(os.environ, {"DISCORD_GUILD_ID": "123456789012345678"})
    def test_reads_guild_id_from_env(self):
        guild_id = digest.get_guild_id()
        assert guild_id == "123456789012345678"
        
    def test_fails_without_guild_id_env(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as excinfo:
                digest.get_guild_id()
            assert excinfo.value.code == 1

    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test_token_123"})
    def test_reads_bot_token_from_env(self):
        token = digest.load_bot_token()
        assert token == "test_token_123"
        
    def test_fails_without_bot_token_env(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as excinfo:
                digest.load_bot_token()
            assert excinfo.value.code == 1

    def test_known_channels_present(self):
        names = digest.CHANNEL_NAMES.values()
        assert "general" in names
        assert "bot-collaboration" in names
        assert "pain-points" in names


class TestIntegration:
    """Integration tests against real Discord API.
    
    These make real API calls but only read data (no modifications).
    Skip with: pytest -m "not integration"
    """

    @pytest.mark.integration
    def test_json_output_is_valid(self):
        """Full run with --json should produce valid JSON."""
        test_env = {
            **os.environ,
            "DISCORD_BOT_TOKEN": os.environ.get("DISCORD_BOT_TOKEN", "test_token"),
            "DISCORD_GUILD_ID": os.environ.get("DISCORD_GUILD_ID", "123456789012345678"),
        }
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "discord-activity-digest.py"),
             "--hours", "1", "--json"],
            capture_output=True, text=True, timeout=30,
            env=test_env,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "total_messages" in data
        assert "total_mentions" in data
        assert "active_channels" in data
        assert "unanswered_mentions" in data
        assert "channels" in data
        assert isinstance(data["total_messages"], int)

    @pytest.mark.integration
    def test_human_output_has_summary_line(self):
        """Human-readable output should contain the JSON summary."""
        test_env = {
            **os.environ,
            "DISCORD_BOT_TOKEN": os.environ.get("DISCORD_BOT_TOKEN", "test_token"),
            "DISCORD_GUILD_ID": os.environ.get("DISCORD_GUILD_ID", "123456789012345678"),
        }
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "discord-activity-digest.py"),
             "--hours", "1"],
            capture_output=True, text=True, timeout=30,
            env=test_env,
        )
        assert result.returncode == 0
        assert "Electrons in a Box" in result.stdout
        json_lines = [l for l in result.stdout.split("\n") if l.startswith("JSON:")]
        assert len(json_lines) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
