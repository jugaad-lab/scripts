#!/usr/bin/env python3
"""
Tests for morning-orchestrator.py
Run: python3 -m pytest test_morning_orchestrator.py -v
"""

import json
import os
import subprocess
import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import importlib
orch = importlib.import_module("morning-orchestrator")


# --- Actionability Tests ---

class TestIsActionable:
    """Test the actionability decision logic."""

    def test_important_emails_trigger(self):
        data = {
            "emails": {"important_count": 3, "noise_count": 0},
            "calendar": {"today_count": 0, "tomorrow_count": 0},
            "discord": {"unanswered_mentions": []},
        }
        actionable, reasons = orch.is_actionable(data)
        assert actionable
        assert any("important emails" in r for r in reasons)

    def test_calendar_events_trigger(self):
        data = {
            "emails": {"important_count": 0, "noise_count": 0},
            "calendar": {"today_count": 2, "tomorrow_count": 0},
            "discord": {"unanswered_mentions": []},
        }
        actionable, reasons = orch.is_actionable(data)
        assert actionable
        assert any("calendar" in r for r in reasons)

    def test_unanswered_mentions_trigger(self):
        data = {
            "emails": {"important_count": 0, "noise_count": 0},
            "calendar": {"today_count": 0, "tomorrow_count": 0},
            "discord": {"unanswered_mentions": [{"author": "nag", "content": "hey"}]},
        }
        actionable, reasons = orch.is_actionable(data)
        assert actionable
        assert any("Discord" in r for r in reasons)

    def test_weekend_with_no_activity_not_actionable(self):
        """On weekends with nothing happening, should NOT be actionable."""
        import types
        original_now = datetime.now

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, *args, **kwargs):
                # Return a Sunday
                return datetime(2026, 2, 22)  # Feb 22, 2026 is a Sunday

        data = {
            "emails": {"important_count": 0, "noise_count": 0},
            "calendar": {"today_count": 0, "tomorrow_count": 0},
            "discord": {"unanswered_mentions": []},
        }

        # Temporarily patch datetime in the module
        old_datetime = orch.datetime
        orch.datetime = FakeDatetime
        try:
            actionable, reasons = orch.is_actionable(data)
            assert not actionable
            assert reasons == []
        finally:
            orch.datetime = old_datetime

    def test_weekday_always_actionable(self):
        """Weekdays should always trigger (work priorities)."""
        data = {
            "emails": {"important_count": 0, "noise_count": 0},
            "calendar": {"today_count": 0, "tomorrow_count": 0},
            "discord": {"unanswered_mentions": []},
        }
        # This test depends on the current day — if run on a weekday, it should pass
        day = datetime.now().weekday()
        actionable, reasons = orch.is_actionable(data)
        if day < 5:  # weekday
            assert actionable
            assert any("weekday" in r for r in reasons)

    def test_multiple_reasons_accumulated(self):
        data = {
            "emails": {"important_count": 5, "noise_count": 0},
            "calendar": {"today_count": 3, "tomorrow_count": 0},
            "discord": {"unanswered_mentions": [{"x": 1}]},
        }
        actionable, reasons = orch.is_actionable(data)
        assert actionable
        assert len(reasons) >= 3  # emails + calendar + mentions (+ possibly weekday)


class TestImportantSenders:
    """Verify sender classification config."""

    def test_default_senders_not_empty(self):
        senders = orch.get_important_senders()
        assert len(senders) > 0

    def test_financial_senders_included(self):
        senders = orch.get_important_senders()
        senders_str = " ".join(senders)
        assert "chase" in senders_str
        assert "google security" in senders_str

    @patch.dict(os.environ, {"IMPORTANT_SENDERS": "custom1@example.com,custom2@example.com"})
    def test_custom_senders_added(self):
        senders = orch.get_important_senders()
        assert "custom1@example.com" in senders
        assert "custom2@example.com" in senders


class TestNoiseCategories:
    """Verify noise classification."""

    def test_promos_are_noise(self):
        assert "CATEGORY_PROMOTIONS" in orch.NOISE_CATEGORIES

    def test_social_is_noise(self):
        assert "CATEGORY_SOCIAL" in orch.NOISE_CATEGORIES

    def test_forums_are_noise(self):
        assert "CATEGORY_FORUMS" in orch.NOISE_CATEGORIES

    def test_primary_not_noise(self):
        assert "CATEGORY_PERSONAL" not in orch.NOISE_CATEGORIES
        assert "CATEGORY_PRIMARY" not in orch.NOISE_CATEGORIES


class TestAccounts:
    """Verify account config."""

    @patch.dict(os.environ, {"GMAIL_ACCOUNTS": "test1@gmail.com,test2@gmail.com,test3@gmail.com"})
    def test_reads_accounts_from_env(self):
        accounts = orch.get_accounts()
        assert len(accounts) == 3
        assert "test1@gmail.com" in accounts

    @patch.dict(os.environ, {"CALENDAR_ACCOUNT": "calendar@example.com"})
    def test_reads_calendar_account_from_env(self):
        account = orch.get_calendar_account()
        assert account == "calendar@example.com"
        
    def test_fails_without_accounts_env(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit):
                orch.get_accounts()
                
    def test_fails_without_calendar_env(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit):
                orch.get_calendar_account()


class TestIntegration:
    """Integration test — full dry-run."""

    @pytest.mark.integration
    def test_dry_run_exits_cleanly(self):
        test_env = {
            **os.environ,
            "GMAIL_ACCOUNTS": "test@example.com",
            "CALENDAR_ACCOUNT": "test@example.com",
            "GOG_KEYRING_PASSWORD": os.environ.get("GOG_KEYRING_PASSWORD", "test123"),
            "DISCORD_BOT_TOKEN": os.environ.get("DISCORD_BOT_TOKEN", "test_token"),
            "DISCORD_GUILD_ID": os.environ.get("DISCORD_GUILD_ID", "123456789012345678"),
        }
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "morning-orchestrator.py"), "--dry-run"],
            capture_output=True, text=True, timeout=180,
            env=test_env,
        )
        # Should exit 0 (actionable) or 2 (all clear) — not 1 (error)
        assert result.returncode in (0, 2), f"Unexpected exit code {result.returncode}: {result.stderr}"
        assert "Morning Orchestrator" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
