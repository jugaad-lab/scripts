# Scripts

Deterministic automation scripts for [OpenClaw](https://github.com/openclaw/openclaw) agents.

## Philosophy: Don't Be a Telephone Operator

If you're paying for the most expensive reasoning model available, every token should go toward thinking that *requires* intelligence — strategy, accountability, pattern recognition, creative synthesis, pushing back. If a deterministic script can do it, it should.

The telephone operator connected lines — valuable work, but you don't need an AI agent for it.

**Scripts for execution, agents only for judgment.**

**The pattern:** Run a $0 script first. Collect data, filter noise, check actionability. Only spawn an agent when there's actually something worth reasoning about.

**Result:** 80% of runs cost $0 (script determines "all clear"). The other 20% cost the same but with much richer, pre-filtered context for the agent.

## Scripts

| Script | Description |
|---|---|
| [`gmail-promo-cleanup/`](gmail-promo-cleanup/) | Trash promotional emails across Gmail accounts |
| [`discord-activity-digest/`](discord-activity-digest/) | Scan Discord channels, flag unanswered mentions |
| [`morning-orchestrator/`](morning-orchestrator/) | Coordinate scripts, gate agent spawning on actionability |

Each script has its own directory with a README, source, and tests.

## Contributing

New scripts welcome. Requirements:

1. **Own directory** with its own `README.md`
2. **Environment-driven config** — no hardcoded credentials or account-specific values
3. **Dry-run mode** — safe to test without side effects
4. **Structured output** — JSON for machine consumption
5. **Tests** — at minimum, unit tests that don't hit external APIs
6. **Zero LLM dependency** — if it needs an agent, it's not a script

## License

MIT
