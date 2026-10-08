#!/usr/bin/env python3
"""
Run the REAL triage_bugs workflow using Claude (Anthropic API key) instead of
Gemini - isolated test, does not touch the main app.

Reuses packages.workflows.triage.run_triage_bugs_workflow() and the real
JiraClient/ConfluenceClient completely unmodified - only the LLM client is
swapped (AnthropicClient in this folder, duck-typed to match what the
workflow expects from its `gemini_client` argument: an `.assess_bug()`
coroutine). No file outside test_claude/ is imported for writing, no file
outside test_claude/ is modified by running this script.

Setup:
    Add to the project's .env (same file make dev / scripts/*.py already
    read - the main app ignores unrecognized keys, so this is safe to add):
        ANTHROPIC_API_KEY=sk-ant-...
        ANTHROPIC_MODEL=claude-sonnet-5   # optional, this is the default

Usage:
    PYTHONPATH=$(pwd) venv/bin/python test_claude/triage_via_claude.py
    PYTHONPATH=$(pwd) venv/bin/python test_claude/triage_via_claude.py --jql "..." --max-results 5

Always dry-run (read-only against Jira) - this script never writes to Jira,
regardless of flags, since it's only meant to compare Claude's assessments
against what the real Gemini-backed workflow would produce.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

TEST_CLAUDE_DIR = Path(__file__).parent
PROJECT_ROOT = TEST_CLAUDE_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TEST_CLAUDE_DIR))

from dotenv import load_dotenv

from anthropic_client import AnthropicClient
from packages.integrations.confluence import ConfluenceClient
from packages.integrations.jira import JiraClient
from packages.workflows import triage

DATA_DIR = TEST_CLAUDE_DIR / "data"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run triage_bugs via Claude instead of Gemini (test only).")
    parser.add_argument("--jql", default=triage.DEFAULT_BUG_JQL, help="JQL query selecting bug issues to triage")
    parser.add_argument("--max-results", type=int, default=5, help="Maximum bugs to process (small by default for a quick test)")
    parser.add_argument("--batch-delay-seconds", type=int, default=5, help="Seconds to wait between Claude API calls")
    parser.add_argument("--output", help="Path to save the JSON result (default: test_claude/data/triage_result_claude.json)")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_api_key:
        print("[!] ANTHROPIC_API_KEY не задан в .env. Добавьте строку ANTHROPIC_API_KEY=sk-ant-... и повторите.")
        sys.exit(1)

    claude_client = AnthropicClient({
        "api_key": anthropic_api_key,
        "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
    })
    jira_client = JiraClient({
        "base_url": os.getenv("JIRA_BASE_URL"),
        "email": os.getenv("JIRA_EMAIL"),
        "api_token": os.getenv("JIRA_API_TOKEN"),
    })
    confluence_client = ConfluenceClient({
        "base_url": os.getenv("CONFLUENCE_BASE_URL"),
        "space": os.getenv("CONFLUENCE_SPACE"),
        "email": os.getenv("CONFLUENCE_EMAIL"),
        "api_token": os.getenv("CONFLUENCE_API_TOKEN"),
    })

    for name, client in (("Claude", claude_client), ("Jira", jira_client), ("Confluence", confluence_client)):
        ok, err = await client.test_connection()
        if not ok:
            print(f"[!] Не удалось подключиться к {name}: {err}")
            sys.exit(1)
        print(f"[OK] Подключение к {name} проверено.")

    print(f"\n[*] Запускаю triage_bugs через Claude ({claude_client.model}), только dry-run (Jira не изменяется)...\n")

    result = await triage.run_triage_bugs_workflow(
        jira_client=jira_client,
        confluence_client=confluence_client,
        gemini_client=claude_client,  # duck-typed: only .assess_bug() is called on this
        jql=args.jql,
        max_results=args.max_results,
        apply=False,  # this script is a comparison tool only - never writes to Jira
        add_comment=False,
        batch_delay_seconds=args.batch_delay_seconds,
        log_fn=lambda level, message: print(f"[{level}] {message}"),
    )

    output_path = Path(args.output) if args.output else DATA_DIR / "triage_result_claude.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = result.get("summary", {})
    print("\n=== SUMMARY (Claude) ===")
    print(
        f"Fetched: {summary.get('bugs_fetched', 0)} | Would-triage: {summary.get('bugs_triaged', 0)} | "
        f"Not real bug: {summary.get('skipped_not_real_bug', 0)} | Errors: {summary.get('errors', 0)}"
    )
    for row in result.get("per_issue_results", []):
        if row.get("severity"):
            print(f"  • {row['key']}: real_bug={row.get('is_real_bug')}, severity={row.get('severity')}, "
                  f"impact={row.get('impact')}, priority={row.get('priority')}")
    print(f"\nРезультат сохранён: {output_path}")
    print("[i] Это отдельный тестовый прогон — сравните с реальными Gemini-результатами в /ui/runs, если нужно.")


if __name__ == "__main__":
    asyncio.run(main())
