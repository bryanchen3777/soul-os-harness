# tests/soul/test_life_thread_origins_m4.py
# LIFE-THREAD-M4-1 — 模組 4（起源 Prompt 注入範式）契約可測斷言。
# 規格唯一來源：`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §5.1 / §5.2 / §5.3 / §8 / §9 O4 / §10.1。
#
# 隔離：`tests/conftest.py` 的 autouse fixture 已把 SOUL_OS_DATA_DIR 指向 per-test tmp；
# 本檔另以 monkeypatch.setenv + reset_data_root() 顯式隔離。
# **0 生產 `data/**` 寫入**、**0 真實 LLM 呼叫**、**0 網路**。
#
# async 慣例：沿用本 repo 既有寫法 `asyncio.run(...)` 於同步測試函式內
# （全庫 250+ 處；不引入 `pytest.mark.asyncio` 這條新慣例）。
from __future__ import annotations

import ast
import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul import life_thread_origins as m4  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_origins.py"
MODULE_STEM = MODULE_PATH.stem
AGENT = "agent_ruka"
AGENT_OTHER = "agent_yua"

_NARRATIVE = "早上開窗發現風變涼了，想起陽台那盆枯掉的薄荷，決定今天把它救回來。"
_TITLE = "想把陽台那盆枯掉的薄荷救回來"
_NOW = datetime(2026, 9, 14, 9, 30, tzinfo=timezone.utc)
_SOUL = "我是ルカ。我喜歡在夜裡散步，討厭吵鬧的地方。"


