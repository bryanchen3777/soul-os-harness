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

import contextlib
import pytest
from unittest.mock import MagicMock, patch

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


def test_chrono_context_must_reach_prompt(tmp_path):
    """chrono / 時間偏置必須真實流向 LLM 的 system prompt（匯端 + 中段都有活路徑）。

    ⚠️ 2026-10-13 改寫第二道斷言的說明（不是無聲弱化）：

    原斷言是「SYSTEM_TICK 必須仍有活的訂閱者」。**本工單刻意不恢復那個訂閱。**
    M5.7-2（commit 95891e8）拿掉 EventType.SYSTEM_TICK 的訂閱，是為了避免
    proactive Agency 重複觸發，屬 Bry 的架構裁決；動它等於回退一項已生效的決策。
    源端 / `_on_tick` 死碼屬 SOUL-BUG-003 的範圍，本工單不碰。

    因此中段改走「發言當下現算」：`_fire_intent` 在 chrono_payload 缺 chrono_block
    時呼叫 `_build_chrono_block`（走既有 src/temporal 單一實作）。第二道斷言
    改成驗這個中段確實接上 —— 這才是本工單修好後應成立的契約。

    註：若日後有人恢復 SYSTEM_TICK 訂閱，這支測試不會因此失敗（那是 SOUL-BUG-003
    的裁示範圍，不是本工單的驗收條件）。
    """
    import inspect

    from src.llm import proxy as proxy_mod

    # 匯端斷言：live 的 prompt 組裝器必須真的接受 chrono context。
    for builder in (proxy_mod._build_messages_group, proxy_mod._build_messages_private):
        builder_params = inspect.signature(builder).parameters
        assert "chrono_context" in builder_params, (
            f"live 的 {builder.__name__} 沒有 chrono_context 參數 —— "
            "時間感知無處注入。這是 SOUL-BUG-004 的預期失敗。"
            f"（現有參數：{sorted(builder_params)}）"
        )

    # 中段斷言：_fire_intent 必須真的在發言當下現算 chrono 區塊（見上方說明）。
    import src.agent.consciousness as consciousness

    assert hasattr(consciousness.AgentConsciousness, "_build_chrono_block"), (
        "consciousness 沒有 _build_chrono_block —— 中段沒接上，"
        "四個活路徑只帶 draft，chrono_context 仍會是空字串。"
    )
    fire_intent_src = inspect.getsource(consciousness.AgentConsciousness._fire_intent)
    assert "_build_chrono_block" in fire_intent_src, (
        "_build_chrono_block 存在但 _fire_intent 沒呼叫它 —— 中段是半修復。"
    )


async def test_fire_intent_computes_chrono_block_when_absent(tmp_path, monkeypatch):
    """中段行為：chrono_payload 沒有 chrono_block 時，發言當下現算一次。

    這是 SOUL-BUG-004 的中段修復。四個活路徑（USER_MESSAGE 私聊 / 群聊 /
    AGENT_SPEAK 跟進）過去都只傳 {"draft": ...}，導致
    intent_payload["chrono_context"] 恆為 ""，全角色失去時間偏置。

    刻意不碰 SYSTEM_TICK 訂閱（M5.7-2 / 95891e8 的架構裁決，屬 SOUL-BUG-003）。
    """
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    import src.paths as paths_mod

    paths_mod.reset_data_root()

    import src.agent.consciousness as consciousness

    published = []

    class _FakeBus:
        async def publish(self, event):
            published.append(event)

    class _Probe(consciousness.AgentConsciousness):
        def __init__(self):
            self.agent_id = "agent_probe"
            self.bus = _FakeBus()
            self._pending = False
            self.state = MagicMock()

        def _should_speak(self, elapsed_mins):
            return True

        def _build_intent_payload(self, reason, elapsed_mins):
            return {"text": "probe"}

    agent = _Probe()
    await agent._fire_intent(
        reason="silence_timeout",
        elapsed_mins=42.0,
        chrono_payload={"draft": "\u4f60\u9084\u5728\u55ce"},
    )

    assert len(published) == 1
    payload = published[0].payload
    chrono_context = payload.get("chrono_context", "")
    assert chrono_context, (
        "chrono_context \u4ecd\u662f\u7a7a\u5b57\u4e32 \u2014\u2014 \u4e2d\u6bb5\u6c92\u63a5\u4e0a\u3002"
        "\u56db\u500b\u6d3b\u8def\u5f91\u53ea\u5e36 draft\uff0c_fire_intent \u5fc5\u9808\u5728\u767c\u8a71\u7576\u4e0b\u73fe\u7b97 chrono \u5340\u584a\u3002"
    )
    assert "[CHRONO_SOCIAL_CONTEXT v2.2]" in chrono_context
    # draft \u4ecd\u7167\u65e7\u900f\u50b3\uff08\u9019\u500b\u4e2d\u6bb5\u4fee\u5fa9\u4e0d\u5f97\u5f71\u97ff\u65e2\u6709 draft \u50b3\u905e\uff09
    assert payload.get("draft") == "\u4f60\u9084\u5728\u55ce"

    paths_mod.reset_data_root()


def _fake_memory():
    mem = MagicMock()
    mem.get_group_history.return_value = []
    mem.get_recent_with_meta.return_value = []
    mem.conn.execute.return_value.fetchall.return_value = []
    return mem


