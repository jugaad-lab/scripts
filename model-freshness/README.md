# Model Freshness Checker

Checks if models configured in OpenClaw are up-to-date with Anthropic's latest releases.

## Usage

```bash
# Basic check against default config (~/.openclaw/openclaw.json)
python3 model_freshness.py

# Custom config path
python3 model_freshness.py --config /path/to/openclaw.json

# Discord-formatted output
python3 model_freshness.py --discord
```

## How it works

1. Scrapes Anthropic's [models page](https://docs.anthropic.com/en/docs/about-claude/models) for the latest model aliases
2. Reads your OpenClaw config for configured model IDs
3. Compares and reports which are stale

No API keys required — uses public docs only.

Exit code: `0` if all current, `1` if any stale.
