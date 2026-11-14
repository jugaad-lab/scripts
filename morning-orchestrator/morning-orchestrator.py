#!/usr/bin/env -S python3 -u
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
import time
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

# --- Config ---

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

# Gmail categories that are ALWAYS noise
NOISE_CATEGORIES = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}

# Sender patterns that are noise even if Gmail doesn't categorize them.
# Unknown senders default to IMPORTANT — only add here if confirmed noise.
# Subject patterns that indicate a bill/payment — no sender filtering needed.
# Bills identify themselves by subject line.
BILL_SUBJECT_PATTERNS = [
    r"bill is ready", r"bill available", r"bill is available",
    r"new bill", r"your .* bill",
    r"payment due", r"payment is due", r"amount due", r"balance due",
    r"pay your", r"payment reminder", r"upcoming payment",
    r"scheduled payment", r"payment.* scheduled", r"autopay", r"auto.pay",
    r"new statement", r"statement is ready", r"statement available",
    r"your statement", r"monthly statement",
    r"invoice", r"amount owed",
    r"due date", r"due on \d",
    r"\$[\d,]+\.?\d*\s*due",  # "$102.61 due"
]

# Subject patterns that indicate appointments / reminders
APPOINTMENT_SUBJECT_PATTERNS = [
    r"appointment", r"your visit", r"reminder.*appointment",
    r"upcoming visit", r"check.?up", r"check-in",
    r"reservation", r"booking confirm",
    r"your (?:appointment|visit|reservation) is scheduled",
    r"your (?:appointment|visit|reservation) is confirmed",
    r"appointment on \w+", r"see you on",
    r"dental", r"dentist", r"doctor", r"physician", r"clinic",
    r"veterinar", r"vet visit", r"grooming",
    r"service reminder", r"maintenance", r"oil change",
    r"car service", r"vehicle service",
    r"haircut", r"salon", r"barber",
]

# Subject/sender patterns for topics Raj cares about
# Sourced from cashew brain — update when interests evolve
INTEREST_PATTERNS = [
    # Coffee
    r"coffee", r"espresso", r"roast", r"cafe", r"barista", r"latte",
    # Music (core identity — guitar, drums, production)
    r"guitar", r"drum", r"music", r"concert", r"live show", r"album",
    r"ableton", r"production", r"synth", r"pedal", r"midi",
    r"band", r"musician", r"songwrit",
    # Health & fitness (gym, nutrition, PCOS-friendly for Harika)
    r"workout", r"fitness", r"health", r"nutrition", r"supplement",
    r"sleep", r"wellness", r"pcos", r"meal plan", r"protein",
    r"gym", r"strength", r"training",
    # Puppy / dog
    r"puppy", r"dog train", r"pet", r"chiki",
    # Technology (AI, open source, agents)
    r"open.?source", r"self.?host", r"raspberry pi", r"home.?lab",
    r"llm", r"ai agent", r"anthropic", r"openai", r"claude",
    r"machine learn", r"neural", r"embeddings",
]

# Patterns indicating personal outreach (real humans reaching out)
PERSONAL_OUTREACH_PATTERNS = [
    # Subject patterns
    r"^re:", r"^hey ", r"^hi ", r"^hello",
    r"catch up", r"catching up", r"let'?s connect", r"quick question",
    r"reaching out", r"wanted to reach out", r"following up",
    r"chat\??$", r"raj,?\s*chat", r"coffee\??$",
    r"long time", r"how are you", r"how'?s it going",
    r"missed you", r"checking in",
]

# Recruiter detection — sender and subject patterns
RECRUITER_SENDER_PATTERNS = [
    r"recruit", r"talent", r"hiring", r"sourcing",
    r"inmail.*linkedin", r"linkedin\.com",  # LinkedIn InMail AND notifications about jobs
    r"lever\.co", r"greenhouse",
    r"ashbyhq", r"smartrecruiters", r"icims", r"workday",
    r"hired\.com", r"triplebyte", r"turing\.com",
]