# ══════════════════════════════════════════════════════════════
# fixtures / helpers
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def soul_env(tmp_path, monkeypatch):
    """顯式隔離：資料根 → tmp（0 生產寫入）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()
    try:
        yield tmp_path / "data"
    finally:
        monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=False)
        reset_data_root()


def _run_round(agent_id: str, origin_type: str, **kwargs):
    """同步包裝 `run_origin_round`（repo 既有 `asyncio.run` 慣例）。"""
    return asyncio.run(m4.run_origin_round(agent_id, origin_type, **kwargs))


def _lt_lines(agent_id: str = AGENT) -> list[str]:
    p = lt.life_threads_path(agent_id)
    if not p.exists():
        return []
    raw = p.read_bytes().decode("utf-8")
    assert not raw.startswith("\ufeff"), "life_threads.jsonl 不得有 BOM"
    assert "\r" not in raw, "life_threads.jsonl 必須 LF 行尾"
    return [ln for ln in raw.split("\n") if ln.strip()]


def _entries(agent_id: str = AGENT) -> list[dict]:
    return [json.loads(ln) for ln in _lt_lines(agent_id)]


def _json_response(actions: list) -> str:
    return json.dumps({"actions": actions}, ensure_ascii=False)


def _create_action(**over) -> dict:
    act = {
        "thread_id": None,
        "op": "create",
        "title": _TITLE,
        "narrative_content": _NARRATIVE,
        "origin_type": "world_collision",
        "share_target": "none",
        "next_check_hours": 8,
    }
    act.update(over)
    return act


def _fake_trace(records: list) -> None:
    """把假造的 Lived Context 記錄寫進 tmp 的 `perception_trace.jsonl`。

    ⇒ 證明 `world_collision` 種子**真的**來自 Lived Context 落盤產物
    （`data_root()/world/perception_trace.jsonl`），且**不打真網路**。
    """
    p = m4._perception_trace_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _world_record(summary=None, *, accepted=True, source="weather",
                  event_type="rain_started", ts=None) -> dict:
    extra: dict = {"phase": "evaluated"}
    if summary is not None:
        extra["summary"] = summary
    return {
        "event_id": "nov-1",
        "timestamp": (ts or _NOW).isoformat(),
        "source": source,
        "event_type": event_type,
        "accepted": accepted,
        "extra": extra,
    }


class _SpyLLM:
    """假 LLM 呼叫端：記錄呼叫、回傳固定字串（**0 真實 LLM／0 網路**）。"""

    def __init__(self, response=None, *, raises=False):
        self.calls: list = []
        self.response = response
        self.raises = raises

    def __call__(self, messages, agent_id):
        self.calls.append({"messages": messages, "agent_id": agent_id})
        if self.raises:
            raise RuntimeError("boom (simulated LLM failure)")
        return self.response


class _ForbiddenProxy:
    """若被呼叫即失敗：證明有注入 `llm_caller` 時**不會**碰 proxy。"""

    async def generate_text(self, **kwargs):  # pragma: no cover
        raise AssertionError("測試不得呼叫真實 LLMProxy.generate_text")


# ══════════════════════════════════════════════════════════════
# 1. 四個 origin_type 各自能組出合法 prompt（含必填上下文）
# ══════════════════════════════════════════════════════════════

def test_goal_driven_prompt_contains_required_context(soul_env):
    """§5.2.1：必填上下文 ＝ ACTIVE/IN_PROGRESS goal 的 title/state ＋ soul_context。"""
    p = m4.build_origin_prompt(
        AGENT, "goal_driven", soul_context=_SOUL,
        goals=[{"title": "學會做味噌湯", "state": "ACTIVE"},
               {"title": "把房間整理乾淨", "state": "IN_PROGRESS"}],
    )
    assert p is not None
    assert p["system"].strip() and p["user"].strip()
    assert "學會做味噌湯" in p["user"]
    assert "ACTIVE" in p["user"]
    assert "把房間整理乾淨" in p["user"]
    assert _SOUL in p["system"]
    # §5.2.1 模板逐字要素
    assert "這幾天你在這上面實際動手了嗎" in p["user"]
    # §5.2.1 禁令：不得把 goal 複製成線頭 title
    assert "不是意向複述" in p["system"]


def test_goal_driven_only_reads_own_agent_goals(soul_env):
    """§5.2.1 禁令：**不得**讀其他 agent 的 goals（以 agent_id 為唯一鍵隔離）。"""
    p = m4.build_origin_prompt(
        AGENT, "goal_driven", soul_context=_SOUL,
        goals=[{"title": "只屬於自己的目標", "state": "ACTIVE"}],
    )
    assert "只屬於自己的目標" in p["user"]
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "WHERE agent_id = ?" in src, "goals 讀取必須以 agent_id 隔離"


def test_goal_driven_uses_readonly_sqlite(soul_env):
    """§5.2.1：goals 表**唯讀**（不得建目錄／建檔／寫入）。"""
    assert m4.read_goals_readonly(AGENT) == []
    assert not (soul_env / "memory" / AGENT).exists(), "唯讀不得建立任何目錄"


def test_goal_driven_template_uses_up_to_three_goals(soul_env):
    """§5.2.1：最多 3 條。"""
    goals = [{"title": f"目標{i}", "state": "ACTIVE"} for i in range(5)]
    assert len(m4.collect_goal_seeds(AGENT, goals)) == m4.MAX_GOALS_IN_CONTEXT == 3
    p = m4.build_origin_prompt(AGENT, "goal_driven", soul_context=_SOUL, goals=goals)
    assert "目標0" in p["user"] and "目標2" in p["user"]
    assert "目標3" not in p["user"]


def test_goal_driven_filters_non_active_states(soul_env):
    """§5.2.1：只取 ACTIVE / IN_PROGRESS。"""
    goals = [{"title": "已完成", "state": "COMPLETED"},
             {"title": "進行中", "state": "IN_PROGRESS"},
             {"title": "被擱置", "state": "SUSPENDED"}]
    seeds = m4.collect_goal_seeds(AGENT, goals)
    assert [s["title"] for s in seeds] == ["進行中"]


def test_necessity_driven_prompt_contains_slot_time_and_diary(soul_env):
    """§5.2.2：必填上下文 ＝ 當前本地時間與時段 ＋ 近 3 日 `source=="llm"` diary。"""
    diary = [{"date": "2026-09-14", "slot": "morning", "content": "昨晚沒睡好，肩膀很痠。"},
             {"date": "2026-09-13", "slot": "night", "content": "把廚房擦了一遍，累了。"}]
    p = m4.build_origin_prompt(AGENT, "necessity_driven", now=_NOW, diary=diary)
    assert p is not None
    assert "morning" in p["user"]
    assert "2026-09-14" in p["user"]
    assert "昨晚沒睡好" in p["user"]
    # §5.2.2 模板逐字要素：預設事情已存在，問「怎麼解決」
    assert "不是你能選要不要發生的" in p["user"]
    assert "你打算怎麼處理它" in p["user"]


def test_necessity_driven_forbids_optionality_framing(soul_env):
    """§5.2.2 禁令：**不得**宣稱「沒有選擇要不要開始」；須預設事情已存在。"""
    p = m4.build_origin_prompt(AGENT, "necessity_driven", now=_NOW, diary=[])
    assert "預設事情已經存在" in p["system"]
    assert "沒有選擇要不要開始" in p["system"]
    assert "外部工具呼叫" in p["system"]


def test_necessity_driven_reads_only_llm_sourced_diary(soul_env):
    """§5.2.2：沿既有 `source == "llm"` 過濾（排除 placeholder）；壞行跳過。"""
    d = soul_env / "soul" / AGENT / "diary"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{_NOW.strftime('%Y-%m-%d')}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"slot": "morning", "source": "llm", "content": "真的日記"}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"slot": "morning", "source": "placeholder", "content": "假的内文"}, ensure_ascii=False) + "\n")
        f.write("{bad json\n")
    contents = [e["content"] for e in m4.read_recent_diary_llm(AGENT, _NOW)]
    assert "真的日記" in contents
    assert "假的内文" not in contents


def test_necessity_driven_slot_domain_is_exactly_two_values(soul_env):
    """§4.1／§5.2.2：時段值域**只有** {morning, night}（不新增第三個時段）。"""
    assert m4.SLOT_VALUES == ("morning", "night")
    assert m4.current_slot(datetime(2026, 9, 14, 8, 0)) == "morning"
    assert m4.current_slot(datetime(2026, 9, 14, 22, 0)) == "night"
    assert m4.current_slot(datetime(2026, 9, 14, 3, 0)) == "night"
    assert m4.current_slot(datetime(2026, 9, 14, 15, 0)) == "night"


def test_whim_driven_prompt_is_purely_endogenous(soul_env):
    """§5.2.3：必填上下文只有 `soul_context`；**不得**用 Lived Context／外部事實。"""
    p = m4.build_origin_prompt(AGENT, "whim_driven", soul_context=_SOUL)
    assert p is not None
    assert _SOUL in p["system"]
    assert "只有你才會" in p["user"]
    assert "當下正在做" in p["user"]
    # 🔴 純內生：不得出現世界感知／時間／處境區塊
    for forbidden in ("Lived Context", "[當前時間]", "[當前處境]"):
        assert forbidden not in p["user"], f"whim_driven 不得注入 {forbidden}"
    # 註：`[到期線頭]` **允許**出現 —— 那是角色**自己的**線頭（§5.1 第 1 件事：
    # 對到期線頭推進敘事），不是「外部事實」；§5.2.3 禁的是 Lived Context。
    assert set(m4.collect_whim_seeds(AGENT, _SOUL).keys()) == {"soul_context"}


def test_whim_driven_forbids_generic_soup_and_external_facts(soul_env):
    p = m4.build_origin_prompt(AGENT, "whim_driven", soul_context=_SOUL)
    assert "純內生" in p["system"]
    assert "通用雞湯" in p["system"]


def test_whim_driven_absent_soul_context_yields_no_prompt(soul_env, monkeypatch):
    """必填上下文（soul_context）不足 ⇒ 不組 prompt（fail-silent）。"""
    monkeypatch.setattr(m4, "load_soul_context", lambda *a, **k: "")
    assert m4.build_origin_prompt(AGENT, "whim_driven") is None


def test_world_collision_seed_comes_from_lived_context(soul_env):
    """§5.2.4：種子必須來自 Lived Context（`perception_trace.jsonl` 的 accepted 記錄）。"""
    _fake_trace([_world_record("外面開始下雨了。")])
    seeds = m4.collect_world_seeds(AGENT, now=_NOW)
    assert len(seeds["facts"]) == 1
    assert seeds["facts"][0]["summary"] == "外面開始下雨了。"
    assert seeds["facts"][0]["source"] == "weather"

    p = m4.build_origin_prompt(AGENT, "world_collision", soul_context=_SOUL, now=_NOW)
    assert p is not None
    assert "外面開始下雨了。" in p["user"]      # 具體事實文字
    assert "剛剛世界發生了這件事" in p["user"]
    assert "Bryan 對我說的話" in p["system"]     # 禁令在 prompt 內


def test_world_collision_requires_concrete_fact_text_not_event_type(soul_env):
    """§5.2.4：fact text **只能**來自 `extra["summary"]`，不得以 event_type 頂替。"""
    _fake_trace([_world_record(summary=None)])  # 有 event_type，但沒有 fact text
    seeds = m4.collect_world_seeds(AGENT, now=_NOW)
    assert seeds["facts"] == []
    assert seeds["skipped"] == 1
    assert m4.build_origin_prompt(AGENT, "world_collision", soul_context=_SOUL, now=_NOW) is None


def test_world_collision_tolerates_legacy_records_without_summary(soul_env, caplog):
    """🔴 前置票未生效：既有歷史列沒有 `extra["summary"]` ⇒ 跳過、有界計數、繼續。"""
    _fake_trace([
        _world_record(summary=None),
        _world_record(summary="", source="news", event_type="news_event"),
        _world_record(summary="   ", source="calendar", event_type="calendar_event"),
        _world_record(summary="真的有文字", source="news", event_type="news_event"),
    ])
    with caplog.at_level(logging.INFO):
        seeds = m4.collect_world_seeds(AGENT, now=_NOW)
    assert len(seeds["facts"]) == 1
    assert seeds["skipped"] == 3
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "無 fact text" in joined, "必須寫有界計數行"
    assert "skipped=3" in joined


def test_world_collision_rejects_unqualified_and_stale_records(soul_env):
    """§4.2／§5.2.4：只算 `SOURCES_QUALIFYING`、`accepted is True`、4h 視窗內。"""
    stale = _NOW - timedelta(hours=m4.WORLD_COLLISION_WINDOW_HOURS + 1)
    _fake_trace([
        _world_record("未過門檻", accepted=False),
        _world_record("不算數的來源", source="synthetic"),
        _world_record("太舊了", ts=stale),
        _world_record("新鮮的天氣", ts=_NOW - timedelta(hours=1)),
    ])
    facts = m4.collect_world_seeds(AGENT, now=_NOW)["facts"]
    assert [f["summary"] for f in facts] == ["新鮮的天氣"]
    assert m4.WORLD_COLLISION_WINDOW_HOURS == 4


def test_world_collision_accepts_weather_news_and_bryan_calendar(soul_env):
    """§5.2.4：真實天氣／新聞／**Bryan 日程**三類（VISION §4.3）。"""
    _fake_trace([
        _world_record("外面開始下雨了。", source="weather", event_type="rain_started"),
        _world_record("某地發生了大事。", source="news", event_type="news_event"),
        _world_record("30 分鐘後有重要會議。", source="calendar", event_type="calendar_event"),
    ])
    facts = m4.collect_world_seeds(AGENT, now=_NOW)["facts"]
    assert {f["source"] for f in facts} == {"weather", "news", "calendar"}
    # 非具名來源不算數（裸字面量禁令）
    assert "synthetic" not in m4.SOURCES_QUALIFYING


def test_all_four_origin_types_build_legal_prompts(soul_env):
    """§5.2：四個 `origin_type` **全部**都要能組出合法 prompt（不接受 3/4）。"""
    assert set(lt.ORIGIN_TYPES) == {
        "goal_driven", "necessity_driven", "whim_driven", "world_collision"
    }
    _fake_trace([_world_record("外面開始下雨了。")])
    for origin in lt.ORIGIN_TYPES:
        p = m4.build_origin_prompt(
            AGENT, origin, soul_context=_SOUL, now=_NOW,
            goals=[{"title": "學會做味噌湯", "state": "ACTIVE"}],
        )
        assert p is not None, f"{origin} 必須能組出 prompt"
        assert isinstance(p.get("system"), str) and p["system"].strip()
        assert isinstance(p.get("user"), str) and p["user"].strip()
        # §5.1 共同骨架必須在每個 origin 的 prompt 內
        assert "輸出契約 §5.1" in p["system"]
        assert '"actions"' in p["system"]
        assert f'origin_type = "{origin}"' in p["system"]


def test_prompt_forbids_numeric_scoring_fields(soul_env):
    """§5.1 規則 6：prompt 必須明令禁止任何數值評分欄位。"""
    p = m4.build_origin_prompt(AGENT, "whim_driven", soul_context=_SOUL)
    for banned in sorted(lt.FORBIDDEN_FIELDS):
        assert banned in p["system"], f"prompt 應禁用 {banned}"
    assert "強度分數" in p["system"]


def test_prompt_requires_full_life_semantics(soul_env):
    """§5.1 規則 2：`narrative_content` 必須具備完整生活語義，不得等同 title。"""
    p = m4.build_origin_prompt(AGENT, "whim_driven", soul_context=_SOUL)
    assert "完整生活語義" in p["system"]
    assert "不得等同 `title`" in p["system"]


def test_prompt_declares_capacity_is_a_count_not_a_score(soul_env):
    """§2.6.1：容量是**計數**（不是評分），且 create 只在 active < cap 時允許。"""
    p = m4.build_origin_prompt(AGENT, "whim_driven", soul_context=_SOUL)
    cap = lt.capacity(AGENT)
    assert f"上限 {cap} 條" in p["system"]


def test_invalid_origin_type_returns_none(soul_env):
    assert m4.build_origin_prompt(AGENT, "not_a_real_origin", soul_context=_SOUL) is None


# ══════════════════════════════════════════════════════════════
# 2. LLM 一律 mock；失敗／非 JSON ⇒ 不落盤
# ══════════════════════════════════════════════════════════════

def test_llm_failure_does_not_persist(soul_env, monkeypatch):
    """§5.1：LLM 失敗 ⇒ fail-silent、本時段**不落盤任何事件**。"""
    called: list = []
    monkeypatch.setattr(m4.lt, "create_thread", lambda *a, **k: called.append(a))
    spy = _SpyLLM(raises=True)

    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["called"] is True
    assert res["created"] == []
    assert called == [], "LLM 失敗時 create_thread 不得被呼叫"
    assert _lt_lines() == [], "不得落盤任何列"


def test_non_json_response_does_not_persist(soul_env, monkeypatch):
    """§5.1：非 JSON ⇒ fail-silent、不落盤（含 markdown 圍欄／缺 actions）。"""
    called: list = []
    monkeypatch.setattr(m4.lt, "create_thread", lambda *a, **k: called.append(a))

    for bad in ("這不是 JSON", "", "```json\nnot json at all\n```",
                '{"nope": 1}', '{"actions": "x"}', "[1,2,3]"):
        res = _run_round(AGENT, "whim_driven", llm_caller=_SpyLLM(bad),
                         build_kwargs={"soul_context": _SOUL})
        assert res["created"] == [], f"bad={bad!r} 不得落盤"
    assert called == []
    assert _lt_lines() == []


def test_no_proxy_and_no_caller_is_fail_silent(soul_env):
    """無注入 LLMProxy 且無 caller ⇒ **0 呼叫**、0 落盤、不 raise（不得打真網路）。"""
    m4.set_llm_proxy(None)
    res = _run_round(AGENT, "whim_driven", build_kwargs={"soul_context": _SOUL})
    assert res["prompt_available"] is True
    assert res["called"] is False
    assert res["llm_calls"] == 0
    assert res["created"] == []
    assert _lt_lines() == []


def test_injected_caller_never_touches_proxy(soul_env):
    """有注入 `llm_caller` 時**不得**碰 proxy（證明測試 0 真實 LLM）。"""
    m4.set_llm_proxy(_ForbiddenProxy())
    try:
        res = _run_round(
            AGENT, "whim_driven",
            llm_caller=_SpyLLM(_json_response([_create_action(origin_type="whim_driven")])),
            build_kwargs={"soul_context": _SOUL},
        )
        assert len(res["created"]) == 1
    finally:
        m4.set_llm_proxy(None)


def test_prompt_unavailable_calls_no_llm(soul_env):
    """必填上下文不足（無世界命中）⇒ **連 LLM 都不呼叫**（0 成本）、不落盤。"""
    spy = _SpyLLM(_json_response([_create_action()]))
    res = _run_round(AGENT, "world_collision", now=_NOW, llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["prompt_available"] is False
    assert spy.calls == [], "無 fact text（無命中）時不得呼叫 LLM"
    assert res["llm_calls"] == 0
    assert _lt_lines() == []


# ══════════════════════════════════════════════════════════════
# 3. 寫入的列通過 M1 的 fold 與欄位驗證
# ══════════════════════════════════════════════════════════════

def test_created_row_passes_m1_validation_and_fold(soul_env):
    """寫入列須通過 M1 欄位驗證 ＋ fold（title 1–40、narrative 1–600 非空非佔位符不等同 title）。"""
    spy = _SpyLLM(_json_response([_create_action()]))
    res = _run_round(
        AGENT, "world_collision", now=_NOW, llm_caller=spy,
        build_kwargs={"soul_context": _SOUL,
                      "world_records": [_world_record("外面開始下雨了。")]},
    )
    assert len(res["created"]) == 1
    thread_id = res["created"][0]

    lines = _lt_lines()
    assert len(lines) == 1, "恰好一列 created"
    entry = json.loads(lines[0])

    # ── §2.2 欄位規格 ──
    assert entry["thread_id"] == thread_id
    assert entry["event_seq"] == 1
    assert entry["event_type"] == "created"
    assert entry["status"] == "active"
    assert entry["origin_type"] == "world_collision"
    assert entry["share_target"] in lt.SHARE_TARGET_FIXED

    title, narrative = entry["title"], entry["narrative_content"]
    assert lt.TITLE_MIN_LEN <= len(title) <= lt.TITLE_MAX_LEN
    assert lt.NARRATIVE_MIN_LEN <= len(narrative) <= lt.NARRATIVE_MAX_LEN
    assert narrative.strip(), "narrative_content 不得為空"
    assert narrative.lower().strip() not in lt._PLACEHOLDER_TOKENS, "不得為佔位符"
    assert narrative.strip() != title.strip(), "不得等同 title"
    assert len(narrative) >= 20, "必須具備完整生活語義（本引擎的存在理由）"

    assert entry["check_after_ts"] is not None
    assert entry["dissolved_at"] is None and entry["sage_fact_id"] is None

    # ── M1 fold ──
    folded = lt.fold(AGENT)
    assert thread_id in folded
    assert folded[thread_id]["status"] == "active"
    assert folded[thread_id]["title"] == title
    assert lt.active_count(AGENT) == 1

    # ── check_after_ts = now + clamp(next_check_hours) ──
    assert datetime.fromisoformat(entry["check_after_ts"]) - _NOW == timedelta(hours=8)


def test_next_check_hours_is_clamped_to_1_72(soul_env):
    """§5.1：`next_check_hours` clamp 至 [1, 72]（整數時距，非強度分數）。"""
    assert m4.NEXT_CHECK_HOURS_MIN == 1 and m4.NEXT_CHECK_HOURS_MAX == 72
    assert m4._clamp_hours(0) == 1
    assert m4._clamp_hours(-5) == 1
    assert m4._clamp_hours(999) == 72
    assert m4._clamp_hours("8") == 8
    assert m4._clamp_hours(None) is None
    assert m4._clamp_hours("abc") is None
    assert m4._clamp_hours(True) is None

    spy = _SpyLLM(_json_response([_create_action(next_check_hours=999,
                                                 origin_type="whim_driven")]))
    _run_round(AGENT, "whim_driven", now=_NOW, llm_caller=spy,
               build_kwargs={"soul_context": _SOUL})
    entry = _entries()[0]
    assert datetime.fromisoformat(entry["check_after_ts"]) - _NOW == timedelta(hours=72)


def test_narrative_equal_to_title_is_rejected_and_not_persisted(soul_env):
    """§2.2／§5.1：`narrative_content` 等同 `title` ⇒ 不落盤、不中斷呼叫端。"""
    spy = _SpyLLM(_json_response([_create_action(narrative_content=_TITLE,
                                                 origin_type="whim_driven")]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["created"] == []
    assert _lt_lines() == []
    assert any(s.startswith("create:") for s in res["skipped"])


def test_placeholder_narrative_is_rejected(soul_env):
    spy = _SpyLLM(_json_response([_create_action(narrative_content="placeholder",
                                                 origin_type="whim_driven")]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["created"] == [] and _lt_lines() == []


def test_overlong_title_or_narrative_is_rejected(soul_env):
    for over in (_create_action(title="字" * 41, origin_type="whim_driven"),
                 _create_action(narrative_content="字" * 601, origin_type="whim_driven"),
                 _create_action(title="   ", origin_type="whim_driven")):
        res = _run_round(AGENT, "whim_driven", llm_caller=_SpyLLM(_json_response([over])),
                         build_kwargs={"soul_context": _SOUL})
        assert res["created"] == [] and _lt_lines() == []


def test_actions_truncated_to_three(soul_env):
    """§5.1：`actions` 超過 3 項 ⇒ 截斷至前 3 項。"""
    actions = [_create_action(title=f"線頭{i}",
                              narrative_content=f"這是第 {i} 條線頭的生活敘事內容。",
                              origin_type="whim_driven")
               for i in range(5)]
    spy = _SpyLLM(_json_response(actions))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert m4.MAX_ACTIONS_PER_ROUND == 3
    attempts = len(res["created"]) + sum(1 for s in res["skipped"] if s == "create:rejected")
    assert attempts == 3, "只應嘗試前 3 項"
    assert lt.active_count(AGENT) <= lt.LIFE_THREAD_ACTIVE_CAP_HARD_MAX


def test_advance_and_terminal_ops_go_through_m1(soul_env):
    """§5.1：`advance` / `complete` / `abandon` 一律經 M1 公開 API。"""
    tid = lt.create_thread(AGENT, _TITLE, _NARRATIVE, "whim_driven",
                           check_after_ts=(_NOW - timedelta(hours=1)).isoformat())
    assert tid is not None

    spy = _SpyLLM(_json_response([{
        "thread_id": tid, "op": "advance",
        "narrative_content": "今天終於去買了新的花盆，還順手換了土。",
        "origin_type": "whim_driven", "share_target": "none", "next_check_hours": 12}]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL, "now": _NOW})
    assert res["advanced"] == [tid]
    assert "換了土" in lt.fold(AGENT)[tid]["narrative_content"]

    # 未知 op ⇒ 不落盤、記 skipped（不得 raise）
    spy2 = _SpyLLM(_json_response([{
        "thread_id": tid, "op": "completed", "origin_type": "whim_driven",
        "share_target": "none", "next_check_hours": 12}]))
    res2 = _run_round(AGENT, "whim_driven", llm_caller=spy2,
                      build_kwargs={"soul_context": _SOUL, "now": _NOW})
    assert res2["transitioned"] == [] and res2["skipped"] == ["unknown_op"]
    assert lt.fold(AGENT)[tid]["status"] == "active"

    spy3 = _SpyLLM(_json_response([{
        "thread_id": tid, "op": "complete", "narrative_content": "薄荷活過來了。",
        "origin_type": "whim_driven", "share_target": "none", "next_check_hours": 12}]))
    res3 = _run_round(AGENT, "whim_driven", llm_caller=spy3,
                      build_kwargs={"soul_context": _SOUL, "now": _NOW})
    assert res3["transitioned"] == [tid]
    assert lt.fold(AGENT)[tid]["status"] == "completed"
    assert lt.fold(AGENT)[tid]["check_after_ts"] is None


def test_illegal_transition_is_not_written(soul_env):
    """§2.5：終態不可復活 ⇒ 非法轉移不寫列、不中斷。"""
    tid = lt.create_thread(AGENT, _TITLE, _NARRATIVE, "whim_driven")
    assert lt.append_transition(AGENT, tid, "completed") is True
    before = len(_lt_lines())

    spy = _SpyLLM(_json_response([{
        "thread_id": tid, "op": "abandon", "narrative_content": "不玩了。",
        "origin_type": "whim_driven", "share_target": "none", "next_check_hours": 12}]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["transitioned"] == []
    assert res["skipped"] == ["abandon:rejected"]
    assert len(_lt_lines()) == before, "非法轉移不得寫入任何列"


def test_unknown_thread_advance_is_ignored(soul_env):
    """未知 thread_id ⇒ M1 回 False ⇒ 不落盤、不中斷。"""
    spy = _SpyLLM(_json_response([{
        "thread_id": "00000000-0000-4000-8000-000000000000", "op": "advance",
        "narrative_content": "推進一個不存在的線頭。", "origin_type": "whim_driven",
        "share_target": "none", "next_check_hours": 12}]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["advanced"] == [] and res["skipped"] == ["advance:rejected"]
    assert _lt_lines() == []


# ══════════════════════════════════════════════════════════════
# 4. 禁用欄位 key 集合斷言（No-Scoring）
# ══════════════════════════════════════════════════════════════

def test_forbidden_fields_set_matches_contract(soul_env):
    """§2.2 禁用欄位清單逐字。"""
    assert lt.FORBIDDEN_FIELDS == frozenset(
        {"score", "weight", "intensity", "urgency", "priority", "longing", "confidence"})


def test_written_entry_keys_have_no_forbidden_fields(soul_env):
    """§2.2：寫入 dict 的 key 集合不得含任何評分鍵。"""
    spy = _SpyLLM(_json_response([_create_action()]))
    _run_round(AGENT, "world_collision", now=_NOW, llm_caller=spy,
               build_kwargs={"soul_context": _SOUL,
                             "world_records": [_world_record("外面開始下雨了。")]})
    assert _entries(), "前置條件：應已落地一列"
    for entry in _entries():
        assert not (set(entry.keys()) & lt.FORBIDDEN_FIELDS)


def test_module_result_and_seed_keys_have_no_forbidden_fields(soul_env):
    """本模組自身的回傳／種子 dict 亦不得出現禁用鍵。"""
    _fake_trace([_world_record("外面開始下雨了。")])
    spy = _SpyLLM(_json_response([_create_action(origin_type="whim_driven")]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    payloads = [
        res,
        m4.collect_world_seeds(AGENT, now=_NOW),
        m4.collect_necessity_seeds(AGENT, now=_NOW),
        m4.collect_whim_seeds(AGENT, _SOUL),
    ]
    for p in payloads:
        assert not (set(p.keys()) & lt.FORBIDDEN_FIELDS)
    for g in m4.collect_goal_seeds(AGENT, [{"title": "t", "state": "ACTIVE"}]):
        assert not (set(g.keys()) & lt.FORBIDDEN_FIELDS)


def test_module_source_has_no_forbidden_key_literals(soul_env):
    """原始碼層級：不得以禁用鍵作 dict 鍵或賦值目標。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    assert k.value not in lt.FORBIDDEN_FIELDS, f"禁用鍵 {k.value!r} 出現在 dict"
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assert t.id not in lt.FORBIDDEN_FIELDS, f"禁用鍵 {t.id!r} 被賦值"


