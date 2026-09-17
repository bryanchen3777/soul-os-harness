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
import locale
import logging
import subprocess
import sys
import warnings
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
# 6. 接線後孤立性證明（§10.1 ＋ M5）
# ══════════════════════════════════════════════════════════════

#: M4 護欄的**掃描基底**（F4）：恰為這四個目錄，與 M3 的
#: `test_t7_non_python_dirs_still_zero_reference` 覆蓋面**對齊**。
#: ⚠️ M5 重寫時曾把基底縮成 `("src", "scripts")`，而被它取代的
#: `git grep -- src scripts configs` 原本**含 `configs`** ⇒ 覆蓋變窄且無替代測試；
#: 這裡以精確清單等值釘死（見 `test_m4_guard_scan_covers_configs_and_clients`）。
_M4_SCAN_BASES = ("src", "scripts", "configs", "clients")

#: 🔴 F5：`ast.parse` **無法解析**的 `.py`（相對 repo 根、排序）。
#: 舊版掃描器是 `except Exception: continue` ⇒ 這些檔案的 import **完全隱形**
#: （反而 `git grep` 還抓得到），是典型的 fail-open。改為「實際無法解析的集合
#: **精確等於**本白名單」的顯式斷言：新增一個無法解析的檔 ⇒ 紅。
_M4_UNPARSABLE_WHITELIST = (
    # 檔首 UTF-8 BOM（U+FEFF）⇒ `ast.parse` 直接 SyntaxError。
    # 可接受：該檔不在生產路徑、且它自己的 import 已由 `git grep` 層面的人工審視覆蓋。
    "scripts/manual_full_system.py",
    # 第 171 行用了 **PEP 701**（Python 3.12+）的嵌套同引號 f-string；
    # 本 repo 的 `.venv` 是 **3.11.15** ⇒ SyntaxError。
    # 可接受：同上（非生產檔；升到 3.12 後本行白名單會自動失效並轉紅，屬預期訊號）。
    "scripts/manual_proactive_bugs.py",
)

#: LIFE-THREAD-M5 接線後：M4 的**生產 importer 白名單恰為**這兩處。
#: ⚠️ 不變量未被放寬：原斷言是「M4 沒有任何生產路徑 import」；M5 的職責**就是**
#: 把 M4 接上生產路徑，故改寫成「**恰好**這兩個、且各自只能是那一種用法」——
#: 比原本的「0 命中」更精確（多一個 importer 就紅）。
_M4_IMPORTER_WHITELIST = (
    "scripts/run_server.py",              # 僅允許 set_llm_proxy 注入行
    "src/soul/life_thread_orchestrator.py",  # 唯一的呼叫端
)


