#!/usr/bin/env python3
"""Check if OpenClaw config models are up-to-date with Anthropic's latest."""

import argparse, json, re, sys, urllib.request
from pathlib import Path

MODELS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"
# Support both current (~/.openclaw/) and legacy (~/.clawdbot/) config paths
CONFIG_PATHS = [Path.home() / ".openclaw" / "openclaw.json",
                Path.home() / ".clawdbot" / "openclaw.json"]

FAMILIES = ["opus", "sonnet", "haiku"]


def find_default_config():
    """Find the first existing config path."""
    for p in CONFIG_PATHS:
        if p.exists():
            return p
    return CONFIG_PATHS[0]  # fall back to primary path for error messaging


def fetch_latest_models():
    """Scrape Anthropic docs for latest model versions.
    
    NOTE: This is inherently brittle — depends on Anthropic's HTML structure.
    If it breaks (returns empty dict), update the regex patterns below.
    """
    try:
        req = urllib.request.Request(MODELS_URL, headers={"User-Agent": "ModelFreshness/1.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode()
    except Exception as e:
        print(f"ERROR: Failed to fetch Anthropic docs: {e}", file=sys.stderr)
        sys.exit(2)
    latest = {}
    for family in FAMILIES:
        pat = re.compile(rf"claude-{family}-[\d.]+-[\d]+|claude-{family}-[\d.]+")
        matches = pat.findall(html)
        if matches:
            latest[family] = matches[0]
    if not latest:
        print("ERROR: Scraped 0 models from Anthropic docs — page structure may have changed.", file=sys.stderr)
        sys.exit(2)
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
    parser.add_argument("--config", default=None, help="Path to openclaw.json (auto-detects if not set)")
    parser.add_argument("--discord", action="store_true", help="Format output for Discord")
    args = parser.parse_args()

    config_path = args.config or str(find_default_config())
    if not Path(config_path).exists():
        print(f"ERROR: Config not found at {config_path}", file=sys.stderr)
        sys.exit(2)
    results = check_freshness(config_path)
    print(format_discord(results) if args.discord else format_plain(results))
    sys.exit(1 if any(r["stale"] for r in results) else 0)