# LinkedIn senders that are NOT recruiter signals (digests, social updates)
LINKEDIN_NOISE_SUBJECTS = [
    r"share.* thoughts", r"endorsed you", r"congratulat",
    r"trending", r"digest", r"people you may know",
    r"new post", r"commented on", r"reacted to",
    r"recently posted", r"posted a", r"published a",
    r"happy (work|birth)day", r"anniversary",
    r"announces", r"new article", r"in the news",
    r"take control", r"career tips", r"career in the",
    r"jobs? you might", r"recommended for you",
]

RECRUITER_SUBJECT_PATTERNS = [
    r"opportunity", r"role at", r"position at", r"job opening",
    r"developer opportunity", r"engineer opportunity",
    r"we'?re hiring", r"join .* team", r"interested in",
    r"your profile", r"your background", r"your experience",
    r"full.?stack", r"senior engineer", r"staff engineer",
    r"exciting opportunity", r"career opportunity",
    r"new job:", r"job alert",  # LinkedIn job notifications
    # Indirect recruiter pitches (hedge funds, trading firms, stealth startups)
    r"hedge fund", r"trading firm", r"quant", r"prop trading",
    r"great fit", r"perfect fit", r"your skills",
    r"confidential.*role", r"stealth", r"pre.?launch",
]

# Sender patterns that indicate automated/noreply (NOT personal outreach)
AUTOMATED_SENDER_PATTERNS = [
    r"noreply", r"no-reply", r"no_reply", r"donotreply",
    r"notifications?@", r"alerts?@", r"updates?@",
    r"newsletter", r"marketing@", r"promo",
    r"support@", r"billing@", r"info@",
    r"mailer-daemon", r"postmaster",
    r"marketplace.*amazon", r"shipment-tracking",
    r"account-update", r"security-alert",
    r"feedback@", r"survey@", r"rate.your",
]

NOISE_SENDERS = [
    # Digests / newsletters
    "quora", "digest@", "newsletters@", "noreply@marketing",
    # Marketing disguised as updates
    "starbucks", "avis", "hertz", "enterprise",
    "amazon business", "no-reply@business.amazon",
    "robinhood", "snacks.robinhood",
    "redfin", "zillow", "realtor.com",
    "bayclubs", "crunch fitness", "lifetime fitness",
    "doordash", "ubereats", "grubhub",
    "spotify", "netflix", "hulu", "youtube",
    "linkedin", "indeed", "glassdoor",
    "cleartax",
]


# --- Helpers ---

def run_command(cmd: list[str], env: dict = None, timeout: int = 60, retries: int = 3, backoff: float = 2.0) -> subprocess.CompletedProcess:
    """Run a command with retries and exponential backoff."""
    last_result = None
    for attempt in range(retries):
        last_result = subprocess.run(cmd, capture_output=True, text=True, env=env or os.environ, timeout=timeout)
        if last_result.returncode == 0:
            return last_result
        if attempt < retries - 1:
            wait = backoff * (2 ** attempt)
            print(f"   ⚠️  Attempt {attempt + 1} failed (exit {last_result.returncode}), retrying in {wait:.0f}s...", file=sys.stderr)
            time.sleep(wait)
    return last_result


def run_script(script_name: str, args: list[str] = None) -> dict | None:
    """Run a sibling script and parse its JSON output.
    script_name is the basename (e.g. 'gmail-promo-cleanup.py').
    Scripts live in SCRIPTS_DIR/<stem>/<script_name>.
    """
    stem = script_name.replace(".py", "")
    script_path = os.path.join(SCRIPTS_DIR, stem, script_name)
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


def _classify_email(subject: str, sender: str) -> dict:
    """Classify an email by subject/sender patterns. Returns tag dict."""
    subj_lower = subject.lower()
    sender_lower = sender.lower()
    combined = f"{subj_lower} {sender_lower}"
    tags = {}

    if any(re.search(p, subj_lower) for p in BILL_SUBJECT_PATTERNS):
        tags["bill"] = True
    if any(re.search(p, subj_lower) for p in APPOINTMENT_SUBJECT_PATTERNS):
        tags["appointment"] = True
    if any(re.search(p, combined) for p in INTEREST_PATTERNS):
        tags["interest"] = True

    # Recruiter detection (sender OR subject match)
    is_recruiter_sender = any(re.search(p, sender_lower) for p in RECRUITER_SENDER_PATTERNS)
    is_recruiter_subject = any(re.search(p, subj_lower) for p in RECRUITER_SUBJECT_PATTERNS)

    # LinkedIn special handling: sender matches but check if it's just a social digest
    if is_recruiter_sender and "linkedin" in sender_lower:
        is_linkedin_noise = any(re.search(p, subj_lower) for p in LINKEDIN_NOISE_SUBJECTS)
        if is_linkedin_noise:
            is_recruiter_sender = False

    if is_recruiter_sender or is_recruiter_subject:
        tags["recruiter"] = True

    # Personal outreach — subject looks personal AND sender is NOT automated
    is_automated = any(re.search(p, sender_lower) for p in AUTOMATED_SENDER_PATTERNS)
    if not is_automated and not tags:
        # Only check for personal outreach if not already tagged as something else
        if any(re.search(p, subj_lower) for p in PERSONAL_OUTREACH_PATTERNS):
            tags["personal_outreach"] = True

    return tags