@contextlib.contextmanager
def _hermetic_proxy():
    """把 prompt \u7d44\u88c5\u5668\u6240\u6709\u8b80 data/ \u7684 helper \u63db\u6210\u7a7a\u5b57\u4e32\uff0c\u8b93\u6e2c\u8a66\u5b8c\u5168 hermetic\u3002"""
    from src.llm import proxy

    names = (
        "_format_horizon_block",
        "_format_capability_block",
        "_format_emergent_block",
        "_format_relationship_block",
        "_format_relational_perception_block",
        "_format_recent_inner_life",
        "_format_attachment_str",
        "_format_temporal_anchor",
        "_load_bry_recent",
        "_load_self_recent",
    )
    with contextlib.ExitStack() as stack:
        for name in names:
            stack.enter_context(patch.object(proxy, name, return_value=""))
        yield proxy


_CHRONO_FIXTURE = (
    "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
    "time_period=afternoon\n"
    "silence=2.0h\n"
    "arrival_deviation=normal\n"
    "vulnerability_window=False\n"
    "carryover_worry=0.00\n"
    "attachment_heat=0.00\n"
    "reaction_bias=neutral\n"
    "temporal_salience=low\n"
    "expression_mode=implicit\n"
    "[/CHRONO_SOCIAL_CONTEXT]"
)


def test_chrono_context_reaches_system_prompt(tmp_path, monkeypatch):
    """\u6b63\u5411\uff1achrono_context \u975e\u7a7a\u6642\uff0c\u7fa4\u804a / \u79c1\u804a\u5169\u500b live builder \u90fd\u5fc5\u9808\u6ce8\u5165 system prompt\u3002"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    import src.paths as paths_mod

    paths_mod.reset_data_root()

    with _hermetic_proxy() as proxy:
        common = dict(
            agent_id="agent_probe",
            soul="SOUL TEXT",
            current_input="",
            memory_context="\u8a18\u61b6\u5167\u5bb9",
            memory=_fake_memory(),
            current_time="2026-10-13 15:00 \u4e0b\u5348 (EDT)",
        )
        group_msgs = proxy._build_messages_group(chrono_context=_CHRONO_FIXTURE, **common)
        private_msgs = proxy._build_messages_private(chrono_context=_CHRONO_FIXTURE, **common)

    for label, messages in (("group", group_msgs), ("private", private_msgs)):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        assert "[CHRONO_SOCIAL_CONTEXT v2.2]" in system, (
            f"{label} builder \u6c92\u628a chrono_context \u6ce8\u9032 system message \u2014\u2014 "
            "\u9019\u662f SOUL-BUG-004 \u7684\u9810\u671f\u5931\u6557\uff08\u532f\u7aef\u65b7\u93c8\uff09\u3002"
        )
        assert "time_period=afternoon" in system
        # \u6ce8\u5165\u4f4d\u7f6e\u5951\u7d04\uff1amemory_context \u4e4b\u5f8c\u3001\u5c0d\u8a71\u6b77\u53f2\u4e4b\u524d
        # \uff08\u5c0d\u9f4a\u5df2\u522a\u9664\u7684 PromptContext \u76f8\u5c0d\u9806\u5e8f\uff09
        assert system.index("\u4f60\u8a18\u5f97\u4ee5\u4e0b\u9019\u4e9b\u4e8b\u60c5") < system.index(
            "[CHRONO_SOCIAL_CONTEXT v2.2]"
        ), f"{label}: chrono \u5340\u584a\u5fc5\u9808\u572d\u5728\u8a18\u61b6\u6bb5\u843d\u4e4b\u5f8c"

    paths_mod.reset_data_root()


def test_empty_chrono_context_injects_nothing(tmp_path, monkeypatch):
    """\u8ca0\u5411\uff1a\u9810\u8a2d chrono_context="" \u6642\u4e0d\u5f97\u591a\u51fa\u4efb\u4f55\u7a7a\u767d/\u54e8\u5175\u6bb5\u843d\uff08\u9632\u300c\u63a5\u7dda\u4f46\u6ce8\u5165\u7a7a\u5b57\u4e32\u300d\uff09\u3002"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    import src.paths as paths_mod

    paths_mod.reset_data_root()

    with _hermetic_proxy() as proxy:
        common = dict(
            agent_id="agent_probe",
            soul="SOUL TEXT",
            current_input="",
            memory_context="\u8a18\u61b6\u5167\u5bb9",
            memory=_fake_memory(),
            current_time="2026-10-13 15:00 \u4e0b\u5348 (EDT)",
        )
        default_group = proxy._build_messages_group(**common)
        explicit_empty_group = proxy._build_messages_group(chrono_context="", **common)
        default_private = proxy._build_messages_private(**common)
        explicit_empty_private = proxy._build_messages_private(chrono_context="", **common)

    assert default_group == explicit_empty_group, (
        "\u9810\u8a2d\u53c3\u6578\u8207\u986f\u5f0f\u50b3\u7a7a\u5b57\u4e32\u7522\u51fa\u4e0d\u540c prompt \u2014\u2014 \u6709\u5077\u5075\u6ce8\u5165\u4e1c\u897f\uff08\u534a\u4fee\u5fa9\uff09\u3002"
    )
    assert default_private == explicit_empty_private
    for messages in (default_group, default_private):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        assert "[CHRONO_SOCIAL_CONTEXT" not in system
        assert not system.endswith("\n"), "\u4e0d\u5f97\u591a\u51fa\u7a7a\u884c\u5c3e\u5df4"

    paths_mod.reset_data_root()