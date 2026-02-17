# OpenClaw Automation Scripts

Deterministic automation scripts for OpenClaw cost optimization.

## Philosophy: Don't Be a Telephone Operator

**The core principle:** If you're paying for the most expensive reasoning model available (Opus, GPT-4, etc.), every token should go toward thinking that REQUIRES intelligence — strategy, accountability, pattern recognition, creative synthesis, pushing back. If a deterministic script can do it, it should.

The telephone operator connected lines — valuable work, but you don't need an AI agent for it.

**Why these scripts exist:** Move repeatable execution into $0 scripts, reserve agent tokens for judgment calls only.

**Concrete example:** A morning briefing that used to cost $2+ per run as a pure agent now runs a $0 orchestrator script first. The script collects data, filters noise, and determines actionability. Only when there's actually something worth reasoning about does it spawn an agent with pre-processed context.

**Result:** 80% of mornings cost $0 (script determines "all clear"), 20% cost the same but with much richer, pre-filtered data for the agent to work with.

**Scripts for execution, agents only for judgment.**

These tools handle the deterministic heavy lifting - data collection, filtering, and processing - without burning expensive LLM tokens. Agents are only invoked when human-level judgment is needed for synthesis, prioritization, or decision-making.

## Scripts

### gmail-promo-cleanup.py

Automatically trashes promotional emails from Gmail across multiple accounts to reduce inbox noise.

**What it does:**
- Searches for emails in `category:promotions` across configured accounts
- Batches them for efficient deletion (max 300 per account per run)
- Provides dry-run mode for safe testing
- Outputs structured JSON results for integration

**Required Environment Variables:**
- `GMAIL_ACCOUNTS` - Comma-separated list of Gmail addresses to process
- `GOG_KEYRING_PASSWORD` - Password for gog CLI keyring authentication

**Google Cloud Setup:**
You need a Google Cloud project with Gmail API enabled, OAuth consent screen configured, and credentials created via the `gog` CLI tool. Run `gog auth` first to set up OAuth credentials.

**Usage:**
```bash
export GMAIL_ACCOUNTS="user1@gmail.com,user2@gmail.com"
export GOG_KEYRING_PASSWORD="your_password"
python3 gmail-promo-cleanup.py [--dry-run] [--account EMAIL]
```

### discord-activity-digest.py

Collects recent message activity from Discord channels and identifies unanswered mentions.

**What it does:**
- Scans configured Discord channels for recent messages
- Counts activity per channel and author
- Identifies unanswered bot mentions that need attention
- Outputs structured summary for morning briefings

**Required Environment Variables:**
- `DISCORD_BOT_TOKEN` - Discord bot token for API access
- `DISCORD_GUILD_ID` - Discord server/guild ID to monitor

**Usage:**
```bash
export DISCORD_BOT_TOKEN="your_bot_token"
export DISCORD_GUILD_ID="123456789012345678"
python3 discord-activity-digest.py [--hours 24] [--json]
```

### morning-orchestrator.py

Coordinates the other scripts and determines if agent intervention is needed for morning briefings.

**What it does:**
- Runs promotional email cleanup
- Collects Discord activity digest
- Scans important emails across accounts
- Checks calendar events for today/tomorrow
- Determines actionability based on configured rules
- Exits with code 0 (actionable) or 2 (all clear) for cron integration

**Required Environment Variables:**
- `GMAIL_ACCOUNTS` - Comma-separated Gmail addresses 
- `CALENDAR_ACCOUNT` - Primary calendar account for event checking
- `GOG_KEYRING_PASSWORD` - Password for gog CLI keyring
- `DISCORD_BOT_TOKEN` - Discord bot token (used by sub-scripts)
- `DISCORD_GUILD_ID` - Discord server ID (used by sub-scripts)

**Optional Environment Variables:**
- `IMPORTANT_SENDERS` - Comma-separated list of additional sender patterns to prioritize

**Usage:**
```bash
export GMAIL_ACCOUNTS="user@gmail.com"
export CALENDAR_ACCOUNT="user@gmail.com" 
export GOG_KEYRING_PASSWORD="your_password"
python3 morning-orchestrator.py [--out /tmp/morning-data.json] [--dry-run]
```

## Setup

**Requirements:**
- Python 3.10+
- `gog` CLI tool installed and authenticated
- Discord bot token with appropriate permissions

**Installation:**
```bash
pip install pytest
```

**Testing:**
```bash
# Run unit tests only (no API calls)
python3 -m pytest test_*.py -k "not integration" -v

# Run integration tests (requires real credentials)
python3 -m pytest test_*.py -v
```

## License

MIT License