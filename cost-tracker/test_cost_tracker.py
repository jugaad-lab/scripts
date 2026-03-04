#!/usr/bin/env python3
"""
Tests for cost-tracker.py
Run: python3 -m pytest test_cost_tracker.py -v
"""

import json
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import importlib
ct = importlib.import_module("cost-tracker")


class TestExtractCosts:
    """Test transcript parsing."""

    def test_extracts_assistant_costs(self, tmp_path):
        """Should extract cost data from assistant messages."""
        now = datetime.now()
        ts = int(now.timestamp() * 1000)
        transcript = tmp_path / "test.jsonl"
        transcript.write_text(json.dumps({
            "role": "assistant",
            "model": "claude-opus-4-6",
            "timestamp": ts,
            "usage": {
                "input": 1000,
                "output": 500,
                "cacheRead": 5000,
                "cost": {"input": 0.015, "output": 0.0375, "cacheRead": 0.0025, "cacheWrite": 0.001, "total": 0.056}
            }
        }) + "\n")

        entries = ct.extract_costs(str(transcript), now - timedelta(hours=1), now + timedelta(hours=1))
        assert len(entries) == 1
        assert entries[0]["cost_total"] == 0.056
        assert entries[0]["model"] == "claude-opus-4-6"

    def test_skips_user_messages(self, tmp_path):
        """Should ignore user messages."""
        now = datetime.now()
        ts = int(now.timestamp() * 1000)
        transcript = tmp_path / "test.jsonl"
        transcript.write_text(json.dumps({
            "role": "user",
            "timestamp": ts,
            "usage": {"cost": {"total": 0.05}}
        }) + "\n")

        entries = ct.extract_costs(str(transcript), now - timedelta(hours=1), now + timedelta(hours=1))
        assert len(entries) == 0

    def test_skips_out_of_range(self, tmp_path):
        """Should skip messages outside date range."""
        old = datetime.now() - timedelta(days=60)
        ts = int(old.timestamp() * 1000)
        transcript = tmp_path / "test.jsonl"
        transcript.write_text(json.dumps({
            "role": "assistant",
            "model": "test",
            "timestamp": ts,
            "usage": {"input": 100, "output": 50, "cost": {"total": 0.01}}
        }) + "\n")

        now = datetime.now()
        entries = ct.extract_costs(str(transcript), now - timedelta(days=30), now)
        assert len(entries) == 0

    def test_handles_malformed_lines(self, tmp_path):
        """Should gracefully skip bad JSON lines."""
        now = datetime.now()
        ts = int(now.timestamp() * 1000)
        transcript = tmp_path / "test.jsonl"
        transcript.write_text(
            "not json\n"
            + json.dumps({
                "role": "assistant", "model": "test", "timestamp": ts,
                "usage": {"input": 100, "output": 50, "cost": {"total": 0.01}}
            }) + "\n"
            + "\n"
        )

        entries = ct.extract_costs(str(transcript), now - timedelta(hours=1), now + timedelta(hours=1))
        assert len(entries) == 1


