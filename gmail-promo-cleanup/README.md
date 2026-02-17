# Gmail Promo Cleanup

Automatically trashes promotional emails from Gmail across multiple accounts. Zero LLM tokens — pure deterministic execution.

## What It Does

- Searches `category:promotions` in inbox across all configured Gmail accounts
- Batches deletions efficiently (max 300 per account per run)
- Dry-run mode for safe testing
- Structured JSON output for pipeline integration

## Prerequisites

- Python 3.10+
- [`gog`](https://github.com/openclaw/openclaw) CLI installed and authenticated
- Google Cloud project with Gmail API enabled

### Google Cloud Setup

1. Create a Google Cloud project
2. Enable the Gmail API
3. Configure OAuth consent screen
4. Run `gog auth` to set up credentials

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GMAIL_ACCOUNTS` | ✅ | Comma-separated Gmail addresses to process |
| `GOG_KEYRING_PASSWORD` | ✅ | Password for `gog` CLI keyring authentication |

## Usage

```bash
export GMAIL_ACCOUNTS="user1@gmail.com,user2@gmail.com"
export GOG_KEYRING_PASSWORD="your_password"

# Dry run first
python3 gmail-promo-cleanup.py --dry-run

# Live run
python3 gmail-promo-cleanup.py

# Single account only
python3 gmail-promo-cleanup.py --account user1@gmail.com
```

## Output

Human-readable summary + a `JSON:` line for machine consumption:

```json
{"timestamp": "2026-02-17 08:00:00", "total": 42, "accounts": {"user1@gmail.com": 30, "user2@gmail.com": 12}}
```

## Testing

```bash
python3 -m pytest test_gmail_promo_cleanup.py -v
```
