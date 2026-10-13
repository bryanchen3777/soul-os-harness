#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SOUL-MEM-001 Phase 0 — 真實 prompt baseline 量測（唯讀）

SOUL-MEM-001 原稿驗收標準 2 宣稱「Input Token >3,500 → <700，縮減 ≥5×」。
本腳本的唯一目的是量出真實數字，證明或推翻該前提。本腳本不做任何優化。

🔴 安全邊界（AGENTS.md 行程安全鐵律 / 資料紀律）：
  - 唯讀：本腳本只 open(mode='r')。不寫、不改名、不刪除 data/** 下任何檔案。
  - 不啟動伺服器、不綁任何 port、不發任何真實 HTTP 請求（LLM 成本 = 0）。
  - 不 import / 不呼叫 consciousness._fire_intent（會 publish 到 eventbus 觸發真實推理）。
  - 不殺任何行程。
  - 輸出只寫 logs/。

用法：
  .venv\\Scripts\\python.exe scripts\\measure_prompt_baseline.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PERSONAS_DIR = REPO / "personas"
DATA_ROOT = REPO / "data"
OUT_JSON = REPO / "logs" / "prompt_baseline_20261012.json"

# ── 量測單位 ────────────────────────────────────────────────
# repo 內部慣例是 CHARS_PER_TOKEN = 4（src/temporal/render.py:9、
# src/memory/sage/token_utils.py:16）。但 persona 是中文為主，
# 該係數對 CJK 會嚴重低估（約 1 CJK 字 ≈ 1 token，而非 1/4）。
# 因此雙軌報告，並以 cjk_aware 為主值。
CHARS_PER_TOKEN = 4


def est_tokens_cjk_aware(text: str) -> int:
    """CJK 每字約 1 token，其餘約 4 char / token。估算，非真實 tokenizer。"""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF      # CJK 統一表意
            or 0x3040 <= o <= 0x30FF   # 日文假名
            or 0xAC00 <= o <= 0xD7AF   # 韓文
            or 0x3000 <= o <= 0x303F   # CJK 標點
        ):
            cjk += 1
    other = len(text) - cjk
    return int(cjk + other / CHARS_PER_TOKEN)


def est_tokens_chars4(text: str) -> int:
    """repo 既有慣例口徑，保留供對照。"""
    return int(len(text) / CHARS_PER_TOKEN)


def measure(text: str) -> Dict[str, int]:
    return {
        "chars": len(text),
        "tok_cjk_aware": est_tokens_cjk_aware(text),
        "tok_chars4": est_tokens_chars4(text),
    }


def safe(fn, *a, **kw) -> Any:
    """任何量測失敗都不得中斷整份報告。"""
    try:
        return fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


# ── 1. persona 檔 ────────────────────────────────────────────
def measure_personas() -> List[Dict[str, Any]]:
    rows = []
    for f in sorted(PERSONAS_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = measure(text)
        rows.append({"agent_id": f.stem, "path": f"personas/{f.name}", **m})
    return sorted(rows, key=lambda r: r.get("chars", 0), reverse=True)


# ── 2. history 檔（proxy 實際讀的那幾個）───────────────────
def measure_history() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    conv = DATA_ROOT / "conversations"
    if conv.is_dir():
        files = []
        for f in conv.glob("*.json"):
            try:
                raw = f.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                files.append({"file": f.name, "error": str(exc)})
                continue
            entry: Dict[str, Any] = {"file": f.name, **measure(raw)}
            # 若為 messages 陣列，額外給條數
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    entry["turns"] = len(data)
                elif isinstance(data, dict):
                    for k in ("messages", "history"):
                        if isinstance(data.get(k), list):
                            entry["turns"] = len(data[k])
                            break
            except Exception:  # noqa: BLE001
                pass
            files.append(entry)
        out["conversations"] = sorted(
            files, key=lambda r: r.get("chars", 0), reverse=True
        )
    return out


# ── 3. proxy 的 read-side 投影區塊（真正的 Σt 投影層）──────
BLOCK_HELPERS = [
    ("_format_life_thread_lines", ()),
    ("_format_recent_inner_life", ()),
    ("_format_relationship_block", ()),
    ("_format_temporal_context", ()),
    ("_format_attachment_str", ()),
    ("_format_emergent_block", ()),
    ("_format_horizon_block", ()),
    ("_format_capability_block", ()),
]


def measure_blocks(sample_agents: List[str]) -> Dict[str, Any]:
    import src.llm.proxy as proxy

    result: Dict[str, Any] = {}
    for helper_name, _ in BLOCK_HELPERS:
        fn = getattr(proxy, helper_name, None)
        if fn is None:
            result[helper_name] = {"error": "helper 不存在"}
            continue
        per_agent: Dict[str, Any] = {}
        for aid in sample_agents:
            if helper_name == "_format_temporal_context":
                val = safe(fn, None)
                text = "" if not isinstance(val, str) else val
            elif helper_name == "_format_horizon_block":
                val = safe(fn, aid, "", "")
                text = "" if not isinstance(val, str) else val
            else:
                val = safe(fn, aid)
                if isinstance(val, list):
                    text = "\n".join(str(x) for x in val)
                elif isinstance(val, str):
                    text = val
                else:
                    text = ""
            if isinstance(val, dict) and "__error__" in val:
                per_agent[aid] = {"error": val["__error__"]}
            else:
                per_agent[aid] = measure(text)
        result[helper_name] = per_agent
    return result


# ── 4. 完整組裝（group / private）───────────────────────────
def _try_memory():
    try:
        from src.memory.store import MemoryStore
        return MemoryStore()
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"MemoryStore 建構失敗: {type(exc).__name__}: {exc}"}


def measure_assembled(sample_agents: List[str]) -> Dict[str, Any]:
    import src.llm.proxy as proxy

    memory = _try_memory()
    out: Dict[str, Any] = {"memory_backend": "ok" if not isinstance(memory, dict) else memory}

    for aid in sample_agents:
        soul_path = PERSONAS_DIR / f"{aid}.md"
        if not soul_path.exists():
            continue
        soul = soul_path.read_text(encoding="utf-8")
        per_path: Dict[str, Any] = {}

        for path_name, builder in (
            ("private", proxy._build_messages_private),
            ("group", proxy._build_messages_group),
        ):
            msgs = safe(
                builder,
                aid,
                soul,
                "[量測用假想輸入] 你現在在想什麼？",
                "",
                memory,
            )
            if not isinstance(msgs, list):
                per_path[path_name] = {"error": str(msgs)}
                continue
            roles: Dict[str, int] = {}
            total = 0
            for m in msgs:
                role = m.get("role", "?")
                roles[role] = roles.get(role, 0) + 1
                total += est_tokens_cjk_aware(m.get("content", ""))
            # system 區與 history 區分開算
            sys_tok = sum(
                est_tokens_cjk_aware(m.get("content", ""))
                for m in msgs
                if m.get("role") == "system"
            )
            hist_tok = sum(
                est_tokens_cjk_aware(m.get("content", ""))
                for m in msgs
                if m.get("role") != "system"
            )
            per_path[path_name] = {
                "n_messages": len(msgs),
                "role_counts": roles,
                "tok_cjk_aware_total": total,
                "tok_system": sys_tok,
                "tok_non_system": hist_tok,
                "tok_persona": est_tokens_cjk_aware(soul),
                "tok_payload_excl_persona": total - est_tokens_cjk_aware(soul),
            }
        out[aid] = per_path
    return out


def main() -> int:
    personas = measure_personas()
    agent_ids = [r["agent_id"] for r in personas]

    # 完整組裝量測的抽樣對象：最大的 3 個 persona + agent_mahiru。
    # agent_mahiru 是 REM-LEGACY-CLEANSE 的驗收對象（§7 的 legacy runaway turn
    # 汙染檔就是 bryan_agent_mahiru_private.json），必須列入否則驗收無從比對。
    sample: List[str] = agent_ids[:3]
    if "agent_mahiru" not in sample:
        sample.append("agent_mahiru")

    report: Dict[str, Any] = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(REPO),
        "safety": "唯讀量測；未啟動伺服器、未發真實請求、未呼叫 _fire_intent、未寫 data/",
        "token_unit": {
            "primary": "tok_cjk_aware (CJK 1字≈1token, 其餘 4char≈1token)",
            "secondary": "tok_chars4 (repo 既有 CHARS_PER_TOKEN=4 慣例)",
            "caveat": "本機 .venv 無 tiktoken，兩者皆為估算，非真實 tokenizer 計數",
        },
        "personas": personas,
        "history_files": measure_history(),
        "projection_blocks": measure_blocks(agent_ids[:4]),
        "assembled_messages": measure_assembled(sample),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── console 摘要 ──
    print("=" * 68)
    print("persona 檔案（最大的 12 份）")
    print("=" * 68)
    print(f"{'agent':<18}{'chars':>9}{'tok(cjk)':>11}{'tok(c/4)':>11}")
    for r in personas[:12]:
        print(f"{r['agent_id']:<18}{r['chars']:>9,}{r['tok_cjk_aware']:>11,}{r['tok_chars4']:>11,}")

    print()
    print("=" * 68)
    print("完整組裝後的 messages（每個區塊的實際 token）")
    print("=" * 68)
    for aid, paths in report["assembled_messages"].items():
        if aid in ("memory_backend",):
            continue
        for path_name, d in paths.items():
            if "error" in d:
                print(f"{aid}/{path_name}: ERROR {d['error']}")
                continue
            print(
                f"{aid:<18}{path_name:<9} total={d['tok_cjk_aware_total']:>7,} "
                f"| persona={d['tok_persona']:>7,} "
                f"| payload_excl_persona={d['tok_payload_excl_persona']:>6,} "
                f"| msgs={d['n_messages']}"
            )

    print()
    print(f"完整 JSON: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())