def _get_thread_snippet(thread_id: str, account: str) -> str:
    """Fetch the snippet (preview text) for a thread. Lightweight — no full body decode."""
    result = run_command(
        ["gog", "gmail", "thread", "get", thread_id,
         "--account", account, "--json", "--no-input"],
        env=get_gog_env(), timeout=15, retries=1,
    )
    if result.returncode != 0:
        return ""
    try:
        data = json.loads(result.stdout)
        messages = data.get("thread", {}).get("messages", [])
        # Combine snippets from all messages in thread (most recent last)
        snippets = [m.get("snippet", "") for m in messages]
        return " ".join(snippets)[:500]
    except (json.JSONDecodeError, KeyError):
        return ""


# Body/snippet patterns for recruiter detection (second pass)
RECRUITER_BODY_PATTERNS = [
    r"recruit", r"hiring", r"talent",
    r"role", r"position", r"opportunity",
    r"great fit", r"perfect fit", r"strong fit",
    r"your background", r"your experience", r"your profile",
    r"job description", r"compensation", r"total comp",
    r"interview", r"screening", r"phone screen",
    r"engineering team", r"join our",
    r"resume", r"cv",
    r"trading firm", r"hedge fund", r"quant",
    r"series [a-d]", r"funded", r"startup",
]

# Body/snippet patterns for personal outreach (second pass)
PERSONAL_BODY_PATTERNS = [
    r"^hi raj", r"^hey raj", r"^hello raj",
    r"wanted to connect", r"wanted to reach out",
    r"let me know", r"would love to chat",
    r"catch up", r"coffee", r"meet up",
    r"how are you", r"how have you been",
    r"long time", r"been a while",
]

# Body/snippet patterns for appointments (second pass)
APPOINTMENT_BODY_PATTERNS = [
    r"appointment", r"scheduled for", r"your visit",
    r"please arrive", r"check.?in",
    r"reservation", r"booking",
    r"\d{1,2}:\d{2}\s*(am|pm)", # time mentions
]

# Body/snippet patterns for bills (second pass)
BILL_BODY_PATTERNS = [
    r"amount due", r"balance due", r"payment due",
    r"due date", r"pay by", r"autopay",
    r"bill is ready", r"statement",
    r"\$\d+\.?\d*\s*(due|owed|balance)",
]


def _classify_from_snippet(snippet: str) -> dict:
    """Second-pass classification using email body snippet."""
    snippet_lower = snippet.lower()
    tags = {}

    # Count recruiter signal matches — require 2+ to reduce false positives
    recruiter_hits = sum(1 for p in RECRUITER_BODY_PATTERNS if re.search(p, snippet_lower))
    if recruiter_hits >= 2:
        tags["recruiter"] = True

    # Require 2+ personal outreach signals to reduce false positives
    personal_hits = sum(1 for p in PERSONAL_BODY_PATTERNS if re.search(p, snippet_lower))
    if personal_hits >= 2:
        tags["personal_outreach"] = True

    if any(re.search(p, snippet_lower) for p in APPOINTMENT_BODY_PATTERNS):
        tags["appointment"] = True

    if any(re.search(p, snippet_lower) for p in BILL_BODY_PATTERNS):
        tags["bill"] = True

    return tags


