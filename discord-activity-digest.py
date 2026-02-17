#!/usr/bin/env python3
"""
Discord activity digest.
Pulls recent messages from configured channels, summarizes activity,
and flags unanswered @mentions.

Deterministic data collection — no LLM needed.

Usage:
    python3 discord-activity-digest.py [--hours 24] [--json]
    
Environment:
    DISCORD_BOT_TOKEN - required Discord bot token
    DISCORD_GUILD_ID - required Discord server/guild ID

Output: structured summary of channel activity for morning briefing consumption.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# --- Config ---

def load_bot_token() -> str:
    """Read Discord bot token from environment variable."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print("❌ Error: DISCORD_BOT_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)
    return token
def get_guild_id():
    """Get Discord guild ID from environment variable."""
    guild_id = os.environ.get("DISCORD_GUILD_ID", "")
    if not guild_id:
        print("❌ Error: DISCORD_GUILD_ID environment variable is required", file=sys.stderr)
        sys.exit(1)
    return guild_id
BOT_USER_ID = None  # detected at runtime from bot token

# Channel name mapping (from guild config)
CHANNEL_NAMES = {
    "1470196659886620836": "introductions",
    "1470196635526107353": "rules",
    "1470196687464042755": "general",
    "1470196702823583856": "announcements",
    "1470196716258070681": "resources",
    "1470196747119886570": "showcase",
    "1470196754564645020": "off-topic",
    "1470196773799596033": "status-updates",
    "1470196777813541108": "ai-agents",
    "1470196790430138500": "bot-collaboration",
    "1470196807379320832": "pain-points",
    "1470196811141480625": "wins",
    "1470196824223645718": "requests",
    "1470196840992346176": "logs",
}

DISCORD_API = "https://discord.com/api/v10"


# Removed old load_bot_token - now reads from environment variable


def discord_get(endpoint: str, token: str) -> dict | list | None:
    """Make a GET request to Discord API."""
    url = f"{DISCORD_API}{endpoint}"
    req = Request(url, headers={
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://openclaw.ai, 1.0)",
    })
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"⚠️  Discord API error {e.code}: {e.read().decode()}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️  Request failed: {e}", file=sys.stderr)
        return None


def get_bot_user_id(token: str) -> str | None:
    """Get the bot's own user ID."""
    data = discord_get("/users/@me", token)
    return data.get("id") if data else None


def get_channel_messages(channel_id: str, token: str, after_snowflake: str) -> list[dict]:
    """Fetch messages from a channel after a given snowflake ID."""
    messages = discord_get(
        f"/channels/{channel_id}/messages?after={after_snowflake}&limit=100",
        token,
    )
    return messages if isinstance(messages, list) else []


def datetime_to_snowflake(dt: datetime) -> str:
    """Convert a datetime to a Discord snowflake ID for filtering."""
    # Discord epoch: 2015-01-01T00:00:00Z
    discord_epoch = 1420070400000
    timestamp_ms = int(dt.timestamp() * 1000)
    snowflake = (timestamp_ms - discord_epoch) << 22
    return str(snowflake)


def analyze_messages(messages: list[dict], bot_user_id: str | None) -> dict:
    """Analyze messages for a channel."""
    if not messages:
        return {
            "count": 0,
            "authors": {},
            "mentions_bot": [],
            "bot_replied": False,
        }

    authors: dict[str, int] = {}
    mentions_bot: list[dict] = []

    for msg in messages:
        author_name = msg.get("author", {}).get("username", "unknown")
        author_id = msg.get("author", {}).get("id", "")
        authors[author_name] = authors.get(author_name, 0) + 1

        # Check if bot was mentioned
        mentioned_ids = [m.get("id") for m in msg.get("mentions", [])]
        content = msg.get("content", "")
        if bot_user_id and (bot_user_id in mentioned_ids or f"<@{bot_user_id}>" in content):
            mentions_bot.append({
                "id": msg["id"],
                "author": author_name,
                "content": content[:200],
                "timestamp": msg.get("timestamp", ""),
            })

    # Check if bot has replied after any mention
    bot_message_ids = set()
    if bot_user_id:
        for msg in messages:
            if msg.get("author", {}).get("id") == bot_user_id:
                bot_message_ids.add(msg["id"])
                # Also check referenced messages
                ref = msg.get("referenced_message")
                if ref:
                    bot_message_ids.add(ref.get("id", ""))

    return {
        "count": len(messages),
        "authors": authors,
        "mentions_bot": mentions_bot,
        "bot_replied": bool(bot_message_ids),
    }


def main():
    parser = argparse.ArgumentParser(description="Discord activity digest")
    parser.add_argument("--hours", type=int, default=24, help="Look back N hours (default: 24)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    token = load_bot_token()
    bot_user_id = get_bot_user_id(token)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    after_snowflake = datetime_to_snowflake(cutoff)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    channels_data = {}
    total_messages = 0
    total_mentions = 0
    active_channels = []

    for channel_id, channel_name in CHANNEL_NAMES.items():
        messages = get_channel_messages(channel_id, token, after_snowflake)
        analysis = analyze_messages(messages, bot_user_id)
        channels_data[channel_name] = analysis
        total_messages += analysis["count"]
        total_mentions += len(analysis["mentions_bot"])
        if analysis["count"] > 0:
            active_channels.append((channel_name, analysis["count"]))

    # Sort active channels by message count
    active_channels.sort(key=lambda x: -x[1])

    # Collect all unanswered mentions
    unanswered = []
    for ch_name, data in channels_data.items():
        for mention in data["mentions_bot"]:
            unanswered.append({**mention, "channel": ch_name})

    result = {
        "timestamp": timestamp,
        "hours": args.hours,
        "total_messages": total_messages,
        "total_mentions": total_mentions,
        "active_channels": active_channels,
        "unanswered_mentions": unanswered,
        "channels": channels_data,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    # Human-readable output
    print(f"📊 Electrons in a Box — Activity Digest ({args.hours}h)")
    print(f"   Generated: {timestamp}")
    print(f"   Total messages: {total_messages}")
    print()

    if not active_channels:
        print(f"   🔇 No activity in the last {args.hours} hours")
    else:
        print("   Active channels:")
        for ch_name, count in active_channels:
            authors = channels_data[ch_name]["authors"]
            author_str = ", ".join(f"{name}({c})" for name, c in sorted(authors.items(), key=lambda x: -x[1]))
            print(f"   #{ch_name}: {count} msgs — {author_str}")

    if unanswered:
        print()
        print(f"   ⚠️  Unanswered @mentions: {len(unanswered)}")
        for m in unanswered:
            print(f"   - #{m['channel']} by {m['author']}: {m['content'][:100]}")
    else:
        print()
        print("   ✅ No unanswered @mentions")

    print()
    print(f"JSON: {json.dumps({'total_messages': total_messages, 'total_mentions': total_mentions, 'active_channels': len(active_channels), 'unanswered': len(unanswered)})}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
