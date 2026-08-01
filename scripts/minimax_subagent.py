#!/usr/bin/env python3
"""Small MiniMax-M3 subagent CLI.

Reads MINIMAX_API_KEY from soul-os-harness/.env and calls MiniMax through the
Anthropic-compatible Messages API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_BASE_URL = "https://api.minimax.io/anthropic"
DEFAULT_MODEL = "MiniMax-M3"
SESSION_DIR = REPO_ROOT / "data" / "minimax_sessions"
DEFAULT_SYSTEM = (
    "You are minimax, a concise coding and research subagent working for Codex. "
    "Return useful, direct results. State uncertainty clearly. Do not reveal secrets."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minimax",
        description="Send a task to the local MiniMax-M3 subagent.",
    )
    parser.add_argument("task", nargs="*", help="Task text. Reads stdin when omitted.")
    parser.add_argument("--system", default=DEFAULT_SYSTEM, help="System prompt.")
    parser.add_argument("--model", default=os.getenv("MINIMAX_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.getenv("MINIMAX_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--session", help="Persist and reuse a named conversation.")
    parser.add_argument("--reset", action="store_true", help="Clear the named session before sending.")
    parser.add_argument("--history", type=int, default=30, help="Max previous messages to send.")
    parser.add_argument("--show-history", action="store_true", help="Print a named session transcript.")
    parser.add_argument("--list-sessions", action="store_true", help="List saved MiniMax sessions.")
    parser.add_argument(
        "--thinking",
        choices=("default", "adaptive", "disabled"),
        default="disabled",
        help="MiniMax-M3 thinking mode.",
    )
    parser.add_argument("--json", action="store_true", help="Print response JSON envelope.")
    return parser


def _read_task(args: argparse.Namespace) -> str:
    if args.task:
        return " ".join(args.task).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _messages_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    return f"{base}/v1/messages"


def _extract_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks).strip()


def _safe_session_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe.strip("._-") or "default"


def _session_path(name: str) -> Path:
    return SESSION_DIR / f"{_safe_session_name(name)}.json"


def _load_session(name: str) -> dict[str, Any]:
    path = _session_path(name)
    if not path.exists():
        return {"name": _safe_session_name(name), "messages": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"name": _safe_session_name(name), "messages": []}
    if not isinstance(data, dict):
        return {"name": _safe_session_name(name), "messages": []}
    data.setdefault("name", _safe_session_name(name))
    data.setdefault("messages", [])
    return data


def _save_session(name: str, session: dict[str, Any]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    _session_path(name).write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _api_message(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _session_messages(session: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = session.get("messages") or []
    if limit > 0:
        rows = rows[-limit:]
    messages = []
    for row in rows:
        role = row.get("role")
        text = row.get("text")
        if role in {"user", "assistant"} and isinstance(text, str) and text:
            messages.append(_api_message(role, text))
    return messages


def _list_sessions() -> int:
    if not SESSION_DIR.exists():
        return 0
    for path in sorted(SESSION_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(path.stem)
            continue
        count = len(data.get("messages") or [])
        updated = data.get("updated_at", "")
        print(f"{path.stem}\t{count} messages\t{updated}")
    return 0


def _show_history(name: str) -> int:
    session = _load_session(name)
    for row in session.get("messages") or []:
        role = row.get("role", "?")
        text = str(row.get("text", ""))
        print(f"{role}: {text}")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list_sessions:
        return _list_sessions()

    if args.show_history:
        if not args.session:
            parser.error("--show-history requires --session")
        return _show_history(args.session)

    load_dotenv(args.env_file)
    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        parser.error(f"MINIMAX_API_KEY not found. Checked env and {args.env_file}")

    task = _read_task(args)
    if not task:
        parser.error("Provide a task argument or pipe task text on stdin.")

    session: dict[str, Any] | None = None
    messages = [_api_message("user", task)]
    if args.session:
        if args.reset:
            session = {"name": _safe_session_name(args.session), "messages": []}
            _save_session(args.session, session)
        session = _load_session(args.session)
        messages = _session_messages(session, args.history) + messages

    body: dict[str, Any] = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "system": args.system,
        "messages": messages,
    }
    if args.thinking != "default":
        body["thinking"] = {"type": args.thinking}

    try:
        with httpx.Client(timeout=180.0) as client:
            response = client.post(
                _messages_url(args.base_url),
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            f"MiniMax HTTP {exc.response.status_code}: {exc.response.text}",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"MiniMax request failed: {exc}", file=sys.stderr)
        return 1

    data = response.json()
    text = _extract_text(data)
    if args.session and session is not None:
        session.setdefault("messages", [])
        session["messages"].append({"role": "user", "text": task})
        session["messages"].append({"role": "assistant", "text": text})
        _save_session(args.session, session)
    if args.json:
        print(json.dumps({"text": text, "raw": data}, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