def _mark_as_read(thread_ids_by_account: dict) -> None:
    """Mark surfaced email threads as read so Raj doesn't see them as unread."""
    env = get_gog_env()
    for account, thread_ids in thread_ids_by_account.items():
        if not thread_ids:
            continue
        for tid in thread_ids:
            result = run_command(
                ["gog", "gmail", "thread", "modify", tid,
                 "--remove", "UNREAD", "--account", account, "--force", "--no-input"],
                env=env, timeout=15, retries=1,
            )
            if result.returncode != 0:
                print(f"   ⚠️  Failed to mark {tid} as read in {account}", file=sys.stderr)
        print(f"   ✓ Marked {len(thread_ids)} threads as read in {account}")


def collect_emails() -> dict:
    """Scan emails across all accounts, classify importance."""
    print("📬 Scanning emails...")
    all_emails = []
    important = []
    noise_count = 0
    surfaced_threads = {}  # account -> [thread_ids] for mark-as-read
    scan_failures = []  # accounts whose search failed — scan is incomplete, NOT "inbox clear"

    accounts = get_accounts()

    for account in accounts:
        result = run_command(
            ["gog", "gmail", "search", "newer_than:1d", "--max", "20", "--account", account, "--json", "--no-input"],
            env=get_gog_env(),
            timeout=30,
        )
        if result.returncode != 0:
            print(f"   ⚠️  {account}: search failed", file=sys.stderr)
            scan_failures.append({"account": account, "stage": "search", "error": result.stderr[:300]})
            continue

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            scan_failures.append({"account": account, "stage": "parse", "error": "invalid JSON from gog search"})
            continue

        surfaced_threads[account] = []
        threads = data.get("threads", [])
        for thread in threads:
            labels = set(thread.get("labels", []))
            sender = thread.get("from", "").lower()
            subject = thread.get("subject", "")
            thread_id = thread.get("id", "")

            email_entry = {
                "account": account,
                "from": thread.get("from", ""),
                "subject": subject,
                "date": thread.get("date", ""),
                "labels": list(labels),
                "thread_id": thread_id,
            }

            # Classify by subject/sender patterns
            tags = _classify_email(subject, sender)
            email_entry.update(tags)

            # Bills, appointments, and interests are ALWAYS important
            # even if Gmail categorized them as promos/social/forums
            is_tagged = bool(tags)

            # Classify: noise if in noise category OR from noise sender.
            # Everything else defaults to important (unknown senders = important).
            is_noise = bool(labels & NOISE_CATEGORIES) or any(s in sender for s in NOISE_SENDERS)

            if is_tagged:
                email_entry["important"] = True
                important.append(email_entry)
                surfaced_threads[account].append(thread_id)
            elif is_noise:
                noise_count += 1
            elif "INBOX" in labels:
                email_entry["important"] = True
                important.append(email_entry)
                surfaced_threads[account].append(thread_id)
            else:
                noise_count += 1

            all_emails.append(email_entry)

    # Recruiter deep scan: check older unread emails for recruiter/personal outreach
    # This catches emails that arrived days ago but weren't seen
    print(f"   🎯 Recruiter deep scan (7 days, unread only)...")
    for account in accounts:
        result = run_command(
            ["gog", "gmail", "search", "is:unread newer_than:7d -category:promotions -category:social -category:forums",
             "--max", "15", "--account", account, "--json", "--no-input"],
            env=get_gog_env(), timeout=30,
        )
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue

        for thread in data.get("threads", []):
            thread_id = thread.get("id", "")
            # Skip if already processed in the 1-day scan
            if any(e.get("thread_id") == thread_id for e in all_emails):
                continue

            sender = thread.get("from", "").lower()
            subject = thread.get("subject", "")
            tags = _classify_email(subject, sender)

            if tags.get("recruiter") or tags.get("personal_outreach"):
                email_entry = {
                    "account": account,
                    "from": thread.get("from", ""),
                    "subject": subject,
                    "date": thread.get("date", ""),
                    "labels": thread.get("labels", []),
                    "thread_id": thread_id,
                    "important": True,
                    "classified_by": "recruiter_deep_scan",
                    **tags,
                }
                important.append(email_entry)
                surfaced_threads.setdefault(account, []).append(thread_id)
                print(f"      ✓ Deep scan: {subject[:60]} → {list(tags.keys())}")

            # If not classified by subject, check snippet for recruiter signals
            elif not any(re.search(p, sender.lower()) for p in AUTOMATED_SENDER_PATTERNS):
                try:
                    snippet = _get_thread_snippet(thread_id, account)
                    if snippet:
                        body_tags = _classify_from_snippet(snippet)
                        if body_tags.get("recruiter") or body_tags.get("personal_outreach"):
                            email_entry = {
                                "account": account,
                                "from": thread.get("from", ""),
                                "subject": subject,
                                "date": thread.get("date", ""),
                                "labels": thread.get("labels", []),
                                "thread_id": thread_id,
                                "important": True,
                                "classified_by": "recruiter_deep_scan_body",
                                "snippet_preview": snippet[:200],
                                **body_tags,
                            }
                            important.append(email_entry)
                            surfaced_threads.setdefault(account, []).append(thread_id)
                            print(f"      ✓ Deep scan (body): {subject[:60]} → {list(body_tags.keys())}")
                except Exception as e:
                    print(f"      ⚠️  Snippet fetch failed for {thread_id}: {e}", file=sys.stderr)

    # Second pass: for unclassified emails from non-automated senders,
    # fetch the snippet and try to classify from the body preview.
    # This catches recruiter emails with weird subject lines, personal outreach, etc.
    unclassified = [
        e for e in all_emails
        if not e.get("important")
        and e.get("thread_id")
        and not any(re.search(p, e.get("from", "").lower()) for p in AUTOMATED_SENDER_PATTERNS)
        and "INBOX" in set(e.get("labels", []))
    ]

    if unclassified:
        print(f"   🔍 Second pass: scanning {len(unclassified)} unclassified emails...")
        for email_entry in unclassified:
            try:
                snippet = _get_thread_snippet(email_entry["thread_id"], email_entry["account"])
                if not snippet:
                    continue
                body_tags = _classify_from_snippet(snippet)
                if body_tags:
                    email_entry.update(body_tags)
                    email_entry["important"] = True
                    email_entry["classified_by"] = "body_scan"
                    email_entry["snippet_preview"] = snippet[:200]
                    important.append(email_entry)
                    surfaced_threads.setdefault(email_entry["account"], []).append(email_entry["thread_id"])
                    noise_count -= 1  # was counted as noise in first pass
                    print(f"      ✓ Caught: {email_entry['subject'][:60]} → {list(body_tags.keys())}")
            except Exception as e:
                print(f"      ⚠️  Snippet fetch failed: {e}", file=sys.stderr)

    # Mark all surfaced emails as read. Non-fatal: a gog failure here must not
    # take down the whole email-scan step (and must not be swallowed either).
    total_surfaced = sum(len(ids) for ids in surfaced_threads.values())
    if total_surfaced > 0:
        print(f"   📖 Marking {total_surfaced} surfaced emails as read...")
        try:
            _mark_as_read(surfaced_threads)
        except Exception as e:
            print(f"   ⚠️  Mark-as-read failed: {e}", file=sys.stderr)
            scan_failures.append({"account": "all", "stage": "mark_as_read", "error": str(e)})

    print(f"   {len(important)} important, {noise_count} noise across {len(accounts)} accounts")
    return {
        "total": len(all_emails),
        "important": important,
        "important_count": len(important),
        "noise_count": noise_count,
        "scan_failed": bool(scan_failures),
        "scan_failures": scan_failures,
    }


