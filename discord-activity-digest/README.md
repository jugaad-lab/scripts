# Discord Activity Digest

Scans Discord channels for recent messages and flags unanswered @mentions. Deterministic data collection — no LLM needed.

## What It Does

- Pulls messages from configured channels within a time window
- Counts activity per channel and per author
- Identifies unanswered bot mentions that need attention
- Outputs structured JSON or human-readable summary

## Prerequisites

- Python 3.10+ (stdlib only — no pip dependencies)
- Discord bot token with message read permissions

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | Discord bot token for API access |
| `DISCORD_GUILD_ID` | ✅ | Discord server/guild ID to monitor |

## Configuration

Channel IDs are currently hardcoded in `CHANNEL_NAMES` dict. To monitor your own server, update this mapping with your channel IDs and names.

> **TODO:** Move channel config to a YAML/JSON file or environment variable for easier customization.

## Usage

```bash
export DISCORD_BOT_TOKEN="your_bot_token"
export DISCORD_GUILD_ID="123456789012345678"

# Human-readable output (last 24 hours)
python3 discord-activity-digest.py

# Custom time window
python3 discord-activity-digest.py --hours 12

# Machine-readable JSON
python3 discord-activity-digest.py --json
```

## Output (JSON mode)

```json
{
  "timestamp": "2026-02-17 08:00:00",
  "hours": 24,
  "total_messages": 47,
  "total_mentions": 2,
  "active_channels": [["general", 23], ["ai-agents", 15]],
  "unanswered_mentions": [
    {"id": "...", "author": "someone", "content": "@bot help me", "channel": "general"}
  ]
}
```

## Testing

```bash
python3 -m pytest test_discord_activity_digest.py -v
```
