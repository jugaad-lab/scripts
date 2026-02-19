#!/usr/bin/env -S python3 -u
"""
jugaad-pulse: Scan jugaad-lab GitHub org for open PRs, issues, and stale items.
Outputs JSON summary for agent consumption, or a Discord-formatted summary.

Exit codes:
    0 = items found needing attention
    2 = all clear
    1 = error

Env vars:
    PULSE_USER       GitHub username to consider "mine" (default: bunny-bot-openclaw)
    PULSE_STALE_DAYS Days before a PR/issue is considered stale (default: 3)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ORG = "jugaad-lab"
KNOWN_BOTS = ["bunny-bot-openclaw", "cheenu1092-oss", "ChhotuBot", "jarvis-bot"]
MY_USER = os.environ.get("PULSE_USER", "bunny-bot-openclaw")
STALE_DAYS = int(os.environ.get("PULSE_STALE_DAYS", "3"))


def gh_api(endpoint: str) -> list | dict | None:
    """Call GitHub API via gh CLI."""
    result = subprocess.run(
        ["gh", "api", endpoint, "--paginate"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  ⚠️ gh api {endpoint} failed: {result.stderr[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def get_repos() -> list[str]:
    """List all repos in the org."""
    repos = gh_api(f"/orgs/{ORG}/repos?per_page=100&type=all")
    if not repos:
        return []
    return [r["name"] for r in repos]


def get_review_state(repo: str, pr_number: int) -> str:
    """
    Get the latest review state for a PR.

    Returns one of: "approved", "changes_requested", "pending"
    - "approved"           = at least one APPROVED and no later CHANGES_REQUESTED
    - "changes_requested"  = latest substantive review is CHANGES_REQUESTED
    - "pending"            = no reviews yet (or only COMMENTED)
    """
    reviews = gh_api(f"/repos/{ORG}/{repo}/pulls/{pr_number}/reviews")
    if not reviews:
        return "pending"

    # Walk reviews in order, track per-reviewer latest state
    # (a reviewer can change their mind)
    reviewer_latest: dict[str, str] = {}
    for review in reviews:
        state = review.get("state", "").upper()
        reviewer = review["user"]["login"]
        if state in ("APPROVED", "CHANGES_REQUESTED"):
            reviewer_latest[reviewer] = state

    if not reviewer_latest:
        return "pending"

    states = set(reviewer_latest.values())
    if "CHANGES_REQUESTED" in states:
        return "changes_requested"
    if all(s == "APPROVED" for s in states):
        return "approved"
    return "pending"


def get_open_prs(repo: str) -> list[dict]:
    """Get open PRs for a repo, enriched with review state and activity info."""
    prs = gh_api(f"/repos/{ORG}/{repo}/pulls?state=open&per_page=50")
    if not prs:
        return []
    now = datetime.now(timezone.utc)
    results = []
    for pr in prs:
        created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
        age_days = (now - created).days
        days_since_activity = (now - updated).days
        author = pr["user"]["login"]
        stale = days_since_activity >= STALE_DAYS

        review_state = get_review_state(repo, pr["number"])

        results.append({
            "number": pr["number"],
            "title": pr["title"],
            "author": author,
            "url": pr["html_url"],
            "age_days": age_days,
            "days_since_activity": days_since_activity,
            "stale": stale,
            "mine": author == MY_USER,
            "is_bot": author in KNOWN_BOTS,
            "review_state": review_state,
            "reviews": pr.get("requested_reviewers", []),
        })
    return results


def get_open_issues(repo: str) -> list[dict]:
    """Get open issues (not PRs) for a repo."""
    issues = gh_api(f"/repos/{ORG}/{repo}/issues?state=open&per_page=50")
    if not issues:
        return []
    now = datetime.now(timezone.utc)
    result = []
    for i in issues:
        if "pull_request" in i:
            continue  # exclude PRs from issues endpoint
        updated = datetime.fromisoformat(i["updated_at"].replace("Z", "+00:00"))
        days_since_activity = (now - updated).days
        author = i["user"]["login"]
        result.append({
            "number": i["number"],
            "title": i["title"],
            "author": author,
            "url": i["html_url"],
            "labels": [l["name"] for l in i.get("labels", [])],
            "days_since_activity": days_since_activity,
            "stale": days_since_activity >= STALE_DAYS,
            "mine": author == MY_USER,
            "is_bot": author in KNOWN_BOTS,
        })
    return result


def print_discord_summary(summary: dict) -> None:
    """Print a Discord-formatted markdown summary."""
    today = datetime.now().strftime("%Y-%m-%d")
    n_repos = summary["repos_scanned"]
    n_prs = summary["total_open_prs"]
    n_issues = summary["total_open_issues"]

    lines = [
        f"📊 **jugaad-pulse** — {today}",
        f"Scanned {n_repos} repos | {n_prs} open PRs | {n_issues} open issues",
        "",
    ]

    # Build attention list: stale PRs with changes_requested (highest priority first)
    needs_attention = [
        p for p in summary["all_prs"]
        if p["stale"] and p["review_state"] == "changes_requested"
    ]
    # Also include stale PRs authored by known bots (our bots' work going stale)
    stale_bot_prs = [
        p for p in summary["all_prs"]
        if p["stale"] and p["is_bot"] and p not in needs_attention
    ]
    # Stale issues
    stale_issues = [i for i in summary["all_issues"] if i["stale"]]

    # PRs needing review (not mine, not stale-changes_requested — those are in attention)
    review_needed = [
        p for p in summary["all_prs"]
        if not p["mine"]
        and not (p["stale"] and p["review_state"] == "changes_requested")
        and p["review_state"] != "approved"
    ]

    has_anything = needs_attention or stale_bot_prs or stale_issues or review_needed

    if needs_attention or stale_bot_prs or stale_issues:
        lines.append("🔴 **Needs Attention:**")
        for p in needs_attention:
            review_tag = f", {p['review_state'].replace('_', ' ')}" if p["review_state"] != "pending" else ""
            lines.append(
                f"  - [{p['repo']}] PR #{p['number']}: {p['title']} "
                f"({p['author']}, {p['days_since_activity']}d inactive{review_tag})"
            )
        for p in stale_bot_prs:
            lines.append(
                f"  - [{p['repo']}] PR #{p['number']}: {p['title']} "
                f"({p['author']} 🤖, {p['days_since_activity']}d inactive)"
            )
        for i in stale_issues:
            lines.append(
                f"  - [{i['repo']}] Issue #{i['number']}: {i['title']} "
                f"({i['author']}, {i['days_since_activity']}d inactive)"
            )
        lines.append("")

    if review_needed:
        lines.append("👀 **Review Needed:**")
        for p in review_needed:
            state_tag = f", {p['review_state'].replace('_', ' ')}" if p["review_state"] != "pending" else ""
            lines.append(
                f"  - [{p['repo']}] PR #{p['number']}: {p['title']} "
                f"({p['author']}, {p['age_days']}d old{state_tag})"
            )
        lines.append("")

    if not has_anything:
        lines.append("✅ **All Clear**")

    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="jugaad-pulse: Scan jugaad-lab org for open PRs, issues, stale items."
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        help="Print a Discord-formatted markdown summary instead of JSON.",
    )
    parser.add_argument(
        "--out",
        default="/tmp/jugaad-pulse.json",
        help="Output path for JSON (default: /tmp/jugaad-pulse.json)",
    )
    args = parser.parse_args()

    if not args.discord:
        print(f"🔍 jugaad-pulse — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    repos = get_repos()
    if not repos:
        print("❌ Failed to list repos", file=sys.stderr)
        return 1

    if not args.discord:
        print(f"   Scanning {len(repos)} repos...")

    all_prs = []
    all_issues = []

    for repo in repos:
        prs = get_open_prs(repo)
        issues = get_open_issues(repo)
        for pr in prs:
            pr["repo"] = repo
            all_prs.append(pr)
        for issue in issues:
            issue["repo"] = repo
            all_issues.append(issue)

    my_open_prs = [p for p in all_prs if p["mine"]]
    my_stale_prs = [p for p in my_open_prs if p["stale"]]
    review_needed = [p for p in all_prs if not p["mine"] and p["review_state"] != "approved"]
    needs_attention = [
        p for p in all_prs if p["stale"] and p["review_state"] == "changes_requested"
    ]

    summary = {
        "timestamp": datetime.now().isoformat(),
        "repos_scanned": len(repos),
        "total_open_prs": len(all_prs),
        "total_open_issues": len(all_issues),
        "my_user": MY_USER,
        "stale_days_threshold": STALE_DAYS,
        "all_prs": all_prs,
        "all_issues": all_issues,
        "my_open_prs": my_open_prs,
        "my_stale_prs": my_stale_prs,
        "review_needed": review_needed,
        "needs_attention": needs_attention,
    }

    if args.discord:
        print_discord_summary(summary)
    else:
        print(f"   {len(all_prs)} open PRs, {len(all_issues)} open issues")
        print(f"   {len(my_stale_prs)} of my PRs are stale (>{STALE_DAYS} days inactive)")
        print(f"   {len(review_needed)} PRs from others need review")
        print(f"   {len(needs_attention)} PRs need attention (stale + changes requested)")

        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\n📄 Written to {args.out}")

    has_items = my_stale_prs or review_needed or all_issues or needs_attention
    return 0 if has_items else 2


if __name__ == "__main__":
    sys.exit(main())