def _to_pacific(dt_str: str, tz=None) -> str:
    """Convert an ISO datetime string to Pacific Time display format."""
    if not dt_str:
        return dt_str
    if tz is None:
        tz = ZoneInfo("America/Los_Angeles")
    try:
        # All-day events are just dates — return as-is
        if len(dt_str) <= 10:
            return dt_str
        # Try parsing with timezone info (e.g. 2026-03-09T10:00:00Z or +00:00)
        dt_str_clean = dt_str.replace("Z", "+00:00")
        from datetime import datetime as _dt
        dt = _dt.fromisoformat(dt_str_clean)
        if dt.tzinfo is None:
            # Assume UTC if no timezone
            dt = dt.replace(tzinfo=timezone.utc)
        dt_pacific = dt.astimezone(tz)
        return dt_pacific.strftime("%Y-%m-%dT%H:%M:%S %Z")
    except (ValueError, TypeError):
        return dt_str


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

    # Convert start times to Pacific BEFORE comparing dates — an event with a
    # non-Pacific offset can have a different calendar date in its raw start
    # string than its actual Pacific-local date, so matching the raw string
    # silently misses it.
    pacific = ZoneInfo("America/Los_Angeles")
    today_events = [e for e in events if today_str in _to_pacific(e.get("start", ""), pacific)]
    tomorrow_events = [e for e in events if tomorrow_str in _to_pacific(e.get("start", ""), pacific)]

    # Simplify event data, converting times to Pacific
    simplified = []
    for e in events:
        simplified.append({
            "summary": e.get("summary", "No title"),
            "start": _to_pacific(e.get("start", ""), pacific),
            "end": _to_pacific(e.get("end", ""), pacific),
            "status": e.get("status", ""),
        })

    print(f"   {len(today_events)} today, {len(tomorrow_events)} tomorrow")
    return {
        "events": simplified,
        "today_count": len(today_events),
        "tomorrow_count": len(tomorrow_events),
    }