def _parse_py(path: Path):
    """回 `(tree, None)`；無法解析回 `(None, "ExcType: msg")` —— **不吞、不跳過**（F5）。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001 - 原因字串要進斷言訊息
        return None, f"{type(e).__name__}: {e}"


def _scan_py_sources():
    """掃 `_M4_SCAN_BASES/**/*.py`，回 `({相對路徑: AST}, [無法解析的相對路徑])`。

    🔴 F5：無法解析的檔案一律**顯式收集**（不再 `continue` 靜默跳過），
    由 `test_m4_unparsable_py_files_match_explicit_whitelist` 以精確等值斷言釘死。
    """
    trees: dict = {}
    unparsable: list[str] = []
    for base in _M4_SCAN_BASES:
        root = _REPO_ROOT / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            tree, _err = _parse_py(path)
            if tree is None:
                unparsable.append(rel)
            else:
                trees[rel] = tree
    return trees, sorted(unparsable)


def _m4_importers() -> list[str]:
    """AST 掃描 `_M4_SCAN_BASES`：只認真正 import M4 的檔案。

    以 **AST import 語句**判定，而非原始文字比對 —— 否則一行**註解**引用模組路徑
    就能讓不變量失效（`src/world/middleware.py:93` 正是這種註解）。
    """
    trees, _unparsable = _scan_py_sources()
    return sorted(rel for rel, tree in trees.items() if _module_imports_m4(tree))


def _m4_entry_callers() -> list[str]:
    """AST 掃描 `_M4_SCAN_BASES`：真正**呼叫** M4 入口 `run_origin_round(...)` 的檔案。

    以 `ast.Call` 的被呼叫名判定（`run_origin_round(...)` / `x.run_origin_round(...)`）；
    註解與**文件字串不算** —— 否則本模組自己的 docstring 會被誤計為 caller。
    """
    trees, _unparsable = _scan_py_sources()
    hits: list[str] = []
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "run_origin_round") or (
                isinstance(func, ast.Attribute) and func.attr == "run_origin_round"
            ):
                hits.append(rel)
                break
    return sorted(hits)


def _module_imports_m4(tree) -> bool:
    """該 AST 是否**真正 import** M4（`ast.Import` / `ast.ImportFrom` 的模組名與 alias 名）。

    三種寫法都要 DETECT：`import src.soul.life_thread_origins`、
    `from src.soul import life_thread_origins`、
    `from src.soul.life_thread_origins import run_origin_round`。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == MODULE_STEM or alias.name.endswith("." + MODULE_STEM):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == MODULE_STEM or mod.endswith("." + MODULE_STEM):
                return True
            for alias in node.names:
                if alias.name == MODULE_STEM:
                    return True
    return False


def test_module_is_not_wired_into_production_paths(soul_env):
    """§10.1（M5 改寫）：M4 的生產 importer **白名單恰為** orchestrator ＋ run_server。

    前身是本檔 `:766-778` 的 `git grep <MODULE_STEM>` 文字比對，那支有**兩個**缺陷：

    1. **假綠**：`subprocess.run(..., text=True)` 未指定 `encoding`，本機 locale＝cp950
       ⇒ reader thread `UnicodeDecodeError` ⇒ `proc.stdout is None` ⇒
       `(proc.stdout or "")` 為 `""` ⇒ 斷言**空轉通過**。以 `encoding="utf-8"` 重跑，
       真實輸出**非空**（`src/world/middleware.py:93` 一行**註解**引用了模組路徑）。
    2. **假陽性**：文字比對會被「一行註解」打穿（同上），也會被字串誤觸。

    故改為 **AST import 掃描**（只認 import 語句）＋ **恰好等於白名單**。
    """
    assert _m4_importers() == list(_M4_IMPORTER_WHITELIST)


def test_m4_importer_scan_has_teeth(tmp_path):
    """牙齒證明（反例構造）：真 import ⇒ DETECT；純註解／字串 ⇒ MISS。"""
    (tmp_path / "real_from.py").write_text(
        f"from src.soul import {MODULE_STEM}\n", encoding="utf-8"
    )
    (tmp_path / "real_import.py").write_text(
        f"import src.soul.{MODULE_STEM}\n", encoding="utf-8"
    )
    (tmp_path / "only_comment.py").write_text(
        f"# 上限與讀取端 `src/soul/{MODULE_STEM}.py:103` 一致。\nx = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "only_string.py").write_text(
        f'DOC = "src/soul/{MODULE_STEM}.py"\n', encoding="utf-8"
    )
    detected = []
    for path in sorted(tmp_path.rglob("*.py")):
        if _module_imports_m4(ast.parse(path.read_text(encoding="utf-8"))):
            detected.append(path.name)
    assert detected == ["real_from.py", "real_import.py"], detected
    assert "only_comment.py" not in detected
    assert "only_string.py" not in detected
    # 對照：文字比對**會**把註解檔誤判為命中 ⇒ 證明放棄文字比對是必要的收緊
    assert MODULE_STEM in (tmp_path / "only_comment.py").read_text(encoding="utf-8")


