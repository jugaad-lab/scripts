#!/usr/bin/env python3
"""Emit readable conversation text from Claude Code sessions since the last
extraction watermark.

Walks every .jsonl under ~/.claude/projects/<slug>/, pulls out human-readable
text (user messages + assistant text blocks, skipping tool_use/tool_result/
thinking/usage metadata), filters by timestamp, prints a compact plain-text
transcript to stdout, then advances the watermark.

First run: no watermark → seed to now-minus-48h so we don't replay the whole
backlog.

Usage:
    session-delta.py [--project SLUG] [--dry-run] [--max-entries N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PROJECT = "-Users-bunny-bunny-claude-bridge"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"
WATERMARK_FILE = Path.home() / ".cashew" / "extract-watermark.json"
BACKFILL_WINDOW = timedelta(hours=48)


def load_watermark() -> dict[str, str]:
    if not WATERMARK_FILE.exists():
        return {}
    try:
        return json.loads(WATERMARK_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def save_watermark(data: dict[str, str]) -> None:
    WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATERMARK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(WATERMARK_FILE)


def extract_text(entry: dict) -> str | None:
    kind = entry.get("type")
    msg = entry.get("message") or {}
    content = msg.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = (block.get("text") or "").strip()
            if t:
                parts.append(t)
        elif btype == "tool_result" and kind == "user":
            inner = block.get("content")
            if isinstance(inner, str):
                t = inner.strip()
                if t and len(t) < 2000:
                    parts.append(f"[tool-result] {t}")
    if not parts:
        return None
    return "\n".join(parts)


def iter_entries(project_dir: Path, cutoff: str):
    for path in sorted(project_dir.glob("*.jsonl")):
        try:
            with path.open() as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") not in ("user", "assistant"):
                        continue
                    ts = entry.get("timestamp")
                    if not ts or ts <= cutoff:
                        continue
                    text = extract_text(entry)
                    if not text:
                        continue
                    yield ts, entry.get("type"), text
        except OSError:
            continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-entries", type=int, default=500)
    args = ap.parse_args()

    project_dir = PROJECTS_ROOT / args.project
    if not project_dir.is_dir():
        print(f"project dir not found: {project_dir}", file=sys.stderr)
        return 1

    watermark = load_watermark()
    cutoff = watermark.get(args.project)
    if cutoff is None:
        cutoff = (datetime.now(timezone.utc) - BACKFILL_WINDOW).isoformat().replace("+00:00", "Z")

    emitted = 0
    newest = cutoff
    for ts, kind, text in iter_entries(project_dir, cutoff):
        if emitted >= args.max_entries:
            break
        role = "USER" if kind == "user" else "ASSISTANT"
        sys.stdout.write(f"--- {role} @ {ts} ---\n{text}\n\n")
        emitted += 1
        if ts > newest:
            newest = ts

    sys.stderr.write(f"session-delta: project={args.project} cutoff={cutoff} emitted={emitted} newest={newest}\n")

    if not args.dry_run and newest != cutoff:
        watermark[args.project] = newest
        save_watermark(watermark)

    return 0


if __name__ == "__main__":
    sys.exit(main())