# --- Workspace File Checks (zero-cost, filesystem only) ---

WORKSPACE_DIR = os.path.dirname(SCRIPTS_DIR)

def collect_cost_tracker() -> dict:
    """Run cost tracker and return results. Filesystem only, zero API calls."""
    print("💰 Checking API costs...")
    cost_file = "/tmp/cost-tracker.json"

    # Run the cost tracker script
    cost_script = os.path.join(SCRIPTS_DIR, "cost-tracker", "cost-tracker.py")
    if not os.path.exists(cost_script):
        print("   Cost tracker script not found")
        return {"exists": False}

    result = subprocess.run(
        [sys.executable, cost_script, "--out", cost_file, "--days", "30"],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode not in (0, 2):
        print(f"   ⚠️  Cost tracker failed: {result.stderr[:200]}", file=sys.stderr)
        return {"exists": False}

    if result.returncode == 2:
        print("   No cost data found")
        return {"exists": True, "has_data": False}

    try:
        with open(cost_file) as f:
            data = json.load(f)
        # Only pass summary to agent, not full daily breakdown
        summary = {
            "exists": True,
            "has_data": data.get("has_data", False),
            "total_cost": data.get("total_cost"),
            "daily_average": data.get("daily_average"),
            "monthly_projection": data.get("monthly_projection"),
            "trend": data.get("trend"),
            "recommendation": data.get("recommendation"),
            "model_breakdown": data.get("model_breakdown", [])[:3],  # top 3 models only
        }
        print(f"   ${data.get('daily_average', 0)}/day avg, ${data.get('monthly_projection', 0)}/mo projected → {data.get('recommendation', 'N/A')}")
        return summary
    except (json.JSONDecodeError, IOError):
        return {"exists": False}


def collect_blog_pipeline() -> dict:
    """Check blog pipeline status from cashew brain."""
    print("📝 Checking blog pipeline...")
    cashew_dir = os.path.join(WORKSPACE_DIR, "cashew")
    try:
        env = os.environ.copy()
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        result = subprocess.run(
            ["python3", "scripts/cashew_context.py", "context", "--hints", "blog pipeline active draft status shipping"],
            capture_output=True, text=True, timeout=30, cwd=cashew_dir, env=env
        )
        output = result.stdout.strip()
        # Extract relevant lines from brain output
        blog_lines = [l.strip() for l in output.split("\n") if any(kw in l.lower() for kw in ["blog", "draft", "ship"])]
        if blog_lines:
            print(f"   {len(blog_lines)} blog-related nodes found")
            return {"exists": True, "brain_context": blog_lines[:10]}
        else:
            print("   No active blog items in brain")
            return {"exists": False}
    except Exception as e:
        print(f"   Brain query failed: {e}")
        return {"exists": False}


def collect_todos() -> dict:
    """Check pending items from cashew brain."""
    print("✅ Checking todos...")
    cashew_dir = os.path.join(WORKSPACE_DIR, "cashew")
    try:
        env = os.environ.copy()
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        result = subprocess.run(
            ["python3", "scripts/cashew_context.py", "context", "--hints", "pending todo action items open tasks deadlines"],
            capture_output=True, text=True, timeout=30, cwd=cashew_dir, env=env
        )
        output = result.stdout.strip()
        todo_lines = [l.strip() for l in output.split("\n") if any(kw in l.lower() for kw in ["todo", "pending", "deadline", "action", "task", "need to"])]
        if todo_lines:
            print(f"   {len(todo_lines)} pending items found")
            return {"exists": True, "pending_count": len(todo_lines), "brain_context": todo_lines[:10]}
        else:
            print("   No pending items in brain")
            return {"exists": False, "pending_count": 0}
    except Exception as e:
        print(f"   Brain query failed: {e}")
        return {"exists": False, "pending_count": 0}


# --- Actionability Check ---

def is_actionable(data: dict) -> tuple[bool, list[str]]:
    """Determine if any data warrants waking an agent. Returns (bool, reasons)."""
    reasons = []

    # Email scan failures — surface explicitly so the briefing doesn't imply
    # a clean inbox when the scan actually failed partway through.
    if data["emails"].get("scan_failed"):
        failed_accounts = ", ".join(f.get("account", "?") for f in data["emails"].get("scan_failures", []))
        reasons.append(f"⚠️ email scan failed for: {failed_accounts}")

    # Bills / financial emails (high priority)
    bills = [e for e in data["emails"].get("important", []) if e.get("bill")]
    if bills:
        reasons.append(f"💰 {len(bills)} bill(s) due — needs attention")

    # Appointments
    appts = [e for e in data["emails"].get("important", []) if e.get("appointment")]
    if appts:
        reasons.append(f"📋 {len(appts)} appointment reminder(s)")

    # Recruiter emails (high priority — Raj wants to be proactive)
    recruiters = [e for e in data["emails"].get("important", []) if e.get("recruiter")]
    if recruiters:
        reasons.append(f"🎯 {len(recruiters)} recruiter outreach(es) — needs review")

    # Personal outreach
    outreach = [e for e in data["emails"].get("important", []) if e.get("personal_outreach")]
    if outreach:
        reasons.append(f"👋 {len(outreach)} personal outreach(es)")

    # Interest-based emails
    interests = [e for e in data["emails"].get("important", []) if e.get("interest")]
    if interests:
        reasons.append(f"⭐ {len(interests)} email(s) matching your interests")

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

    # Cost tracker — only flag if there's an actionable plan change recommendation
    costs = data.get("costs", {})
    rec = costs.get("recommendation", "")
    if rec.startswith("DOWNGRADE_") or rec.startswith("UPGRADE_"):
        savings = costs.get("potential_savings", 0)
        reasons.append(f"API costs: ${costs.get('monthly_projection', '?')}/mo projected → {rec} (save ~${savings}/mo)")

    # Blog pipeline — flag if brain has active blog items
    blog = data.get("blog_pipeline", {})
    if blog.get("exists") and blog.get("brain_context"):
        reasons.append("active blog item(s) in brain")

    # Pending todos
    todos = data.get("todos", {})
    if todos.get("pending_count", 0) > 0:
        reasons.append(f"{todos['pending_count']} pending todo(s)")

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

    # Collect all data — each collector is isolated so one failure doesn't kill the rest
    def safe_collect(name, fn, fallback):
        try:
            return fn()
        except Exception as e:
            print(f"   ⚠️  {name} failed: {e}", file=sys.stderr)
            return fallback

    data = {
        "timestamp": timestamp,
        "day": day_name,
        "promos": safe_collect("promo cleanup", collect_promo_cleanup, {"total": 0, "accounts": {}}),
        "discord": safe_collect("discord digest", lambda: collect_discord_digest(hours=24), {"total_messages": 0, "total_mentions": 0, "unanswered_mentions": [], "active_channels": []}),
        "emails": safe_collect("email scan", collect_emails, {"total": 0, "important": [], "important_count": 0, "noise_count": 0}),
        "calendar": safe_collect("calendar check", collect_calendar, {"events": [], "today_count": 0, "tomorrow_count": 0}),
        "blog_pipeline": safe_collect("blog pipeline", collect_blog_pipeline, {"exists": False}),
        "todos": safe_collect("todos", collect_todos, {"exists": False, "pending_count": 0}),
        "costs": safe_collect("cost tracker", collect_cost_tracker, {"exists": False}),
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
