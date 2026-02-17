#!/usr/bin/env python3
"""
Gmail promotional email cleanup.
Trashes all emails in category:promotions across configured accounts.
Deterministic, no LLM needed.

Usage:
    python3 gmail-promo-cleanup.py [--dry-run] [--account EMAIL]
    
Environment:
    GOG_KEYRING_PASSWORD - required for gog CLI auth
"""

import subprocess
import json
import sys
import os
import argparse
from datetime import datetime

def get_accounts():
    """Get Gmail accounts from environment variable."""
    accounts_str = os.environ.get("GMAIL_ACCOUNTS", "")
    if not accounts_str.strip():
        print("❌ Error: GMAIL_ACCOUNTS environment variable is required (comma-separated email addresses)", file=sys.stderr)
        sys.exit(1)
    return [email.strip() for email in accounts_str.split(",") if email.strip()]

MAX_PER_PAGE = 100
MAX_PAGES = 3  # safety cap: 300 emails per account per run


def run_gog(args: list[str], account: str) -> subprocess.CompletedProcess:
    """Run a gog command with the given account."""
    cmd = ["gog"] + args + ["--account", account, "--force", "--no-input"]
    env = os.environ.copy()
    if "GOG_KEYRING_PASSWORD" not in os.environ:
        print("❌ Error: GOG_KEYRING_PASSWORD environment variable is required", file=sys.stderr)
        sys.exit(1)
    env["GOG_KEYRING_PASSWORD"] = os.environ["GOG_KEYRING_PASSWORD"]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)


def get_promo_ids(account: str, max_results: int = MAX_PER_PAGE) -> list[str]:
    """Fetch message IDs for promotional emails in inbox."""
    result = run_gog(
        ["gmail", "messages", "search", "in:inbox category:promotions",
         "--max", str(max_results), "--json"],
        account,
    )
    if result.returncode != 0:
        print(f"  ⚠️  Search failed: {result.stderr.strip()}", file=sys.stderr)
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  ⚠️  Invalid JSON from search", file=sys.stderr)
        return []

    messages = data.get("messages", [])
    return [m["id"] for m in messages if "id" in m]


def trash_messages(account: str, msg_ids: list[str], dry_run: bool = False) -> int:
    """Batch trash messages. Returns count trashed."""
    if not msg_ids:
        return 0
    if dry_run:
        print(f"  [DRY RUN] Would trash {len(msg_ids)} messages")
        return len(msg_ids)

    result = run_gog(
        ["gmail", "batch", "modify"] + msg_ids + ["--add", "TRASH", "--remove", "INBOX"],
        account,
    )
    if result.returncode != 0:
        print(f"  ⚠️  Batch modify failed: {result.stderr.strip()}", file=sys.stderr)
        return 0
    return len(msg_ids)


def cleanup_account(account: str, dry_run: bool = False) -> int:
    """Clean promos from one account. Returns total trashed."""
    total = 0
    for page in range(MAX_PAGES):
        ids = get_promo_ids(account)
        if not ids:
            break
        trashed = trash_messages(account, ids, dry_run)
        total += trashed
        print(f"  Page {page + 1}: {trashed} emails {'would be ' if dry_run else ''}trashed")
        if len(ids) < MAX_PER_PAGE:
            break  # no more pages
    return total


def main():
    parser = argparse.ArgumentParser(description="Trash Gmail promotional emails")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be trashed without doing it")
    parser.add_argument("--account", help="Run for a single account only")
    args = parser.parse_args()

    accounts = [args.account] if args.account else get_accounts()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"🧹 Gmail Promo Cleanup — {timestamp}")
    print(f"   Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"   Accounts: {len(accounts)}")
    print()

    grand_total = 0
    results = {}

    for account in accounts:
        print(f"📧 {account}")
        count = cleanup_account(account, args.dry_run)
        results[account] = count
        grand_total += count
        if count == 0:
            print("  ✅ Clean — no promos found")
        print()

    # Summary
    print(f"{'=' * 40}")
    print(f"Total: {grand_total} promotional emails {'would be ' if args.dry_run else ''}trashed")
    for account, count in results.items():
        print(f"  {account}: {count}")

    # Machine-readable output for cron integration
    summary = json.dumps({"timestamp": timestamp, "total": grand_total, "accounts": results})
    print(f"\nJSON: {summary}")

    return 0 if grand_total >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