class TestAnalyze:
    """Test cost analysis logic."""

    def test_empty_entries(self):
        result = ct.analyze([], 30)
        assert result["has_data"] is False

    def test_basic_analysis(self):
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [
            {"date": today, "model": "claude-opus-4-6", "cost_total": 0.50,
             "cost_input": 0.1, "cost_output": 0.3, "cost_cache_read": 0.05, "cost_cache_write": 0.05,
             "tokens_input": 1000, "tokens_output": 500, "tokens_cache_read": 5000, "timestamp": ""},
            {"date": today, "model": "claude-sonnet-4-20250514", "cost_total": 0.10,
             "cost_input": 0.05, "cost_output": 0.05, "cost_cache_read": 0, "cost_cache_write": 0,
             "tokens_input": 500, "tokens_output": 200, "tokens_cache_read": 0, "timestamp": ""},
        ]
        result = ct.analyze(entries, 30)
        assert result["has_data"] is True
        assert result["total_cost"] == 0.60
        assert result["total_calls"] == 2
        assert len(result["model_breakdown"]) == 2

    def test_recommendation_stay_api(self):
        """Low spend should recommend staying on API."""
        entries = [
            {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
             "model": "test", "cost_total": 1.0,
             "cost_input": 0.5, "cost_output": 0.5, "cost_cache_read": 0, "cost_cache_write": 0,
             "tokens_input": 1000, "tokens_output": 500, "tokens_cache_read": 0, "timestamp": ""}
            for i in range(30)
        ]
        result = ct.analyze(entries, 30)
        # $1/day = $30/mo → on Max 20x, should recommend downgrading to API
        assert result["recommendation"] == "DOWNGRADE_TO_API"
        assert result["current_plan"] == "MAX_20X"

    def test_recommendation_downgrade_to_max_5x(self):
        """$3-4/day should recommend downgrading to Max 5x (from Max 20x)."""
        entries = [
            {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
             "model": "test", "cost_total": 3.5,
             "cost_input": 1.5, "cost_output": 2.0, "cost_cache_read": 0, "cost_cache_write": 0,
             "tokens_input": 1000, "tokens_output": 500, "tokens_cache_read": 0, "timestamp": ""}
            for i in range(30)
        ]
        result = ct.analyze(entries, 30)
        # $3.5/day = $105/mo → below 70% of Max 20x ($140), suggest downgrade to Max 5x
        assert result["recommendation"] == "DOWNGRADE_TO_MAX_5X"

    def test_recommendation_stay_max_20x(self):
        """$7+/day should stay on Max 20x."""
        entries = [
            {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
             "model": "test", "cost_total": 7.0,
             "cost_input": 3.0, "cost_output": 4.0, "cost_cache_read": 0, "cost_cache_write": 0,
             "tokens_input": 1000, "tokens_output": 500, "tokens_cache_read": 0, "timestamp": ""}
            for i in range(30)
        ]
        result = ct.analyze(entries, 30)
        # $7/day = $210/mo → above threshold, stay on Max 20x
        assert result["recommendation"] == "STAY_MAX_20X"

    def test_model_breakdown_sorted_by_cost(self):
        """Model breakdown should be sorted by cost descending."""
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [
            {"date": today, "model": "cheap", "cost_total": 0.01,
             "cost_input": 0, "cost_output": 0, "cost_cache_read": 0, "cost_cache_write": 0,
             "tokens_input": 0, "tokens_output": 0, "tokens_cache_read": 0, "timestamp": ""},
            {"date": today, "model": "expensive", "cost_total": 1.00,
             "cost_input": 0, "cost_output": 0, "cost_cache_read": 0, "cost_cache_write": 0,
             "tokens_input": 0, "tokens_output": 0, "tokens_cache_read": 0, "timestamp": ""},
        ]
        result = ct.analyze(entries, 30)
        assert result["model_breakdown"][0]["model"] == "expensive"

    def test_trend_increasing(self):
        """Should detect increasing trend."""
        entries = []
        for i in range(14):
            cost = 1.0 if i < 7 else 3.0  # first week $1, second week $3
            entries.append({
                "date": (datetime.now() - timedelta(days=13 - i)).strftime("%Y-%m-%d"),
                "model": "test", "cost_total": cost,
                "cost_input": 0, "cost_output": 0, "cost_cache_read": 0, "cost_cache_write": 0,
                "tokens_input": 0, "tokens_output": 0, "tokens_cache_read": 0, "timestamp": ""
            })
        result = ct.analyze(entries, 14)
        assert result["trend"] == "increasing"


class TestFindTranscripts:
    """Test transcript discovery."""

    def test_finds_jsonl_files(self, tmp_path):
        (tmp_path / "session1.jsonl").write_text("{}\n")
        (tmp_path / "session2.jsonl").write_text("{}\n")
        (tmp_path / "not-a-transcript.txt").write_text("nope\n")

        files = ct.find_transcripts([str(tmp_path)])
        assert len(files) == 2

    def test_handles_missing_dir(self):
        files = ct.find_transcripts(["/nonexistent/path"])
        assert len(files) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
