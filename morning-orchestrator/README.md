# Morning Orchestrator

Coordinates data collection scripts and determines if an AI agent should be spawned. The gatekeeper that saves you $2+ on quiet mornings.

## What It Does

1. Runs `gmail-promo-cleanup` to trash noise
2. Collects Discord activity digest
3. Scans important emails across accounts
4. Checks calendar for today/tomorrow
5. Evaluates actionability rules
6. **Exit 0** → actionable, data written for agent consumption
7. **Exit 2** → all clear, no agent needed ($0 cost)

## Prerequisites

- Python 3.10+
- [`gog`](https://github.com/openclaw/openclaw) CLI installed and authenticated
- Discord bot token with message read permissions
- Sibling scripts (`gmail-promo-cleanup.py`, `discord-activity-digest.py`) in parent directory

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GMAIL_ACCOUNTS` | ✅ | Comma-separated Gmail addresses |
| `CALENDAR_ACCOUNT` | ✅ | Primary calendar account |
| `GOG_KEYRING_PASSWORD` | ✅ | Password for `gog` CLI keyring |
| `DISCORD_BOT_TOKEN` | ✅ | Discord bot token (used by sub-scripts) |
| `DISCORD_GUILD_ID` | ✅ | Discord server ID (used by sub-scripts) |
| `IMPORTANT_SENDERS` | ❌ | Additional sender patterns to prioritize (comma-separated) |

## Usage

```bash
# Set all required env vars first, then:

# Dry run (collect data, don't write output)
python3 morning-orchestrator.py --dry-run

# Live run (writes to /tmp/morning-data.json by default)
python3 morning-orchestrator.py

# Custom output path
python3 morning-orchestrator.py --out /path/to/output.json
```

## Cron Integration

```bash
# In your cron job or OpenClaw cron:
python3 morning-orchestrator.py --out /tmp/morning-data.json
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    # Actionable — spawn agent with pre-processed data
    # Agent reads /tmp/morning-data.json and composes briefing
    echo "Spawning agent..."
elif [ $EXIT_CODE -eq 2 ]; then
    # All clear — zero tokens burned
    echo "All clear, skipping agent"
else
    # Error
    echo "Orchestrator failed"
fi
```

## Actionability Rules

The orchestrator triggers an agent when ANY of these are true:
- Important emails detected (banks, security alerts, key contacts)
- Calendar events scheduled for today
- Unanswered Discord @mentions
- It's a weekday (work priorities)

## Testing

```bash
python3 -m pytest test_morning_orchestrator.py -v
```
