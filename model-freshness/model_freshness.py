#!/usr/bin/env python3
"""Check if OpenClaw config models are up-to-date with Anthropic's latest."""

import argparse, json, re, sys, urllib.request
from pathlib import Path

MODELS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"
DEFAULT_CONFIG = Path.home() / ".openclaw" / "openclaw.json"

FAMILIES = ["opus", "sonnet", "haiku"]


def fetch_latest_models():
    """Scrape Anthropic docs for latest model API aliases from the first table."""
    req = urllib.request.Request(MODELS_URL, headers={"User-Agent": "ModelFreshness/1.0"})
    html = urllib.request.urlopen(req, timeout=15).read().decode()
    latest = {}
    # Find "API alias" row entries — these are the canonical latest aliases
    # Also check for "API ID" entries as fallback
    for family in FAMILIES:
        # Look for alias first (e.g. claude-opus-4-6), then ID
        pat = re.compile(rf"claude-{family}-[\d.]+-[\d]+|claude-{family}-[\d.]+")
        matches = pat.findall(html)
        if matches:
            # First occurrence on the page is from the "Latest models" table
            latest[family] = matches[0]
    return latest


def extract_config_models(config_path):
    """Pull all model strings from OpenClaw config."""
    text = Path(config_path).read_text()
    models = set()
    for m in re.finditer(r"anthropic/(claude-(?:opus|sonnet|haiku)-[\w.-]+)", text):
        models.add(m.group(1))
    return models


def check_freshness(config_path):
    latest = fetch_latest_models()
    config_models = extract_config_models(config_path)
    results = []
    for family in FAMILIES:
        for cm in config_models:
            if family in cm:
                latest_id = latest.get(family, "unknown")
                stale = cm != latest_id
                results.append({"family": family, "config": cm, "latest": latest_id, "stale": stale})
    return results


def format_plain(results):
    lines = ["Model Freshness Report", "=" * 40]
    for r in results:
        status = "⚠️  STALE" if r["stale"] else "✅ Current"
        lines.append(f"{status} | {r['family']:>7} | config: {r['config']} → latest: {r['latest']}")
    if not any(r["stale"] for r in results):
        lines.append("\nAll models are up to date! 🎉")
    return "\n".join(lines)


def format_discord(results):
    lines = ["**🔍 Model Freshness Report**", "```"]
    for r in results:
        icon = "⚠️" if r["stale"] else "✅"
        lines.append(f"{icon} {r['family']:>7}: {r['config']} → {r['latest']}")
    lines.append("```")
    if any(r["stale"] for r in results):
        lines.append("🚨 Some models need updating!")
    else:
        lines.append("All models are current! 🎉")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check OpenClaw model freshness against Anthropic docs")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to openclaw.json")
    parser.add_argument("--discord", action="store_true", help="Format output for Discord")
    args = parser.parse_args()

    results = check_freshness(args.config)
    print(format_discord(results) if args.discord else format_plain(results))
    sys.exit(1 if any(r["stale"] for r in results) else 0)
