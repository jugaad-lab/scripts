#!/usr/bin/env python3
"""
Tests for gmail-promo-cleanup.py
Run: python3 -m pytest test_gmail_promo_cleanup.py -v
"""

import json
import subprocess
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# Import the module under test
sys.path.insert(0, os.path.dirname(__file__))
import importlib
promo = importlib.import_module("gmail-promo-cleanup")


# --- Fixtures ---

def make_search_result(msg_ids: list[str], has_more: bool = False) -> str:
    """Build a fake gog gmail messages search JSON response."""
    messages = [{"id": mid, "threadId": mid, "date": "2026-02-16", "from": "spam@example.com", "subject": "Buy stuff"} for mid in msg_ids]
    result = {"messages": messages}
    if has_more:
        result["nextPageToken"] = "fake_token"
    return json.dumps(result)


def make_empty_result() -> str:
    return json.dumps({"messages": []})


def make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- Unit Tests ---

class TestGetPromoIds:
    """Test message ID extraction from search results."""

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_returns_ids_from_valid_response(self, mock_run):
        mock_run.return_value = make_completed_process(
            stdout=make_search_result(["msg1", "msg2", "msg3"])
        )
        ids = promo.get_promo_ids("test@gmail.com")
        assert ids == ["msg1", "msg2", "msg3"]

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_returns_empty_on_no_messages(self, mock_run):
        mock_run.return_value = make_completed_process(stdout=make_empty_result())
        ids = promo.get_promo_ids("test@gmail.com")
        assert ids == []

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_returns_empty_on_search_failure(self, mock_run):
        mock_run.return_value = make_completed_process(returncode=1, stderr="auth error")
        ids = promo.get_promo_ids("test@gmail.com")
        assert ids == []

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = make_completed_process(stdout="not json at all")
        ids = promo.get_promo_ids("test@gmail.com")
        assert ids == []

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_returns_empty_on_empty_stdout(self, mock_run):
        mock_run.return_value = make_completed_process(stdout="")
        ids = promo.get_promo_ids("test@gmail.com")
        assert ids == []

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_handles_missing_id_field(self, mock_run):
        """Messages without 'id' field should be skipped."""
        mock_run.return_value = make_completed_process(
            stdout=json.dumps({"messages": [{"id": "good"}, {"threadId": "no_id_field"}]})
        )
        ids = promo.get_promo_ids("test@gmail.com")
        assert ids == ["good"]


class TestTrashMessages:
    """Test batch trash operation."""

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_trashes_messages(self, mock_run):
        mock_run.return_value = make_completed_process()
        count = promo.trash_messages("test@gmail.com", ["msg1", "msg2"])
        assert count == 2
        mock_run.assert_called_once()
        # Verify the command includes message IDs
        call_args = mock_run.call_args[0][0]
        assert "msg1" in call_args
        assert "msg2" in call_args

    def test_returns_zero_on_empty_list(self):
        """Should not call subprocess if no IDs provided."""
        count = promo.trash_messages("test@gmail.com", [])
        assert count == 0

    @patch("subprocess.run")
    def test_dry_run_does_not_call_subprocess(self, mock_run):
        count = promo.trash_messages("test@gmail.com", ["msg1"], dry_run=True)
        assert count == 1
        mock_run.assert_not_called()

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_returns_zero_on_failure(self, mock_run):
        mock_run.return_value = make_completed_process(returncode=1, stderr="batch failed")
        count = promo.trash_messages("test@gmail.com", ["msg1"])
        assert count == 0


