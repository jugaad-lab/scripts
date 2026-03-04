#!/usr/bin/env -S python3 -u
"""
API Cost Tracker — deterministic cost analysis from OpenClaw session transcripts.

Scans .jsonl transcript files for token usage/cost data.
Outputs daily totals, monthly projection, and Max plan comparison.

Exit codes:
    0 = data collected, results written
    2 = no data found
    1 = error

Usage:
    python3 cost-tracker.py [--out /tmp/cost-tracker.json] [--days 30]
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


# --- Config ---

# Where OpenClaw stores session transcripts
TRANSCRIPT_DIRS = [
    os.path.expanduser("~/.openclaw/workspace"),           # workspace transcripts
    os.path.expanduser("~/.openclaw/agents/main/sessions"), # agent session transcripts
]

# Max plan pricing for comparison
MAX_5X_MONTHLY = 100.0   # $100/mo
MAX_20X_MONTHLY = 200.0  # $200/mo

# Buffer: recommend switch only if API cost exceeds plan by this margin
SWITCH_THRESHOLD = 0.8  # recommend at 80% of plan cost (to account for rate limit value)


# --- Core ---

def find_transcripts(dirs: list[str]) -> list[str]:
    """Find all .jsonl transcript files."""
    files = []
    for d in dirs:
        if os.path.isdir(d):
            files.extend(glob.glob(os.path.join(d, "*.jsonl")))
    return files


def extract_costs(filepath: str, start_date: datetime, end_date: datetime) -> list[dict]:
    """Extract cost entries from a transcript file within date range."""
    entries = []
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Support both flat (role+usage) and nested (message.usage) formats
                inner = msg.get("message", msg)
                role = inner.get("role", msg.get("role"))

                # Only look at assistant messages with usage data
                if role != "assistant":
                    continue

                usage = inner.get("usage", msg.get("usage", {}))
                cost = usage.get("cost", {})
                timestamp = msg.get("timestamp", inner.get("timestamp"))

                if not timestamp or not cost.get("total"):
                    continue

                # Parse timestamp (ISO string or epoch ms)
                try:
                    if isinstance(timestamp, str):
                        # ISO 8601 format (e.g. "2026-02-28T15:00:04.719Z")
                        ts = timestamp.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(ts).replace(tzinfo=None)
                    elif isinstance(timestamp, (int, float)):
                        dt = datetime.fromtimestamp(timestamp / 1000.0)
                    else:
                        continue
                except (ValueError, OSError):
                    continue

                if start_date <= dt <= end_date:
                    entries.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "timestamp": dt.isoformat(),
                        "model": inner.get("model", msg.get("model", "unknown")),
                        "cost_total": cost.get("total", 0),
                        "cost_input": cost.get("input", 0),
                        "cost_output": cost.get("output", 0),
                        "cost_cache_read": cost.get("cacheRead", 0),
                        "cost_cache_write": cost.get("cacheWrite", 0),
                        "tokens_input": usage.get("input", 0),
                        "tokens_output": usage.get("output", 0),
                        "tokens_cache_read": usage.get("cacheRead", 0),
                    })
    except (IOError, PermissionError) as e:
        print(f"   ⚠️  Skipping {filepath}: {e}", file=sys.stderr)

    return entries


def analyze(entries: list[dict], days: int) -> dict:
    """Analyze cost entries and produce summary."""
    if not entries:
        return {"has_data": False}

    # Group by date
    by_date = {}
    by_model = {}
    for e in entries:
        date = e["date"]
        model = e["model"]

        by_date.setdefault(date, 0.0)
        by_date[date] += e["cost_total"]

        by_model.setdefault(model, {"cost": 0.0, "count": 0})
        by_model[model]["cost"] += e["cost_total"]
        by_model[model]["count"] += 1

    # Sort dates
    sorted_dates = sorted(by_date.keys())
    daily_costs = [{"date": d, "cost": round(by_date[d], 4)} for d in sorted_dates]

    # Calculate stats
    total_cost = sum(by_date.values())
    days_with_data = len(by_date)
    daily_avg = total_cost / days_with_data if days_with_data > 0 else 0
    monthly_projection = daily_avg * 30

    # Trend: compare last 7 days avg vs previous 7 days avg
    if len(sorted_dates) >= 14:
        recent_7 = sum(by_date[d] for d in sorted_dates[-7:]) / 7
        prev_7 = sum(by_date[d] for d in sorted_dates[-14:-7]) / 7
        trend = "increasing" if recent_7 > prev_7 * 1.1 else "decreasing" if recent_7 < prev_7 * 0.9 else "stable"
        trend_pct = ((recent_7 - prev_7) / prev_7 * 100) if prev_7 > 0 else 0
    elif len(sorted_dates) >= 7:
        recent_7 = sum(by_date[d] for d in sorted_dates[-7:]) / 7
        trend = "insufficient_data"
        trend_pct = 0
    else:
        recent_7 = daily_avg
        trend = "insufficient_data"
        trend_pct = 0

    # Max plan comparison
    if monthly_projection >= MAX_20X_MONTHLY * SWITCH_THRESHOLD:
        recommendation = "CONSIDER_MAX_20X"
    elif monthly_projection >= MAX_5X_MONTHLY * SWITCH_THRESHOLD:
        recommendation = "CONSIDER_MAX_5X"
    else:
        recommendation = "STAY_API"

    # Model breakdown
    model_breakdown = []
    for model, data in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
        model_breakdown.append({
            "model": model,
            "cost": round(data["cost"], 4),
            "calls": data["count"],
            "pct_of_total": round(data["cost"] / total_cost * 100, 1) if total_cost > 0 else 0,
        })

    return {
        "has_data": True,
        "period_days": days,
        "days_with_data": days_with_data,
        "total_cost": round(total_cost, 2),
        "daily_average": round(daily_avg, 2),
        "monthly_projection": round(monthly_projection, 2),
        "trend": trend,
        "trend_pct": round(trend_pct, 1),
        "recommendation": recommendation,
        "max_5x_savings": round(monthly_projection - MAX_5X_MONTHLY, 2) if monthly_projection > MAX_5X_MONTHLY else 0,
        "max_20x_savings": round(monthly_projection - MAX_20X_MONTHLY, 2) if monthly_projection > MAX_20X_MONTHLY else 0,
        "daily_costs": daily_costs,
        "model_breakdown": model_breakdown,
        "total_calls": len(entries),
    }


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="API Cost Tracker")
    parser.add_argument("--out", default="/tmp/cost-tracker.json", help="Output file path")
    parser.add_argument("--days", type=int, default=30, help="Number of days to analyze")
    args = parser.parse_args()

    print(f"💰 Cost Tracker — analyzing last {args.days} days")
    print(f"{'=' * 50}")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    print(f"   Period: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")

    # Find transcripts
    transcripts = find_transcripts(TRANSCRIPT_DIRS)
    print(f"   Found {len(transcripts)} transcript files")

    if not transcripts:
        print("   No transcripts found")
        return 2

    # Extract costs
    all_entries = []
    for t in transcripts:
        entries = extract_costs(t, start_date, end_date)
        all_entries.extend(entries)

    print(f"   Extracted {len(all_entries)} cost entries")

    if not all_entries:
        print("   No cost data found in date range")
        return 2

    # Analyze
    result = analyze(all_entries, args.days)

    # Print summary
    print()
    print(f"   Total cost:          ${result['total_cost']}")
    print(f"   Daily average:       ${result['daily_average']}")
    print(f"   Monthly projection:  ${result['monthly_projection']}")
    print(f"   Trend:               {result['trend']} ({result['trend_pct']:+.1f}%)")
    print(f"   Recommendation:      {result['recommendation']}")
    print()
    print(f"   Model breakdown:")
    for m in result["model_breakdown"]:
        print(f"     {m['model']}: ${m['cost']} ({m['pct_of_total']}%, {m['calls']} calls)")

    # Write output
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n📄 Data written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