def test_m4_guard_scan_covers_configs_and_clients():
    """🔴 F4：護欄掃描基底**精確等於** `("src","scripts","configs","clients")`。

    M5 重寫把基底縮成 `("src","scripts")`，而被取代的
    `git grep <MODULE_STEM> -- src scripts configs` 原本**含 `configs`**
    ⇒ 覆蓋變窄且**無替代測試**（M3 有 `test_t7_non_python_dirs_still_zero_reference`）。
    這裡以精確清單等值把覆蓋面釘死（AST 掃描、非子字串比對）。
    """
    assert _M4_SCAN_BASES == ("src", "scripts", "configs", "clients")


def test_m4_unparsable_py_files_match_explicit_whitelist():
    """🔴 F5：`ast.parse` 無法解析的檔案集合必須**精確等於**白名單（不得靜默跳過）。

    舊版 `except Exception: continue` ⇒ 壞檔內的真 import **完全隱形**（fail-open）；
    新出現的無法解析檔 ⇒ 本斷言紅，逼人回來補白名單與一行理由。
    """
    _trees, unparsable = _scan_py_sources()
    assert unparsable == list(_M4_UNPARSABLE_WHITELIST)


def test_unparsable_detection_has_teeth(tmp_path):
    """牙齒證明：語法壞檔 ⇒ 落到「無法解析」那一側（舊版是被 `continue` 吞掉的）。"""
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n", encoding="utf-8")

    tree_ok, err_ok = _parse_py(good)
    assert tree_ok is not None and err_ok is None

    tree_bad, err_bad = _parse_py(bad)
    assert tree_bad is None, "壞檔不得回 AST"
    assert err_bad and "SyntaxError" in err_bad, err_bad


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)  # legacy 寫法**刻意**重現解碼崩潰 ⇒ 靜音該 thread 例外警告
def test_legacy_git_grep_false_green_is_fixed(soul_env):
    """🔴 假綠→真紅的前後對比（本測試存在的理由，不是為了變綠）。

    `:766-778` 舊版用 `subprocess.run(..., text=True)`（**未指定 `encoding`**）跑
    `git grep`：本機 locale＝cp950 ⇒ reader thread `UnicodeDecodeError` ⇒
    `proc.stdout is None` ⇒ `(proc.stdout or "") == ""` ⇒ 斷言**空轉通過**（假綠）。

    本測試以兩條互相獨立的證據把「假綠」釘死：

    (a) **機制證據（決定性，與 locale 無關）**：先取 raw bytes（永不解碼崩潰），
        再分別用 `cp950` 與 `utf-8` 解碼同一份輸出 —— 前者**必須 raise**、
        後者**必須成功且非空**。這正是 legacy 寫法空轉的成因。
    (b) **實測證據**：真的用 legacy 寫法呼叫一次（cp950 locale 下 stdout 必為 None），
        再用 `encoding="utf-8"` 呼叫一次（必讀到非空真實輸出）。
    """
    cmd = ["git", "grep", "-n", MODULE_STEM, "--", "src", "scripts", "configs"]

    # ── (a) 機制證據：同一份 bytes，cp950 崩、utf-8 通 ──────────
    raw = subprocess.run(cmd, cwd=str(_REPO_ROOT), capture_output=True)
    assert raw.stdout, "git grep 必須有輸出（否則後續對比無意義）"
    with pytest.raises(UnicodeDecodeError):
        raw.stdout.decode("cp950")  # ← legacy text=True 的解碼路徑在這裡崩
    real_out = raw.stdout.decode("utf-8")
    assert MODULE_STEM in real_out
    assert "middleware.py" in real_out, "註解引用是文字比對的假陽性來源"

    # ── (b) 實測證據：legacy 空轉 vs utf-8 真紅 ──────────────
    legacy_enc = locale.getpreferredencoding(False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        legacy = subprocess.run(cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True)
    fixed = subprocess.run(
        cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    fixed_out = (fixed.stdout or "").strip()
    assert fixed_out != "", "以 utf-8 重跑必須讀到真實輸出（證明舊版 0 命中是假的）"

    if legacy_enc.lower().replace("-", "").replace("_", "") not in ("utf8", "cp65001"):
        # cp950 locale：legacy 讀不到任何東西 ⇒ `(stdout or "") == ""` ⇒ 假綠
        assert legacy.stdout is None, (
            f"locale={legacy_enc} 應使 legacy text=True 讀不到 stdout（假綠的成因）"
        )

    # 🔴 明文邊界：不得為了讓本測試變綠去改 `src/world/middleware.py`
    for line in fixed_out.splitlines():
        rel = line.split(":", 1)[0].replace("\\", "/")
        assert rel in _M4_IMPORTER_WHITELIST or rel == "src/world/middleware.py", (
            f"出現白名單外的 M4 引用：{line}"
        )


def test_orchestrator_is_the_m4_call_site(soul_env):
    """M5 職責：orchestrator 是 M4 的**唯一呼叫端**，且必須經 `run_origin_round`。"""
    src = (
        _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "life_thread_origins" in src
    assert "run_origin_round(" in src
    assert "set_llm_proxy(" not in src, "orchestrator 不得自行注入 LLM proxy"
    assert "load_soul_context(" in src


def test_run_server_injection_is_set_llm_proxy_only(soul_env):
    """M5：`scripts/run_server.py` 對 M4 的**唯一**用法是 `set_llm_proxy` 注入行。

    🔴 不注入的後果：`_find_llm_proxy()` 恆 None ⇒ `run_origin_round` 直接 return
    （僅 1 行 warning）⇒ 管線看似活著但**永不產線頭**。
    """
    src = (_REPO_ROOT / "scripts" / "run_server.py").read_text(encoding="utf-8")
    assert "life_thread_origins" in src
    assert "set_llm_proxy(llm)" in src
    # 精確到「對注入物件的屬性呼叫」，故註解／文件字串提及不會誤觸
    assert "_lt_origins.run_origin_round" not in src, "run_server 不得直接呼叫 M4 入口"
    assert "_lt_origins.build_origin_prompt" not in src
    assert "_lt_origins.apply_actions" not in src


def test_no_scheduler_or_timer_wiring(soul_env):
    """§10.1／INV-2：scheduler 內**不得**出現 M4 模組名（M5 經 lazy import orchestrator）。

    可測量的不變量：接線一律經過 `life_thread_orchestrator`（M5 介接層），
    scheduler 自己**不得**認識 M4；且 orchestrator 不得引入任何定時器。
    """
    # scheduler 只認識 orchestrator，不認識 M4 本體
    sched = (_REPO_ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    assert MODULE_STEM not in sched, "scheduler 不得直接引用 M4（必須經 orchestrator）"
    assert "life_thread_orchestrator" in sched, "scheduler 必須掛載 orchestrator"

    # 設定檔與 M1 本體不得被 M5 改動
    for rel in ("configs/default.yaml", "src/soul/life_threads.py"):
        assert MODULE_STEM not in (_REPO_ROOT / rel).read_text(encoding="utf-8"), \
            f"{rel} 不得被改動接線"

    src = MODULE_PATH.read_text(encoding="utf-8")
    for banned in ("asyncio.create_task", "threading.Timer", "call_later",
                   "APScheduler", "setInterval", "asyncio.sleep"):
        assert banned not in src, f"不得引入 {banned}"

    # orchestrator 也不得引入定時器（0 新定時器鐵律延伸到介接層）。
    # ⚠️ 以 **AST 呼叫**判定，不用原始文字 —— 否則文件字串裡「不新增 asyncio.sleep」
    #    這句自我聲明反而會讓護欄偽紅（文字比對的典型陷阱）。
    orch = _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
    offenders = _timer_calls_in(orch)
    assert offenders == [], f"orchestrator 不得引入定時器／排程呼叫：{offenders}"


#: 排程／定時相關的**呼叫**屬性名（AST 層判定）。
_BANNED_TIMER_ATTRS = frozenset({
    "sleep", "create_task", "call_later", "call_at", "call_soon", "wait_for", "Timer",
})


def _timer_calls_in(path: Path) -> list[str]:
    """AST：找出真正的定時器**呼叫**（`x.sleep(...)` / `x.call_later(...)` …）。

    只認 `ast.Call` 的屬性名 ⇒ 註解與文件字串**不會**誤判，真呼叫**一定**命中。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _BANNED_TIMER_ATTRS:
                found.append(node.func.attr)
    return sorted(set(found))


def test_timer_scan_has_teeth(tmp_path):
    """牙齒證明：真 `asyncio.sleep(...)` ⇒ DETECT；文件字串提及 ⇒ MISS。"""
    real = tmp_path / "real.py"
    real.write_text("import asyncio\n\nasync def f():\n    await asyncio.sleep(30)\n",
                    encoding="utf-8")
    doc = tmp_path / "doc.py"
    doc.write_text('"""本模組 0 新定時器：不用 asyncio.sleep / call_later。"""\nx = 1\n',
                   encoding="utf-8")
    assert _timer_calls_in(real) == ["sleep"]
    assert _timer_calls_in(doc) == []


def test_module_declares_m5_wiring_and_reuses_existing_channel(soul_env):
    """文件紀律 ＋ **接線後**結構不變量（F3 改寫）。

    前身是 `test_module_declares_unwired_and_reuses_existing_channel`，它靠
    `assert "未接線零件" in src` 維護 M4 docstring 的「未接線」自我聲明 —— M5 接線後
    那句話已成**事實錯誤**，而該斷言反而**強制保留假話**（修文件 → 變紅，逆向誘因）。

    改為斷言**接線後為真**的四件事：

    1. `src/soul/life_thread_orchestrator.py` 是 M4 入口 `run_origin_round(...)` 的
       **唯一**生產 caller（AST 判定被呼叫名，文件字串不算）；
    2. `scripts/run_server.py` 對 M4 的唯一用法 ＝ `set_llm_proxy` 注入
       （不得直接呼叫入口）；
    3. 0 新 provider／0 新網路通道（沿用既有 `generate_text`）；
    4. docstring 必須自我聲明**已接線／被生產呼叫**，且**不得**再殘留「未接線」字樣
       —— 新增這一則是為了防止過時文字再次靜默殘留（本測試原版的病根）。
    """
    src = MODULE_PATH.read_text(encoding="utf-8")

    # ── 1. 唯一生產 caller ───────────────────────────────────
    assert _m4_entry_callers() == ["src/soul/life_thread_orchestrator.py"]

    # ── 2. run_server 只做 set_llm_proxy 注入 ─────────────────
    run_server = (_REPO_ROOT / "scripts" / "run_server.py").read_text(encoding="utf-8")
    assert "_lt_origins.set_llm_proxy(llm)" in run_server, "run_server 必須注入 LLM proxy"
    assert "_lt_origins.run_origin_round" not in run_server, "run_server 不得呼叫 M4 入口"

    # ── 3. 0 新 provider／0 新通道 ────────────────────────────
    assert "0 新 provider" in src
    assert "generate_text" in src, "必須沿用既有 LLM 通道（§8.1）"
    for net in ("httpx", "requests", "urllib.request", "aiohttp", "socket"):
        assert f"import {net}" not in src, f"不得新增網路通道：{net}"

    # ── 4. 文件必須與接線後的事實一致 ─────────────────────────
    doc = m4.__doc__ or ""
    assert "life_thread_orchestrator" in doc, (
        "docstring 必須寫出**接線後**的唯一生產呼叫端"
    )
    assert "生產呼叫" in doc, "docstring 必須聲明本模組已被生產呼叫"
    assert "未接線" not in doc, (
        "🔴 M5 接線後不得再自我聲明『未接線』（已成事實錯誤；此斷言防過時文字殘留）"
    )


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