# ══════════════════════════════════════════════════════════════
# 5. 容量防線生效 ＋ 不得繞過 M1 直接寫檔
# ══════════════════════════════════════════════════════════════

def test_capacity_defense_blocks_create_and_file_unchanged(soul_env, caplog):
    """§2.6.3／§5.1：`active_count >= capacity` ⇒ 不得建立、**檔案列數不變**。"""
    cap = lt.capacity(AGENT)
    assert cap == lt.LIFE_THREAD_ACTIVE_CAP_DEFAULT == 2

    for i in range(cap):
        assert lt.create_thread(AGENT, f"既有線頭{i}",
                                f"這是第 {i} 條既有線頭的敘事內容。", "whim_driven") is not None
    assert lt.active_count(AGENT) == cap

    before_lines = _lt_lines()
    assert len(before_lines) == cap

    spy = _SpyLLM(_json_response([_create_action(origin_type="whim_driven")]))
    with caplog.at_level(logging.INFO):
        res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                         build_kwargs={"soul_context": _SOUL})

    assert res["created"] == [], "超限不得建立新線頭"
    assert res["skipped"] == ["create:rejected"], "超限 action 應被丟棄（§5.1）"
    assert _lt_lines() == before_lines, "超限時檔案內容必須逐位元不變"
    assert lt.active_count(AGENT) == cap
    # §9 O6 觀測行（由 M1 的**唯一**強制點寫出）
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "(create rejected)" in joined


