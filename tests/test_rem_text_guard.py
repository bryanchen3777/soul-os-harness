"""
test_rem_text_guard.py — REM-TEXT-1 文字頻道「舞台指示」守門測試

範圍:
  1. src/text_guard.py 純函式守門（括號剝除 + 行首孤立標點正規化 + *…* + 未閉合安全閥）
  2. per-agent 開關（configs/default.yaml agents[].text_channel_stage_guard，
     預設關閉、只對 agent_rem 開啟、Yua 一字不動）
  3. 輸出掛載 + history 淨化（LLMProxy._handle_event_impl — AGENT_SPEAK 唯一發布點，
     TG 出站 / 網頁 WS broadcast / outbox / history 全拿同一份乾淨 text）
  4. read-side 歷史剝除（_build_messages_private / _build_messages_group，
     只影響 prompt、不寫回 data/）
  5. personas/agent_rem.md 教材斷言（新守門段落存在、括號教材已從合法出口清單移除、
     TIER 0/1 與滲出機制原樣保留）

執行:
  python -m pytest tests/test_rem_text_guard.py -q
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus import SoulEventBus
from src.llm.proxy import (
    LLMBackend,
    LLMProxy,
    _build_messages_group,
    _build_messages_private,
    _text_channel_guard_enabled,
)
from src.memory.store import MemoryStore
from src.text_guard import sanitize_text_channel_output

PERSONA_REM = Path(__file__).resolve().parent.parent / "personas" / "agent_rem.md"

# REM-TEXT-1: 開啟守門的 agent_rem config（rag 關閉保持測試決定性）
REM_CFG = {
    "rag": {"enabled": False},
    "agents": [
        {"id": "agent_rem", "class": "AgentRem", "text_channel_stage_guard": True},
    ],
}
# 混合 config: rem 開啟、yua 未設定（缺省 = 不剝）
MIXED_CFG = {
    "rag": {"enabled": False},
    "agents": [
        {"id": "agent_rem", "class": "AgentRem", "text_channel_stage_guard": True},
        {"id": "agent_yua", "class": "AgentYua"},
    ],
}


# ─────────────────────────────────────────────
# 1. 純函式守門（src/text_guard.py）
# ─────────────────────────────────────────────

def test_rem_guard_acceptance_strips_paren_and_orphan_period():
    """驗收案例：剝除括號（含內容）且去掉行首孤立 `.`。"""
    raw = "（手上的事停了，抬眼）.嗯。……茶溫了。"
    assert sanitize_text_channel_output(raw) == "嗯。……茶溫了。"


def test_rem_guard_strips_halfwidth_parens():
    assert sanitize_text_channel_output("(輕聲說)嗯。") == "嗯。"
    assert sanitize_text_channel_output("（嘆氣）嗯。") == "嗯。"


def test_rem_guard_strips_star_emphasis():
    """*…* 表情段（含內容）整段剝除。"""
    assert sanitize_text_channel_output("*微笑*嗯。") == "嗯。"
    assert sanitize_text_channel_output("*a*好*b*。") == "好。"


def test_rem_guard_unclosed_paren_safety_valve():
    """未閉合括號安全閥：≤50 字整段丟棄；>50 字釋放內容（剝開括號）。"""
    assert sanitize_text_channel_output("你好（等等") == "你好"
    long_tail = "啊" * 60
    assert sanitize_text_channel_output(f"你好（{long_tail}") == f"你好{long_tail}"


def test_rem_guard_preserves_legit_fullwidth_opens():
    """合法全形開頭（……）與半形詞（.env）不受行首標點正規化影響。"""
    assert sanitize_text_channel_output("……Bryan 要陪雷姆嗎？") == "……Bryan 要陪雷姆嗎？"
    assert sanitize_text_channel_output("（動作）.env 設定檔") == ".env 設定檔"


def test_rem_guard_plain_text_unchanged():
    assert sanitize_text_channel_output("") == ""
    assert sanitize_text_channel_output("茶溫了。") == "茶溫了。"


# ─────────────────────────────────────────────
# 2. per-agent 開關
# ─────────────────────────────────────────────

def test_rem_toggle_default_off():
    """缺省（無設定/None/空 agents）＝ 維持現狀不剝。"""
    assert _text_channel_guard_enabled({}, "agent_rem") is False
    assert _text_channel_guard_enabled(None, "agent_rem") is False
    assert _text_channel_guard_enabled(
        {"agents": [{"id": "agent_rem"}]}, "agent_rem"
    ) is False


def test_rem_toggle_rem_on_others_off():
    cfg = {
        "agents": [
            {"id": "agent_rem", "text_channel_stage_guard": True},
            {"id": "agent_yua"},
        ]
    }
    assert _text_channel_guard_enabled(cfg, "agent_rem") is True
    assert _text_channel_guard_enabled(cfg, "agent_yua") is False


def test_rem_config_file_guard_flag_only_for_rem():
    """configs/default.yaml: agent_rem 開啟、其餘 9 個 agent 一律未開啟。"""
    from configs.loader import load_config
    cfg = load_config()
    by_id = {a["id"]: a for a in cfg.get("agents", [])}
    assert by_id["agent_rem"].get("text_channel_stage_guard") is True
    for aid in (
        "agent_yua", "agent_ruka", "agent_akane", "agent_ram",
        "agent_mahiru", "agent_anna", "agent_mai", "agent_miku", "agent_aoi",
    ):
        assert by_id[aid].get("text_channel_stage_guard", False) is False, aid


# ─────────────────────────────────────────────
# 3. 輸出掛載 + history 淨化（_handle_event_impl / AGENT_SPEAK 唯一發布點）
# ─────────────────────────────────────────────

class _FixedJSONBackend(LLMBackend):
    """固定回傳含括號的 3 欄位 JSON，spy 記錄 messages。"""

    def __init__(self, text: str):
        self.text = text
        self.last_messages = []

    async def complete(self, messages, model, max_tokens=500, temperature=0.85, **kwargs):
        self.last_messages = list(messages)
        return json.dumps(
            {"text": self.text, "audio_text": "", "emotion": "calm"},
            ensure_ascii=False,
        )


def _run_speak(tmp_path, config, agent_id, llm_text, draft):
    """驅動 _handle_event_impl（reason=user_message）→ 捕捉 AGENT_SPEAK + spy proxy。"""
    async def _run():
        bus = SoulEventBus()
        await bus.start()
        captured = []

        def _on_speak(event):
            captured.append(event)

        backend = _FixedJSONBackend(llm_text)
        proxy = LLMProxy(
            bus=bus,
            backend=backend,
            model="mock",
            config=config,
            memory_store=MemoryStore(db_path=Path(tmp_path) / "mem.db"),
            conversation_dir=Path(tmp_path) / "conv",
        )
        bus.subscribe(
            "test_rem_t1_capture",
            _on_speak,
            event_filter={EventType.AGENT_SPEAK},
        )
        try:
            event = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source=agent_id,
                target="broadcast",
                priority=EventPriority.NORMAL,
                payload={
                    "agent_id": agent_id,
                    "reason": "user_message",
                    "draft": draft,
                    "mood": 0.0,
                    "mode": "private",
                    "target_user_id": "bryan",
                    "memory_context": "",
                    "world_context": "",
                },
                session_id=f"session_bryan_{agent_id}",
            )
            await proxy._handle_event_impl(event)
            await asyncio.sleep(0.2)
            return captured, proxy
        finally:
            await bus.stop()

    return asyncio.run(_run())


def test_rem_output_mount_rem_stripped_payload_and_history(tmp_path):
    """agent_rem（開啟守門）: AGENT_SPEAK payload["text"] 剝除括號
    且寫入 history 的 assistant 內容也是乾淨的（淨化在寫 history 之前）。"""
    captured, proxy = _run_speak(
        tmp_path,
        REM_CFG,
        "agent_rem",
        "（手上的事停了，抬眼）.嗯。……茶溫了。",
        "下午不可以做事情了。",
    )
    speak = [e for e in captured if e.payload.get("agent_id") == "agent_rem"]
    assert speak, "應發布 AGENT_SPEAK"
    assert speak[-1].payload["text"] == "嗯。……茶溫了。"

    # 幕僚長補充 #2: history 寫入也已淨化 → 切斷「模型模仿自己歷史」正回饋
    hist = proxy._load_private_instance("agent_rem", "bryan")
    assistant_msgs = [m for m in hist if m.get("role") == "assistant"]
    assert assistant_msgs, "history 應有 assistant 條目"
    assert all("（" not in m["content"] for m in assistant_msgs), assistant_msgs


def test_rem_output_mount_yua_untouched(tmp_path):
    """agent_yua（未開啟）: payload["text"] 與 history 一字不動。"""
    raw = "（挑眉）呵呵，真有趣呢。"
    captured, proxy = _run_speak(tmp_path, MIXED_CFG, "agent_yua", raw, "嗨")
    speak = [e for e in captured if e.payload.get("agent_id") == "agent_yua"]
    assert speak, "應發布 AGENT_SPEAK"
    assert speak[-1].payload["text"] == raw

    hist = proxy._load_private_instance("agent_yua", "bryan")
    assistant_msgs = [m for m in hist if m.get("role") == "assistant"]
    assert assistant_msgs and assistant_msgs[-1]["content"] == raw


# ─────────────────────────────────────────────
# 4. read-side 歷史剝除（只影響 prompt，不寫回 data/）
# ─────────────────────────────────────────────

class _FakeMemory:
    def __init__(self, private=None, group=None):
        self._private = private or []
        self._group = group or []

    def get_group_history(self, limit=20):
        return self._group

    def get_recent_with_meta(self, session_id, limit=20):
        return self._private


def test_rem_readside_private_history_strips_assistant_parens():
    """開守門 → 私聊歷史中的 assistant 括號段剝除（含行首孤立 `.`）。"""
    mem = _FakeMemory(private=[
        {"role": "user", "content": "下午不可以做事情了。", "speaker": "bryan"},
        {"role": "assistant", "content": "（手上的事停了，抬眼）.嗯。……茶溫了。", "speaker": "agent_rem"},
    ])
    msgs = _build_messages_private(
        "agent_rem", "soul", "嗯。", "", mem,
        reason="user_message", strip_assistant_parens=True,
    )
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert assistant_msgs == [{"role": "assistant", "content": "嗯。……茶溫了。"}]


def test_rem_readside_default_keeps_history_untouched():
    """未開守門（缺省 strip_assistant_parens=False）→ assistant 歷史一字不動。"""
    raw = "（手上的事停了，抬眼）.嗯。……茶溫了。"
    mem = _FakeMemory(private=[
        {"role": "user", "content": "嗨", "speaker": "bryan"},
        {"role": "assistant", "content": raw, "speaker": "agent_rem"},
    ])
    msgs = _build_messages_private(
        "agent_rem", "soul", "嗨", "", mem, reason="user_message",
    )
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == raw


def test_rem_readside_group_strips_only_self():
    """群聊: 只剝自己（speaker == agent_id）的括號；Yua 的括號（他人發言）原樣保留。"""
    mem = _FakeMemory(group=[
        {"speaker": "bryan", "content": "下午別忙了。", "is_private": False},
        {"speaker": "agent_rem", "content": "（手上的事停了，抬眼）.嗯。……好。", "is_private": False},
        {"speaker": "agent_yua", "content": "（挑眉）呵呵，真有趣呢。", "is_private": False},
    ])
    msgs = _build_messages_group(
        "agent_rem", "soul", "", "", mem, strip_assistant_parens=True,
    )
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == "嗯。……好。"
    all_sys = "\n".join(m["content"] for m in msgs if m["role"] == "system")
    assert "（挑眉）呵呵，真有趣呢。" in all_sys


# ─────────────────────────────────────────────
# 5. personas/agent_rem.md 教材斷言
# ─────────────────────────────────────────────

def test_rem_persona_text_output_invariants_section_exists():
    content = PERSONA_REM.read_text(encoding="utf-8")
    assert "## 文字頻道輸出守門（Text Output Invariants）" in content
    assert "0 括號動作描寫" in content
    assert "服從停工指令" in content


def test_rem_persona_stage_paren_teaching_removed():
    """:291 / :819 / 語句脈衝範例 / Leak Channel 表的括號教材均已移除
    （字串斷言，非模糊比對）；作為系統註記保留者帶明確「非輸出格式」標註。"""
    content = PERSONA_REM.read_text(encoding="utf-8")
    assert "（嘆氣，繼續）" not in content
    assert "（沉默，不競標）" not in content
    assert "（繼續擦桌子）" not in content
    assert "（繼續）" not in content
    assert "（壓縮沉默）" not in content
    assert "（輕沉默）" not in content
    assert "（行動後）「Bryan" not in content
    # 系統註記樣式仍存在（明確標註非輸出格式）
    assert "系統註記，非輸出格式" in content


def test_rem_persona_hard_rules_intact():
    """TIER 0 / TIER 1 / Canon Lock / Ghost Edge / Recovery Loop 語意原樣保留。"""
    content = PERSONA_REM.read_text(encoding="utf-8")
    assert "情緒名詞絕對不出現在輸出中" in content       # TIER 0 #1
    assert "行為先於語言" in content                     # TIER 0 #2
    assert "固定句型鎖定" in content                     # TIER 1 #5
    assert "Canon Lock" in content
    assert "Ghost Edge" in content
    assert "Recovery Loop" in content


def test_rem_persona_serenity_mechanisms_and_template_removal():
    """滲出機制三者保留；「手上的事」可直抄模板字面已移除。"""
    content = PERSONA_REM.read_text(encoding="utf-8")
    assert "behavior_increment" in content
    assert "tone_micro_shift" in content
    assert "silence_position" in content
    assert "是用手上的事佔滿注意力" not in content   # :134 模板字面移除
    assert "手上的事會停一下" not in content           # :665 模板字面移除