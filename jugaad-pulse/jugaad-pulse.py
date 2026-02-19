#!/usr/bin/env -S python3 -u
"""
jugaad-pulse: Scan jugaad-lab GitHub org for open PRs, issues, and stale items.
Outputs JSON summary for agent consumption.

Exit codes:
    0 = items found needing attention
    2 = all clear
    1 = error
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

ORG = "jugaad-lab"
MY_USER = "bunny-bot-openclaw"
STALE_DAYS = 3  # PR open longer than this = stale


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


def get_open_prs(repo: str) -> list[dict]:
    """Get open PRs for a repo."""
    prs = gh_api(f"/repos/{ORG}/{repo}/pulls?state=open&per_page=50")
    if not prs:
        return []
    now = datetime.now(timezone.utc)
    results = []
    for pr in prs:
        created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        age_days = (now - created).days
        results.append({
            "number": pr["number"],
            "title": pr["title"],
            "author": pr["user"]["login"],
            "url": pr["html_url"],
            "age_days": age_days,
            "stale": age_days >= STALE_DAYS,
            "mine": pr["user"]["login"] == MY_USER,
            "reviews": pr.get("requested_reviewers", []),
        })
    return results


def get_open_issues(repo: str) -> list[dict]:
    """Get open issues (not PRs) for a repo."""
    issues = gh_api(f"/repos/{ORG}/{repo}/issues?state=open&per_page=50")
    if not issues:
        return []
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "author": i["user"]["login"],
            "url": i["html_url"],
            "labels": [l["name"] for l in i.get("labels", [])],
        }
        for i in issues
        if "pull_request" not in i  # exclude PRs from issues endpoint
    ]


def main():
    print(f"🔍 jugaad-pulse — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    repos = get_repos()
    if not repos:
        print("❌ Failed to list repos", file=sys.stderr)
        return 1

    print(f"   Scanning {len(repos)} repos...")

    all_prs = []
    all_issues = []
    my_stale_prs = []
    review_needed = []

    for repo in repos:
        prs = get_open_prs(repo)
        issues = get_open_issues(repo)

        for pr in prs:
            pr["repo"] = repo
            all_prs.append(pr)
            if pr["mine"] and pr["stale"]:
                my_stale_prs.append(pr)
            if not pr["mine"]:
                review_needed.append(pr)

        for issue in issues:
            issue["repo"] = repo
            all_issues.append(issue)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "repos_scanned": len(repos),
        "total_open_prs": len(all_prs),
        "total_open_issues": len(all_issues),
        "my_open_prs": [p for p in all_prs if p["mine"]],
        "my_stale_prs": my_stale_prs,
        "review_needed": review_needed,
        "all_issues": all_issues,
    }

    print(f"   {len(all_prs)} open PRs, {len(all_issues)} open issues")
    print(f"   {len(my_stale_prs)} of my PRs are stale (>{STALE_DAYS} days)")
    print(f"   {len(review_needed)} PRs from others need review")

    # Write output
    out_path = "/tmp/jugaad-pulse.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n📄 Written to {out_path}")

    has_items = my_stale_prs or review_needed or all_issues
    return 0 if has_items else 2


if __name__ == "__main__":
    sys.exit(main())