def test_capacity_uses_m1_single_enforcement_point(soul_env, monkeypatch):
    """§2.6.3：強制點**只有一個**（M1 `create_thread`）——M4 不得自行預檢。"""
    calls: list = []
    real_create = lt.create_thread

    def _spy_create(*a, **k):
        calls.append(k.get("title"))
        return real_create(*a, **k)

    monkeypatch.setattr(m4.lt, "create_thread", _spy_create)
    for i in range(lt.capacity(AGENT)):
        real_create(AGENT, f"既有{i}", f"既有敘事內容第 {i} 條。", "whim_driven")

    spy = _SpyLLM(_json_response([_create_action(origin_type="whim_driven")]))
    _run_round(AGENT, "whim_driven", llm_caller=spy, build_kwargs={"soul_context": _SOUL})
    assert calls == [_TITLE], "M4 必須把 create 交給 M1 統一做容量判定"


def test_module_never_writes_files_directly(soul_env):
    """🔴 M4 不得繞過 M1 直接寫檔：AST 掃描（0 寫入型 open / 0 .write）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                assert mode in (None, "r", "rb"), f"模組不得以 {mode!r} 模式開檔"
            if isinstance(f, ast.Attribute) and f.attr in (
                "write", "writelines", "write_text", "write_bytes"
            ):
                raise AssertionError(f"模組不得直接寫檔：.{f.attr}()")


def test_module_never_touches_life_threads_file_directly(soul_env):
    """M4 不得自行組 life_threads 檔路徑（寫入一律經 M1 公開 API）。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    for api in ("create_thread", "append_updated", "append_transition"):
        assert f"lt.{api}(" in src, f"必須經 M1 的 {api}() 寫入"
    assert "LIFE_THREADS_FILENAME" not in src
    assert "life_threads.jsonl" not in src
    assert "life_threads_path" not in src


