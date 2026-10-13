# -*- coding: utf-8 -*-
"""
SOUL-BUG 看門狗（Bry 拍板 2026-10-13）：以 `xfail(strict=True)` 標記已知缺陷。

為什麼用 `xfail(strict=True)` 而不是 `xfail` 或直接留紅：
- 缺陷存在時報 **XFAIL**，歸屬寫在 reason 裡，**不污染日常開發信號**。
- 未來有人修好缺陷時 pytest 立刻噴 **XPASS(strict 視為 FAILED)**，
  **強迫修復者顯式移除 xfail、把這支測試轉正為常態回歸**。
  ⇒ 杜絕「修好了但沒人知道／沒有驗證」——這正是 SOUL-BUG-001 差點發生的情形。

---
# SOUL-BUG-001 — VALUE-DEDUP-A-1 連帶丟棄證據邊，EL-DD-2 閘門結構性不可達
---
根因: `src/inner_life/elevation_adapter.py:384-392`（commit `fb7ab63`）對**所有**
node_type 做 `(agent_id, node_type, content_hash)` 冪等去重，且當節點被判定重複時
**連帶丟棄其全部證據邊**:
    if key in existing:
        skipped_node_ids.add(rec.get("node_id")); continue
    ...
    if skipped_node_ids:
        edge_records = [e for e in edge_records
                        if e.get("node_id") not in skipped_node_ids]

而 `_event_content`（`:128-139`）由 `trigger_type + provenance.extras` 推導 pattern
內容，生產事件的 `extras` **近乎恆定**（`scripts/run_server.py:1808` `extras={"slot": slot}`；
`:1669-1672` `extras={target_agent_id, all_agents_count}`）⇒ 相同內容是常態而非例外。

⚠️ 本測試刻意使用**空的 extras**，也就是生產實況。
   既有 `test_elevate_mechanism.py` 的 `_make_distinct_events` 刻意給每條事件不同的
   extras 以繞開此缺陷——那個繞法本身就是這個缺陷的藏身處。

---
# SOUL-BUG-004 — chrono 區塊雙端切斷，全角色時間感知喪失
---
根因（兩端皆斷）:
  (a) 源端: 唯一會消費 `SYSTEM_TICK` 的 `consciousness._on_tick`（`:341`）因
      `consciousness.py:155` 被 M5.7-2（`95891e8`）註解而不可達
      （實測 `dispatch system_tick -> []`）；`:171` 為不可達死碼。
  (b) 匯端: `PromptContext.chrono_context`（`proxy.py:1568`）與其注入
      （`proxy.py:1596-1598`）是**死碼，0 處實例化**；
      repo 自己的 `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md:128` 已記錄匯端為死碼。
⇒ chrono / 時間偏置永遠到不了任何 LLM system prompt，且**生產日誌完全無聲**。
"""

from pathlib import Path
import json

import pytest

from src.inner_life import (
    InnerLifeWriter,
    Provenance,
    TRIGGER_TYPE_DIARY_NIGHT,
)
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    elevate_matured_patterns,
    run_elevation,
)
from src.inner_life.emergent_projection import load_elevation_nodes

_SOUL_TYPES = {"belief", "value", "trait", "essence"}


