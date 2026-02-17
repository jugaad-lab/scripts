#!/usr/bin/env python3
"""
Morning briefing orchestrator.
Deterministic data collection layer — calls agent ONLY when actionable.

Exit codes:
    0 = actionable items found, data written to output file for agent
    2 = all clear, no agent needed
    1 = error

Usage:
    python3 morning-orchestrator.py [--out /tmp/morning-data.json] [--dry-run]

The cron job should:
    1. Run this script
    2. If exit 0 → spawn agent with "Read /tmp/morning-data.json and compose briefing"
    3. If exit 2 → skip agent, log "all clear"
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

# --- Config ---

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
def get_accounts():
    """Get Gmail accounts from environment variable."""
    accounts_str = os.environ.get("GMAIL_ACCOUNTS", "")
    if not accounts_str.strip():
        print("❌ Error: GMAIL_ACCOUNTS environment variable is required (comma-separated email addresses)", file=sys.stderr)
        sys.exit(1)
    return [email.strip() for email in accounts_str.split(",") if email.strip()]

def get_calendar_account():
    """Get calendar account from environment variable."""
    account = os.environ.get("CALENDAR_ACCOUNT", "")
    if not account:
        print("❌ Error: CALENDAR_ACCOUNT environment variable is required", file=sys.stderr)
        sys.exit(1)
    return account

def get_gog_env():
    """Get environment with GOG password."""
    if "GOG_KEYRING_PASSWORD" not in os.environ:
        print("❌ Error: GOG_KEYRING_PASSWORD environment variable is required", file=sys.stderr)
        sys.exit(1)
    return {**os.environ, "GOG_KEYRING_PASSWORD": os.environ["GOG_KEYRING_PASSWORD"]}

def get_important_senders():
    """Get important senders from environment variable."""
    # Default important sender patterns
    defaults = [
        "chase", "wellsfargo", "bankofamerica", "citi", "amex", "apple card",
        "google security", "security-noreply@google", "no-reply@accounts.google",
        "anthropic", "meta", "facebook",
        "irs.gov", "ssa.gov",
    ]
    
    # Add custom senders from environment
    custom_str = os.environ.get("IMPORTANT_SENDERS", "")
    if custom_str.strip():
        custom_senders = [sender.strip() for sender in custom_str.split(",") if sender.strip()]
        defaults.extend(custom_senders)
    
    return defaults

# Gmail categories considered noise (won't trigger agent)
NOISE_CATEGORIES = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


# --- Helpers ---

def run_command(cmd: list[str], env: dict = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(cmd, capture_output=True, text=True, env=env or os.environ, timeout=timeout)


def run_script(script_name: str, args: list[str] = None) -> dict | None:
    """Run a sibling script and parse its JSON output."""
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    cmd = [sys.executable, script_path] + (args or [])
    result = run_command(cmd, env=get_gog_env(), timeout=120)
    if result.returncode not in (0, 2):
        print(f"  ⚠️  {script_name} failed (exit {result.returncode}): {result.stderr[:200]}", file=sys.stderr)
        return None

    # Try to extract JSON from output (look for JSON: prefix or --json flag output)
    stdout = result.stdout.strip()

    # If we passed --json, the whole output is JSON
    if args and "--json" in args:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass

    # Otherwise look for "JSON: {...}" line
    for line in stdout.split("\n"):
        if line.startswith("JSON:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                pass

    return None


# --- Data Collection ---

def collect_promo_cleanup() -> dict:
    """Run promo cleanup script, return results."""
    print("🧹 Running promo cleanup...")
    result = run_script("gmail-promo-cleanup.py")
    if result:
        print(f"   Trashed {result.get('total', 0)} promos")
    return result or {"total": 0, "accounts": {}}


def collect_discord_digest(hours: int = 24) -> dict:
    """Run Discord digest, return results."""
    print("📊 Collecting Discord activity...")
    result = run_script("discord-activity-digest.py", ["--hours", str(hours), "--json"])
    if result:
        print(f"   {result.get('total_messages', 0)} msgs, {result.get('total_mentions', 0)} mentions")
    return result or {"total_messages": 0, "total_mentions": 0, "unanswered_mentions": [], "active_channels": []}


def collect_emails() -> dict:
    """Scan emails across all accounts, classify importance."""
    print("📬 Scanning emails...")
    all_emails = []
    important = []
    noise_count = 0
    
    accounts = get_accounts()
    important_senders = get_important_senders()

    for account in accounts:
        result = run_command(
            ["gog", "gmail", "search", "newer_than:1d", "--max", "20", "--account", account, "--json", "--no-input"],
            env=get_gog_env(),
            timeout=30,
        )
        if result.returncode != 0:
            print(f"   ⚠️  {account}: search failed", file=sys.stderr)
            continue

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue

        threads = data.get("threads", [])
        for thread in threads:
            labels = set(thread.get("labels", []))
            sender = thread.get("from", "").lower()
            subject = thread.get("subject", "")

            email_entry = {
                "account": account,
                "from": thread.get("from", ""),
                "subject": subject,
                "date": thread.get("date", ""),
                "labels": list(labels),
            }

            # Classify: is this important?
            is_noise = bool(labels & NOISE_CATEGORIES)
            is_important_sender = any(s in sender for s in important_senders)

            if is_important_sender or (not is_noise and "INBOX" in labels):
                email_entry["important"] = True
                important.append(email_entry)
            else:
                noise_count += 1

            all_emails.append(email_entry)

    print(f"   {len(important)} important, {noise_count} noise across {len(accounts)} accounts")
    return {
        "total": len(all_emails),
        "important": important,
        "important_count": len(important),
        "noise_count": noise_count,
    }


def collect_calendar() -> dict:
    """Check calendar for today and tomorrow."""
    print("📅 Checking calendar...")
    today = datetime.now().strftime("%Y-%m-%dT00:00:00")
    day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")

    result = run_command(
        ["gog", "calendar", "events", "primary", "--from", today, "--to", day_after,
         "--account", get_calendar_account(), "--json", "--no-input"],
        env=get_gog_env(),
        timeout=30,
    )

    if result.returncode != 0:
        print(f"   ⚠️  Calendar check failed", file=sys.stderr)
        return {"events": [], "today_count": 0, "tomorrow_count": 0}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"events": [], "today_count": 0, "tomorrow_count": 0}

    events = data.get("events", [])
    today_str = datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    today_events = [e for e in events if today_str in e.get("start", "")]
    tomorrow_events = [e for e in events if tomorrow_str in e.get("start", "")]

    # Simplify event data
    simplified = []
    for e in events:
        simplified.append({
            "summary": e.get("summary", "No title"),
            "start": e.get("start", ""),
            "end": e.get("end", ""),
            "status": e.get("status", ""),
        })

    print(f"   {len(today_events)} today, {len(tomorrow_events)} tomorrow")
    return {
        "events": simplified,
        "today_count": len(today_events),
        "tomorrow_count": len(tomorrow_events),
    }


# --- Actionability Check ---

def is_actionable(data: dict) -> tuple[bool, list[str]]:
    """Determine if any data warrants waking an agent. Returns (bool, reasons)."""
    reasons = []

    # Important emails
    if data["emails"]["important_count"] > 0:
        reasons.append(f"{data['emails']['important_count']} important emails")

    # Calendar events today
    if data["calendar"]["today_count"] > 0:
        reasons.append(f"{data['calendar']['today_count']} calendar events today")

    # Unanswered Discord mentions
    unanswered = len(data["discord"].get("unanswered_mentions", []))
    if unanswered > 0:
        reasons.append(f"{unanswered} unanswered Discord mentions")

    # Weekday check — always actionable on weekdays (work priorities matter)
    day_of_week = datetime.now().weekday()  # 0=Monday, 6=Sunday
    if day_of_week < 5:
        reasons.append("weekday — work priorities")

    return bool(reasons), reasons


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Morning briefing orchestrator")
    parser.add_argument("--out", default="/tmp/morning-data.json", help="Output file path")
    parser.add_argument("--dry-run", action="store_true", help="Collect data but don't write output")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day_name = datetime.now().strftime("%A")

    print(f"🌅 Morning Orchestrator — {day_name}, {timestamp}")
    print(f"{'=' * 50}")
    print()

    # Collect all data
    data = {
        "timestamp": timestamp,
        "day": day_name,
        "promos": collect_promo_cleanup(),
        "discord": collect_discord_digest(hours=24),
        "emails": collect_emails(),
        "calendar": collect_calendar(),
    }

    print()
    print(f"{'=' * 50}")

    # Check actionability
    actionable, reasons = is_actionable(data)

    if actionable:
        data["actionable"] = True
        data["reasons"] = reasons
        print(f"✅ ACTIONABLE — {len(reasons)} reasons:")
        for r in reasons:
            print(f"   • {r}")

        if not args.dry_run:
            with open(args.out, "w") as f:
                json.dump(data, f, indent=2)
            print(f"\n📄 Data written to {args.out}")
            print("   → Agent should read this file and compose briefing")
        else:
            print(f"\n[DRY RUN] Would write to {args.out}")

        return 0  # actionable
    else:
        print("😴 ALL CLEAR — nothing actionable")
        print("   → No agent needed, zero tokens burned")
        return 2  # not actionable


if __name__ == "__main__":
    sys.exit(main())
