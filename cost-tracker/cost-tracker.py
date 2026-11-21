#!/usr/bin/env -S python3 -u
"""
API Cost Tracker — deterministic cost analysis from Claude Code session transcripts.

Scans .jsonl transcripts under ~/.claude/projects, computes spend from per-message
token usage (Claude Code does NOT write a pre-computed `cost` block — we price
tokens ourselves using current Anthropic rates), and projects monthly spend.

Outputs daily totals, monthly projection, and a Max plan vs API recommendation.

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

# Claude Code stores one jsonl per session under ~/.claude/projects/<slug>/.
TRANSCRIPT_DIRS = [
    os.path.expanduser("~/.claude/projects"),
]

# Anthropic token prices ($ per 1M tokens). Keys are substrings matched against
# the model string on the assistant message. Order matters — most specific first.
# Sources: Anthropic pricing page (input/output), 1h cache creation = input × 1.25,
# cache read = input × 0.1.
TOKEN_PRICES: list[tuple[str, dict]] = [
    ("opus-4",   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50}),
    ("sonnet-4", {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30}),
    ("haiku-4",  {"input":  1.00, "output":  5.00, "cache_write":  1.25, "cache_read": 0.10}),
    ("opus-3",   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50}),
    ("sonnet-3", {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30}),
    ("haiku-3",  {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08}),
]
DEFAULT_PRICE = {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50}

# Max plan pricing. Max 10x is an unofficial tier some users have access to;
# default to $150/mo — adjust if Anthropic publishes a different number.
MAX_5X_MONTHLY  = 100.0
MAX_10X_MONTHLY = 150.0
MAX_20X_MONTHLY = 200.0

# Current plan — change this if you switch plans
CURRENT_PLAN = "MAX_20X"  # Options: "API", "MAX_5X", "MAX_10X", "MAX_20X"

# Downgrade buffer: only recommend downgrade if projected cost is below
# this fraction of the next-cheaper plan. Protects against noisy projections.
DOWNGRADE_MARGIN = 0.3


# --- Core ---

def price_for_model(model: str) -> dict:
    if not model:
        return DEFAULT_PRICE
    m = model.lower()
    for key, price in TOKEN_PRICES:
        if key in m:
            return price
    return DEFAULT_PRICE


def compute_cost(usage: dict, model: str) -> dict:
    """Compute $ cost from a Claude Code usage block.

    Claude Code's usage shape:
        input_tokens                   — fresh (uncached) input
        cache_creation_input_tokens    — written to cache this turn
        cache_read_input_tokens        — served from cache
        output_tokens                  — model output
    """
    p = price_for_model(model)
    in_tok          = usage.get("input_tokens", 0) or 0
    cw_tok          = usage.get("cache_creation_input_tokens", 0) or 0
    cr_tok          = usage.get("cache_read_input_tokens", 0) or 0
    out_tok         = usage.get("output_tokens", 0) or 0

    cost_input      = in_tok  * p["input"]       / 1_000_000
    cost_cache_w    = cw_tok  * p["cache_write"] / 1_000_000
    cost_cache_r    = cr_tok  * p["cache_read"]  / 1_000_000
    cost_output     = out_tok * p["output"]      / 1_000_000

    return {
        "total":      cost_input + cost_cache_w + cost_cache_r + cost_output,
        "input":      cost_input,
        "cache_write": cost_cache_w,
        "cache_read":  cost_cache_r,
        "output":     cost_output,
        "tokens": {
            "input": in_tok, "cache_write": cw_tok,
            "cache_read": cr_tok, "output": out_tok,
        },
    }


def find_transcripts(dirs: list[str]) -> list[str]:
    """Recursively find all .jsonl transcripts beneath the given roots."""
    files = []
    for d in dirs:
        if os.path.isdir(d):
            # ~/.claude/projects/*/*.jsonl
            files.extend(glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True))
    return files


def _parse_ts(ts):
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000.0)
        except (ValueError, OSError):
            return None
    return None


def extract_costs(filepath: str, start_date: datetime, end_date: datetime) -> list[dict]:
    """Extract cost entries from a Claude Code transcript within date range."""
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

                # CC format: top-level `type: "assistant"` with nested message
                if msg.get("type") != "assistant":
                    continue
                inner = msg.get("message") or {}
                usage = inner.get("usage") or {}
                if not usage:
                    continue

                model = inner.get("model") or msg.get("model") or "unknown"
                dt = _parse_ts(msg.get("timestamp") or inner.get("timestamp"))
                if dt is None:
                    continue
                if not (start_date <= dt <= end_date):
                    continue

                cost = compute_cost(usage, model)
                if cost["total"] <= 0:
                    continue

                entries.append({
                    "date":              dt.strftime("%Y-%m-%d"),
                    "timestamp":         dt.isoformat(),
                    "model":             model,
                    "cost_total":        cost["total"],
                    "cost_input":        cost["input"],
                    "cost_output":       cost["output"],
                    "cost_cache_read":   cost["cache_read"],
                    "cost_cache_write":  cost["cache_write"],
                    "tokens_input":      cost["tokens"]["input"],
                    "tokens_output":     cost["tokens"]["output"],
                    "tokens_cache_read": cost["tokens"]["cache_read"],
                    "tokens_cache_write": cost["tokens"]["cache_write"],
                })
    except (IOError, PermissionError) as e:
        print(f"   ⚠️  Skipping {filepath}: {e}", file=sys.stderr)
    return entries


def _recommend(monthly_projection: float, current_plan: str) -> tuple[str, float]:
    """Return (recommendation, potential_savings).

    Rule: recommend the cheapest plan whose effective price (plan monthly cost)
    is at least DOWNGRADE_MARGIN cheaper than the projected API spend, OR
    the plan that matches closest to projected usage without under-provisioning.
    """
    # Effective API spend is the projection. Plans are a flat price.
    # What we want: on current plan, is there a cheaper tier whose cost is
    # clearly below projection? If projection is below API-break-even by a
    # margin, drop to API.
    plans = [
        ("MAX_5X",  MAX_5X_MONTHLY),
        ("MAX_10X", MAX_10X_MONTHLY),
        ("MAX_20X", MAX_20X_MONTHLY),
    ]
    rank = {"API": 0, "MAX_5X": 1, "MAX_10X": 2, "MAX_20X": 3}
    current_rank = rank.get(current_plan, 3)

    # If projection is well below a cheaper tier's price, recommend dropping there.
    # If projection exceeds a higher tier's break-even, recommend upgrade.
    # Otherwise stay.
    if current_plan == "API":
        # API user — should they buy a plan?
        if monthly_projection >= MAX_20X_MONTHLY:
            return ("UPGRADE_TO_MAX_20X", monthly_projection - MAX_20X_MONTHLY)
        if monthly_projection >= MAX_10X_MONTHLY:
            return ("UPGRADE_TO_MAX_10X", monthly_projection - MAX_10X_MONTHLY)
        if monthly_projection >= MAX_5X_MONTHLY:
            return ("UPGRADE_TO_MAX_5X",  monthly_projection - MAX_5X_MONTHLY)
        return ("STAY_API", 0.0)

    # On a Max plan. Compare projection to cheaper tiers.
    # Downgrade to API only if projection is clearly below MAX_5X by margin.
    if monthly_projection < MAX_5X_MONTHLY * (1 - DOWNGRADE_MARGIN):
        current_price = {"MAX_5X": MAX_5X_MONTHLY, "MAX_10X": MAX_10X_MONTHLY, "MAX_20X": MAX_20X_MONTHLY}[current_plan]
        return ("DOWNGRADE_TO_API", current_price - monthly_projection)

    # Look for the cheapest plan strictly below current, whose price projection fits under.
    for name, price in plans:
        if rank[name] >= current_rank:
            break
        # "Fits under": projection < price × (1 - margin) means this tier has
        # enough room for the current usage pattern.
        if monthly_projection < price * (1 - DOWNGRADE_MARGIN):
            current_price = {"MAX_5X": MAX_5X_MONTHLY, "MAX_10X": MAX_10X_MONTHLY, "MAX_20X": MAX_20X_MONTHLY}[current_plan]
            return (f"DOWNGRADE_TO_{name}", current_price - price)
    return (f"STAY_{current_plan}", 0.0)


def analyze(entries: list[dict], days: int) -> dict:
    if not entries:
        return {"has_data": False}

    by_date: dict[str, float] = {}
    by_model: dict[str, dict] = {}
    for e in entries:
        by_date.setdefault(e["date"], 0.0)
        by_date[e["date"]] += e["cost_total"]
        by_model.setdefault(e["model"], {"cost": 0.0, "count": 0})
        by_model[e["model"]]["cost"] += e["cost_total"]
        by_model[e["model"]]["count"] += 1

    sorted_dates = sorted(by_date.keys())
    daily_costs = [{"date": d, "cost": round(by_date[d], 4)} for d in sorted_dates]

    total_cost = sum(by_date.values())
    days_with_data = len(by_date)
    daily_avg = total_cost / days_with_data if days_with_data > 0 else 0
    monthly_projection = daily_avg * 30

    if len(sorted_dates) >= 14:
        recent_7 = sum(by_date[d] for d in sorted_dates[-7:]) / 7
        prev_7   = sum(by_date[d] for d in sorted_dates[-14:-7]) / 7
        trend = "increasing" if recent_7 > prev_7 * 1.1 else "decreasing" if recent_7 < prev_7 * 0.9 else "stable"
        trend_pct = ((recent_7 - prev_7) / prev_7 * 100) if prev_7 > 0 else 0
    else:
        trend = "insufficient_data"
        trend_pct = 0

    recommendation, potential_savings = _recommend(monthly_projection, CURRENT_PLAN)

    plan_costs = {"API": 0.0, "MAX_5X": MAX_5X_MONTHLY, "MAX_10X": MAX_10X_MONTHLY, "MAX_20X": MAX_20X_MONTHLY}
    current_plan_cost = plan_costs.get(CURRENT_PLAN, MAX_20X_MONTHLY)

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
        "current_plan": CURRENT_PLAN,
        "current_plan_cost": current_plan_cost,
        "recommendation": recommendation,
        "potential_savings": round(potential_savings, 2),
        "daily_costs": daily_costs,
        "model_breakdown": model_breakdown,
        "total_calls": len(entries),
    }


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="API Cost Tracker (Claude Code)")
    parser.add_argument("--out", default="/tmp/cost-tracker.json", help="Output file path")
    parser.add_argument("--days", type=int, default=30, help="Number of days to analyze")
    args = parser.parse_args()

    print(f"💰 Cost Tracker — analyzing last {args.days} days of Claude Code transcripts")
    print(f"{'=' * 60}")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)
    print(f"   Period: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")

    transcripts = find_transcripts(TRANSCRIPT_DIRS)
    print(f"   Found {len(transcripts)} transcript files")
    if not transcripts:
        print("   No transcripts found under ~/.claude/projects")
        return 2

    all_entries = []
    for t in transcripts:
        all_entries.extend(extract_costs(t, start_date, end_date))

    print(f"   Extracted {len(all_entries)} cost entries")
    if not all_entries:
        print("   No cost data found in date range")
        return 2

    result = analyze(all_entries, args.days)

    print()
    print(f"   Total cost:          ${result['total_cost']}")
    print(f"   Daily average:       ${result['daily_average']}")
    print(f"   Monthly projection:  ${result['monthly_projection']}")
    print(f"   Trend:               {result['trend']} ({result['trend_pct']:+.1f}%)")
    print(f"   Current plan:        {result['current_plan']} (${result['current_plan_cost']}/mo)")
    print(f"   Recommendation:      {result['recommendation']}")
    if result["potential_savings"]:
        print(f"   Potential delta:     ${result['potential_savings']}/mo")
    print()
    print(f"   Model breakdown:")
    for m in result["model_breakdown"]:
        print(f"     {m['model']}: ${m['cost']} ({m['pct_of_total']}%, {m['calls']} calls)")

    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n📄 Data written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