def _prod_shaped_event(writer, trigger_type, actor_id):
    """造一條**生產形狀**事件：extras 不帶序號（與 run_server.py 的寫法一致）。

    刻意不做 `_make_distinct_events` 那種「每條帶不同 extras」的加工——那正是
    把本缺陷藏起來的測試慣例。生产上 `extras={"slot": slot}` 之類的值是重複的。
    """
    return writer.create_event(
        provenance=Provenance(
            trigger_type=trigger_type,
            actor_id=actor_id,
            source_system="narrative",
            extras={},  # ← 生產形狀：內容必然與同 trigger 的其他事件重複
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# SOUL-BUG-001 看門狗
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SOUL-BUG-001: _persist_result (elevation_adapter.py:384-392, commit "
        "fb7ab63) dedups by (agent_id, node_type, content_hash) for ALL node "
        "types AND drops the skipped node's evidence edges. Production events "
        "carry near-constant extras, so repeated patterns are deduped and "
        "independent evidence can never reach 2 → the EL-DD-2 elevation gate "
        "is structurally unreachable for diary:night / dream:dream."
    ),
)
def test_elevation_pattern_evidence_accumulation_must_reach_two(tmp_path):
    """兩條**生產形狀**（extras 相同）的 diary 事件，獨立證據必須累積到 ≥2 並觸發昇降。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"

    for _ in range(2):
        run_elevation(
            _prod_shaped_event(writer, TRIGGER_TYPE_DIARY_NIGHT, "agent_rem"),
            [],
            store_dir=store_dir,
        )

    # 診斷訊息：若失敗，這兩行會直接指出是「節點沒落盤」還是「邊被丟棄」。
    persisted = load_elevation_nodes(store_dir)
    patterns = [n for n in persisted if n.get("node_type") == "pattern"]
    edges_path = Path(store_dir) / EDGES_FILENAME
    edges = (
        [json.loads(l) for l in edges_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if edges_path.exists()
        else []
    )

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert elevated, (
        f"2 條生產形狀事件未觸發昇降 —— 獨立證據未達 2。"
        f"已落盤 pattern 數={len(patterns)}（預期 2，但去重可能只留 1）；"
        f"已落盤證據邊數={len(edges)}（預期 ≥2，去重可能連邊一起丟棄）。"
        f"這是 SOUL-BUG-001 的預期失敗。"
    )
    assert any(
        getattr(n, "node_type", (n.get("node_type") if isinstance(n, dict) else None)) in _SOUL_TYPES
        for n in elevated
    ), f"昇降結果應含靈魂結構節點，實得：{elevated}"


# ─────────────────────────────────────────────────────────────────────
# SOUL-BUG-004 看門狗
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SOUL-BUG-004: chrono block is severed at BOTH ends. Source: "
        "consciousness.py:155 unsubscribes SYSTEM_TICK (M5.7-2 / 95891e8), "
        "making _on_tick (:341) unreachable and :171 dead code. Sink: "
        "PromptContext.chrono_context (proxy.py:1568) and its injection "
        "(proxy.py:1596-1598) are dead code with 0 instantiations. Time "
        "perception never reaches any LLM prompt, and production logs stay "
        "silent because the computation still runs and is merely discarded."
    ),
)
def test_chrono_context_must_reach_prompt(tmp_path):
    """chrono / 時間偏置必須真實流向 LLM 的 system prompt（匯端存在活的注入路徑）。"""
    import inspect

    from src.llm import proxy as proxy_mod

    # 匯端斷言：live 的 prompt 組裝器必須真的接受並注入 chrono context。
    builder_params = inspect.signature(proxy_mod._build_messages_group).parameters
    assert "chrono_context" in builder_params, (
        "live 的 _build_messages_group 沒有 chrono_context 參數 —— "
        "時間感知無處注入。這是 SOUL-BUG-004 的預期失敗。"
        f"（現有參數：{sorted(builder_params)}）"
    )

    # 源端斷言：SYSTEM_TICK 必須仍有活的訂閱者。
    import src.agent.consciousness as consciousness

    src_text = Path(consciousness.__file__).read_text(encoding="utf-8")
    live_tick_subscription = (
        "EventType.SYSTEM_TICK" in src_text
        and not all(
            ln.strip().startswith("#") or ln.strip().startswith('"')
            for ln in src_text.splitlines()
            if "EventType.SYSTEM_TICK" in ln
        )
    )
    assert live_tick_subscription, (
        "沒有任何活程式碼訂閱 EventType.SYSTEM_TICK（僅存在註解字串）—— "
        "chrono payload 無人消費。這是 SOUL-BUG-004 的預期失敗。"
    )