class TestCleanupAccount:
    """Test per-account cleanup flow."""

    @patch.object(promo, "trash_messages", return_value=5)
    @patch.object(promo, "get_promo_ids")
    def test_single_page(self, mock_get, mock_trash):
        """When fewer than MAX_PER_PAGE results, should stop after one page."""
        mock_get.return_value = ["m1", "m2", "m3", "m4", "m5"]
        total = promo.cleanup_account("test@gmail.com")
        assert total == 5
        assert mock_get.call_count == 1
        mock_trash.assert_called_once()

    @patch.object(promo, "trash_messages", return_value=100)
    @patch.object(promo, "get_promo_ids")
    def test_paginates_when_full_page(self, mock_get, mock_trash):
        """When exactly MAX_PER_PAGE results, should fetch next page."""
        # First call: full page, second call: partial, triggers stop
        mock_get.side_effect = [
            [f"m{i}" for i in range(100)],
            [f"m{i}" for i in range(50)],
        ]
        mock_trash.side_effect = [100, 50]
        total = promo.cleanup_account("test@gmail.com")
        assert total == 150
        assert mock_get.call_count == 2

    @patch.object(promo, "trash_messages")
    @patch.object(promo, "get_promo_ids")
    def test_respects_max_pages_cap(self, mock_get, mock_trash):
        """Should stop after MAX_PAGES even if more exist."""
        mock_get.return_value = [f"m{i}" for i in range(100)]
        mock_trash.return_value = 100
        total = promo.cleanup_account("test@gmail.com")
        assert total == 300  # MAX_PAGES=3 * 100
        assert mock_get.call_count == 3

    @patch.object(promo, "get_promo_ids", return_value=[])
    def test_clean_inbox(self, mock_get):
        """No promos → zero trashed, no trash call."""
        total = promo.cleanup_account("test@gmail.com")
        assert total == 0


class TestAccounts:
    """Verify account configuration."""

    @patch.dict(os.environ, {"GMAIL_ACCOUNTS": "test1@gmail.com,test2@gmail.com,test3@gmail.com"})
    def test_reads_accounts_from_env(self):
        accounts = promo.get_accounts()
        assert len(accounts) == 3
        assert "test1@gmail.com" in accounts
        assert "test2@gmail.com" in accounts
        assert "test3@gmail.com" in accounts
        
    def test_fails_without_accounts_env(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as excinfo:
                promo.get_accounts()
            assert excinfo.value.code == 1


class TestRunGog:
    """Test the gog command wrapper."""

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "test123"})
    @patch("subprocess.run")
    def test_passes_account_and_flags(self, mock_run):
        mock_run.return_value = make_completed_process()
        promo.run_gog(["gmail", "search", "test"], "test@gmail.com")
        call_args = mock_run.call_args[0][0]
        assert "gog" == call_args[0]
        assert "--account" in call_args
        assert "test@gmail.com" in call_args
        assert "--force" in call_args
        assert "--no-input" in call_args

    @patch.dict(os.environ, {"GOG_KEYRING_PASSWORD": "testpass123"})
    @patch("subprocess.run")
    def test_sets_keyring_password(self, mock_run):
        mock_run.return_value = make_completed_process()
        promo.run_gog(["gmail", "search", "test"], "test@gmail.com")
        env = mock_run.call_args[1]["env"]
        assert "GOG_KEYRING_PASSWORD" in env
        assert env["GOG_KEYRING_PASSWORD"] == "testpass123"
        
    def test_fails_without_gog_password(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as excinfo:
                promo.run_gog(["gmail", "search", "test"], "test@gmail.com")
            assert excinfo.value.code == 1


class TestIntegration:
    """Integration tests using --dry-run against real accounts.
    
    These tests verify the script runs end-to-end without errors.
    They DO make real API calls (search only, no modifications).
    Skip with: pytest -m "not integration"
    """

    @pytest.mark.integration
    def test_dry_run_exits_cleanly(self):
        """Full dry-run should exit 0 and produce valid JSON output."""
        test_env = {
            **os.environ,
            "GMAIL_ACCOUNTS": "test@example.com",
            "GOG_KEYRING_PASSWORD": os.environ.get("GOG_KEYRING_PASSWORD", "test123"),
        }
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "gmail-promo-cleanup.py"), "--dry-run"],
            capture_output=True, text=True, timeout=120,
            env=test_env,
        )
        assert result.returncode == 0
        # Should contain JSON summary line
        lines = result.stdout.strip().split("\n")
        json_line = [l for l in lines if l.startswith("JSON:")]
        assert len(json_line) == 1
        data = json.loads(json_line[0].replace("JSON: ", ""))
        assert "total" in data
        assert "accounts" in data
        assert isinstance(data["total"], int)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