# ══════════════════════════════════════════════════════════════
# 6. 未接線證明（§10.1）
# ══════════════════════════════════════════════════════════════

def test_module_is_not_wired_into_production_paths(soul_env):
    """§10.1：`git grep <模組檔名根> -- src scripts configs` 應為 **0 命中**。

    註：本模組原始碼刻意不含自身檔名字面（含標頭註解與 logger 名），
    故本斷言在 commit 後仍為**字面 0 命中**，而非「排除自己」的近似。
    """
    for paths in (["src"], ["scripts"], ["configs"], ["src", "scripts", "configs"]):
        proc = subprocess.run(
            ["git", "grep", "-n", MODULE_STEM, "--"] + paths,
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        out = (proc.stdout or "").strip()
        assert out == "", f"{paths} 不得有 {MODULE_STEM} 命中，實得：\n{out}"


def test_no_scheduler_or_timer_wiring(soul_env):
    """§10.1／INV-2：不得動 scheduler、不得新增定時器。"""
    for rel in ("src/soul/scheduler.py", "configs/default.yaml", "src/soul/life_threads.py"):
        assert MODULE_STEM not in (_REPO_ROOT / rel).read_text(encoding="utf-8"), \
            f"{rel} 不得被改動接線"

    src = MODULE_PATH.read_text(encoding="utf-8")
    for banned in ("asyncio.create_task", "threading.Timer", "call_later",
                   "APScheduler", "setInterval", "asyncio.sleep"):
        assert banned not in src, f"不得引入 {banned}"


def test_module_declares_unwired_and_reuses_existing_channel(soul_env):
    """文件紀律：須自我聲明「未接線零件」與「0 新 provider／通道」。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "未接線零件" in src
    assert "0 新 provider" in src
    assert "generate_text" in src, "必須沿用既有 LLM 通道（§8.1）"
    for net in ("httpx", "requests", "urllib.request", "aiohttp", "socket"):
        assert f"import {net}" not in src, f"不得新增網路通道：{net}"


def test_module_does_not_import_scheduler_or_production_paths(soul_env):
    """§10.1：M4 只依賴 M1（＋唯讀種子來源）；不得 import 生產編排面。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for banned in ("scheduler", "scripts.run_server", "agency", "inner_life"):
        for mod in imported:
            assert banned not in mod, f"不得 import {mod}（{banned}）"


# ══════════════════════════════════════════════════════════════
# 7. 成本斷言（§8）
# ══════════════════════════════════════════════════════════════

def test_single_round_llm_calls_within_section_8_limits(soul_env):
    """§8.1：單輪 ≤ 2（路徑 A 1 ＋ 路徑 B 1）；無 dissolve_hook 時恰為 1。"""
    assert m4.MAX_LLM_CALLS_PER_ROUND == 2
    assert m4.LIFE_THREAD_WAKE_MAX_PER_DAY == 2
    assert m4.LIFE_THREAD_DISSOLVE_MAX_PER_DAY == 3
    # §8.2 最壞 = 路徑 A(2) + 路徑 B(3) = 5
    assert m4.LIFE_THREAD_WAKE_MAX_PER_DAY + m4.LIFE_THREAD_DISSOLVE_MAX_PER_DAY == 5

    spy = _SpyLLM(_json_response([_create_action(origin_type="whim_driven")]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["llm_calls"] == 1, "路徑 A 恰好 1 次"
    assert len(spy.calls) == 1, "同一輪不得重複呼叫"
    assert res["llm_calls"] <= m4.MAX_LLM_CALLS_PER_ROUND


def test_round_never_exceeds_per_round_call_cap(soul_env):
    """即使 3 條線頭同輪轉終態，呼叫數仍 ≤ §8.1 單輪上限。"""
    tids = [lt.create_thread(AGENT, f"線頭{i}", f"第 {i} 條線頭的敘事內容。", "whim_driven")
            for i in range(lt.capacity(AGENT))]
    actions = [{"thread_id": t, "op": "complete",
                "narrative_content": f"第 {i} 條可以放下了。", "origin_type": "whim_driven",
                "share_target": "none", "next_check_hours": 12}
               for i, t in enumerate(tids)]
    dissolved: list = []

    spy = _SpyLLM(_json_response(actions))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     dissolve_hook=lambda a, t, s: dissolved.append(t),
                     build_kwargs={"soul_context": _SOUL})
    assert len(res["transitioned"]) == len(tids) == lt.LIFE_THREAD_ACTIVE_CAP_DEFAULT
    assert sorted(dissolved) == sorted(tids), "溶解接縫（M2）應被呼叫"
    assert res["llm_calls"] <= m4.MAX_LLM_CALLS_PER_ROUND


def test_dissolve_hook_default_none_costs_nothing(soul_env):
    """M2 未落地 ⇒ dissolve_hook 預設 None ⇒ §8 路徑 B 為 0 成本。"""
    tid = lt.create_thread(AGENT, _TITLE, _NARRATIVE, "whim_driven")
    spy = _SpyLLM(_json_response([{
        "thread_id": tid, "op": "complete", "narrative_content": "結束了。",
        "origin_type": "whim_driven", "share_target": "none", "next_check_hours": 12}]))
    res = _run_round(AGENT, "whim_driven", llm_caller=spy,
                     build_kwargs={"soul_context": _SOUL})
    assert res["transitioned"] == [tid]
    assert res["dissolved"] == []
    assert res["llm_calls"] == 1, "無 dissolve_hook ⇒ 不得多一次呼叫"


# ══════════════════════════════════════════════════════════════
# 8. 隔離紅線（0 生產 data/** 寫入）
# ══════════════════════════════════════════════════════════════

def test_data_root_is_the_tmp_isolation_dir(soul_env):
    """所有寫入都落在 per-test tmp 資料根（**0 生產 `data/**` 寫入**）。"""
    assert data_root() == soul_env
    assert str(soul_env).startswith(str(Path(soul_env).parent))
    assert _REPO_ROOT not in soul_env.parents
