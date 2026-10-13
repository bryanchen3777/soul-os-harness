#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
REM-LEGACY-CLEANSE 驗收 — agent_mahiru 私聊路徑 A/B 對照

方法: 同一份 prompt 組裝路徑跑兩次，唯一差異是
  BEFORE: 把 src.llm.proxy.sanitize_legacy_runaway_turn 換成 identity（即修復前）
  AFTER : 用真實淨化器（修復後）
兩次都唯讀、不寫 data/、不發任何 HTTP 請求。

⚠️ 這是量測用的 monkeypatch，只在此腳本行程內有效，不改動 repo 任何檔案。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import src.llm.proxy as proxy
from src.text_guard import sanitize_legacy_runaway_turn as REAL_SANITIZER

sys.path.insert(0, str(REPO / "scripts"))
from measure_prompt_baseline import est_tokens_cjk_aware  # noqa: E402

AGENT = "agent_mahiru"
SOUL = (REPO / "personas" / f"{AGENT}.md").read_text(encoding="utf-8")


def build() -> dict:
    from src.memory.store import MemoryStore
    msgs = proxy._build_messages_private(
        AGENT, SOUL, "[驗收用假想輸入]", "", MemoryStore()
    )
    total = sum(est_tokens_cjk_aware(m.get("content", "")) for m in msgs)
    return {
        "total": total,
        "persona": est_tokens_cjk_aware(SOUL),
        "n_msgs": len(msgs),
    }


# BEFORE: 停用淨化器
proxy.sanitize_legacy_runaway_turn = lambda text: text
before = build()

# AFTER: 還原真實淨化器
proxy.sanitize_legacy_runaway_turn = REAL_SANITIZER
after = build()

print("=" * 66)
print(f"REM-LEGACY-CLEANSE 驗收 — {AGENT} 私聊路徑")
print("=" * 66)
print(f"{'':10}{'total':>10}{'persona':>10}{'payload_excl_persona':>22}{'msgs':>7}")
for label, r in (("BEFORE", before), ("AFTER", after)):
    print(f"{label:10}{r['total']:>10,}{r['persona']:>10,}"
          f"{r['total'] - r['persona']:>22,}{r['n_msgs']:>7}")

saved = before["total"] - after["total"]
print()
print(f"回收 token / 輪 : {saved:,}")
print(f"降幅             : {saved / before['total']:.1%}")
print()
print("對照基準（Bry 驗收標準：正常中位數 800–1,000 tokens）:")
b_pay = before["total"] - before["persona"]
a_pay = after["total"] - after["persona"]
print(f"  payload BEFORE : {b_pay:,}")
print(f"  payload AFTER  : {a_pay:,}")
print(f"  其他 agent 參考: yua 1,295 / ruka 1,597 / aoi 909")