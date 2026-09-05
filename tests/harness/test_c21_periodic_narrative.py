"""
tests/harness/test_c21_periodic_narrative.py — C-2.1 週期敘事昇華 + 護欄驗收

目标: 驗證 PeriodicNarrativeSublimator（契約 docs/C-2.1-COMMITMENT-AND-NARRATIVE-
CONTRACT.md §5 決策 D3/D4/D5/D6/D7）:

  - 週記: ISO 週判據 1 次/週; 冪等鍵 periodic:{YYYY-Www}; 窗內全聚合不挑選;
    空聚合 fail-closed; LLM 失敗 fail-closed
  - 紀念日: perception_trace accepted calendar_event「今日事件」觸發; 聚合往年今日;
    空聚合 fail-closed; 冪等鍵 periodic:memorial:{YYYY-MM-DD}
  - 身分防火牆: 他者 diary / trace 0 內化; 沉澱事件 actor_id==self、
    source_system=="system"（防線 3 複核）; ts 為 UTC ISO-8601
  - scheduler night slot 檢查鏈 additive 分支（0 新定時器 0 新通道）, 同晚可並存
  - 護欄（TL-8 同款）: AST 0 新定時器 / 0 直通 publish / No-Scoring; 運行時
    0 直寫 SAGE facts

Frozen contract 边界 (0 change): Agency / TriggerEnvelope / InnerLifeEvent /
4 handlers / SAGE 写入逻辑 / MOTIVE / diary 排程 全部不触碰。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/harness/test_c21_periodic_narrative.py -v
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from src.goals.models import GOAL_STATE_COMPLETED, Goal
from src.goals.narrative_sublimator import PeriodicNarrativeSublimator
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root
from src.soul.scheduler import SoulScheduler
from src.timezone_utils import LOCAL_TZ

AGENT = "agent_c21p"
OTHER = "agent_other"
ROOT = Path(__file__).resolve().parents[2]

TS_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)$"
)


# ───────────────────────────────────────────────────────────
# Fixture: 隔离 data_root（0 production data 接触）
# ───────────────────────────────────────────────────────────


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    yield tmp_path
    reset_data_root()


# ───────────────────────────────────────────────────────────
# 数据触点 helpers
# ───────────────────────────────────────────────────────────


def _loc(hour: int = 22, day_delta: int = 0) -> datetime:
    """本地時區固定時刻（2026-09-06 起算; 22:00 = night slot 當晚）。"""
    return datetime(2026, 9, 6, hour, tzinfo=LOCAL_TZ) + timedelta(days=day_delta)


def _monday(dt: datetime):
    return dt.date() - timedelta(days=dt.weekday())


def _weekly_key(dt: datetime) -> str:
    iso = dt.astimezone(LOCAL_TZ).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _llm_capture() -> tuple[Callable, List[str]]:
    prompts: List[str] = []

    async def stub(messages, agent_id, max_tokens, temperature):
        prompts.append(str(messages[0]["content"]))
        return json.dumps({"title": "标题", "narrative": "叙事内容"})

    return stub, prompts


def _llm_fail():
    async def stub(messages, agent_id, max_tokens, temperature):
        return None

    return stub


def _write_diary(tmp_path: Path, agent: str, date_iso: str, entries: List[Dict]) -> None:
    path = tmp_path / "soul" / agent / "diary" / f"{date_iso}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    path.write_text(lines + "\n", encoding="utf-8")


def _write_trace(tmp_path: Path, records: List[Dict]) -> None:
    path = tmp_path / "inner_life" / "trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_perception(tmp_path: Path, records: List[Dict]) -> None:
    path = tmp_path / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _insert_goal(tmp_path: Path, *, title: str, state_updated_at: float) -> str:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    gid = uuid.uuid4().hex
    g = Goal(
        goal_id=gid,
        agent_id=AGENT,
        axis="bryan",
        title=title,
        description="",
        seed_source_ref="relationship:user_bryan",
        state=GOAL_STATE_COMPLETED,
        state_updated_at=state_updated_at,
        created_at=state_updated_at,
        advance_count=2,
    )
    store.upsert_goal(g)
    store.flush()
    store.close()
    return gid


def _trace_records(tmp_path: Path) -> List[Dict]:
    path = tmp_path / "inner_life" / "trace.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _periodic_records(tmp_path: Path) -> List[Dict]:
    out = []
    for r in _trace_records(tmp_path):
        prov = r.get("provenance") or {}
        ref = prov.get("trace_ref") or ""
        if ref.startswith("periodic:"):
            out.append(r)
    return out


def _facts_count(tmp_path: Path) -> int:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────
# 週記
# ───────────────────────────────────────────────────────────


class TestWeekly:
    def test_weekly_sediments_once_per_iso_week_and_next_week(self, iso_env):
        tmp = iso_env
        now = _loc(22)
        mon = _monday(now)
        # 本週素材: 自己的 diary（必窗內）+ 自己 trace + 本週 goal 終態
        _write_diary(tmp, AGENT, mon.isoformat(), [
            {"ts": f"{mon.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "週一早上練琴", "source": "llm"},
            {"ts": f"{mon.isoformat()}T12:00:00+00:00", "slot": "night",
             "content": "晚上散步", "source": "llm"},
        ])
        _write_trace(tmp, [{
            "event_id": uuid.uuid4().hex, "session_id": None, "correlation_id": None,
            "parent_event_id": None, "ts": f"{mon.isoformat()}T10:00:00+00:00",
            "provenance": {"trigger_type": "dream:dream", "actor_id": AGENT,
                           "source_system": "dream", "trace_ref": None,
                           "extras": {"subject": "夢到了海"}},
            "lineage_depth": 0, "lineage_path": "x",
            "source_world_event_novelty_id": None,
        }])
        _insert_goal(tmp, title="面试关心", state_updated_at=now.timestamp() - 3600)

        stub, prompts = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        event_id = asyncio.run(sublimator.sublimate_weekly(now=now))
        assert event_id is not None
        key = f"periodic:{_weekly_key(now)}"
        recs = _periodic_records(tmp)
        assert len(recs) == 1
        r = recs[0]
        prov = r["provenance"]
        assert prov["trace_ref"] == key
        assert prov["actor_id"] == AGENT          # 身分防火牆: 沉澱事件屬於自己
        assert prov["source_system"] == "system"
        assert prov["trigger_type"] == "system"   # 0 新 trigger_type
        assert TS_PATTERN.match(r["ts"])          # UTC ISO-8601
        assert r["ts"].endswith("+00:00") or r["ts"].endswith("Z")
        extras = prov["extras"]
        assert extras["period"] == "weekly"
        assert extras["period_start"] == mon.isoformat()
        assert extras["period_end"] == now.date().isoformat()
        assert "週一早上練琴" in prompts[0]
        assert "夢到了海" in prompts[0]
        assert "面试关心" in prompts[0]           # 本週 goal 終態全聚合

        # 同 ISO 週二次觸發 → 0 二次沉澱（冪等鍵判重）
        again = asyncio.run(sublimator.sublimate_weekly(now=now))
        assert again is None
        assert len(_periodic_records(tmp)) == 1

        # 下週（新素材）→ 第 2 次沉澱（頻率 ~1 次/週）
        now2 = now + timedelta(days=7)
        mon2 = _monday(now2)
        _write_diary(tmp, AGENT, mon2.isoformat(), [
            {"ts": f"{mon2.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "次週早上的事", "source": "llm"},
        ])
        event2 = asyncio.run(sublimator.sublimate_weekly(now=now2))
        assert event2 is not None
        assert event2 != event_id
        keys = [r["provenance"]["trace_ref"] for r in _periodic_records(tmp)]
        assert keys == [f"periodic:{_weekly_key(now)}", f"periodic:{_weekly_key(now2)}"]

    def test_weekly_empty_aggregation_fail_closed(self, iso_env):
        """空聚合（0 素材）→ fail-closed 不沉澱（0 半成品, 0 編造）。"""
        tmp = iso_env
        stub, _ = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        result = asyncio.run(sublimator.sublimate_weekly(now=_loc(22)))
        assert result is None
        assert _periodic_records(tmp) == []

    def test_weekly_llm_failure_fail_closed(self, iso_env):
        tmp = iso_env
        now = _loc(22)
        mon = _monday(now)
        _write_diary(tmp, AGENT, mon.isoformat(), [
            {"ts": f"{mon.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "素材存在但 LLM 壞了", "source": "llm"},
        ])
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=_llm_fail())
        result = asyncio.run(sublimator.sublimate_weekly(now=now))
        assert result is None
        assert _periodic_records(tmp) == []
        # 冪等鍵未寫 → 次日重試仍可觸發（不鎖死本週）
        stub2, _ = _llm_capture()
        sublimator2 = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub2)
        result2 = asyncio.run(sublimator2.sublimate_weekly(now=now))
        assert result2 is not None

    def test_weekly_identity_firewall_others_zero(self, iso_env):
        """身分防火牆: 他者 diary/trace 0 內化（他者經歷 0 進聚合窗）。"""
        tmp = iso_env
        now = _loc(22)
        mon = _monday(now)
        _write_diary(tmp, OTHER, mon.isoformat(), [
            {"ts": f"{mon.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "OTHER_SECRET_MARKER 他者的私密經歷", "source": "llm"},
        ])
        _write_diary(tmp, AGENT, mon.isoformat(), [
            {"ts": f"{mon.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "SELF_MARKER 自己的平常一天", "source": "llm"},
        ])
        _write_trace(tmp, [{
            "event_id": uuid.uuid4().hex, "session_id": None, "correlation_id": None,
            "parent_event_id": None, "ts": f"{mon.isoformat()}T09:00:00+00:00",
            "provenance": {"trigger_type": "diary:morning", "actor_id": OTHER,
                           "source_system": "diary", "trace_ref": None,
                           "extras": {"note": "OTHER_TRACE_MARKER"}},
            "lineage_depth": 0, "lineage_path": "x",
            "source_world_event_novelty_id": None,
        }])
        stub, prompts = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        event_id = asyncio.run(sublimator.sublimate_weekly(now=now))
        assert event_id is not None
        assert "SELF_MARKER" in prompts[0]
        assert "OTHER_SECRET_MARKER" not in prompts[0]
        assert "OTHER_TRACE_MARKER" not in prompts[0]
        r = _periodic_records(tmp)[0]
        assert r["provenance"]["actor_id"] == AGENT


# ───────────────────────────────────────────────────────────
# 紀念日
# ───────────────────────────────────────────────────────────


class TestMemorial:
    def test_memorial_today_event_with_past_diaries(self, iso_env):
        tmp = iso_env
        now = _loc(22)
        today = now.date()
        # 觸發依據: 今晚有「事件日 == 今天」的 accepted calendar_event
        _write_perception(tmp, [{
            "event_type": "calendar_event", "accepted": True,
            "novelty_id": "cal_anniversary", "timestamp": now.astimezone(
                timezone.utc).isoformat(),
        }])
        # 往年今日的自己的 diary（同 M-D 不同年）
        y1, y2 = today.year - 1, today.year - 2
        _write_diary(tmp, AGENT, f"{y1}-{today.month:02d}-{today.day:02d}", [
            {"ts": f"{y1}-{today.month:02d}-{today.day:02d}T08:00:00+00:00",
             "slot": "morning", "content": "去年今日的日记", "source": "llm"},
        ])
        _write_diary(tmp, AGENT, f"{y2}-{today.month:02d}-{today.day:02d}", [
            {"ts": f"{y2}-{today.month:02d}-{today.day:02d}T08:00:00+00:00",
             "slot": "morning", "content": "前年今日的日记", "source": "llm"},
        ])
        # 他者往年今日 → 0 內化
        _write_diary(tmp, OTHER, f"{y1}-{today.month:02d}-{today.day:02d}", [
            {"ts": f"{y1}-{today.month:02d}-{today.day:02d}T08:00:00+00:00",
             "slot": "morning", "content": "MEMORIAL_OTHER_MARKER", "source": "llm"},
        ])

        stub, prompts = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        event_id = asyncio.run(sublimator.sublimate_memorial(now=now))
        assert event_id is not None
        recs = _periodic_records(tmp)
        assert len(recs) == 1
        r = recs[0]
        prov = r["provenance"]
        assert prov["trace_ref"] == f"periodic:memorial:{today.isoformat()}"
        assert prov["actor_id"] == AGENT
        assert prov["source_system"] == "system"
        assert TS_PATTERN.match(r["ts"])
        extras = prov["extras"]
        assert extras["period"] == "memorial"
        assert extras["period_start"] == f"{y2}-{today.month:02d}-{today.day:02d}"
        assert extras["period_end"] == f"{y1}-{today.month:02d}-{today.day:02d}"
        assert "cal_anniversary" in extras["event_summary"]
        assert "去年今日的日记" in prompts[0]
        assert "前年今日的日记" in prompts[0]
        assert "MEMORIAL_OTHER_MARKER" not in prompts[0]  # 他者 0 內化

        # 同日二次觸發 → 0 二次反芻（冪等鍵）
        again = asyncio.run(sublimator.sublimate_memorial(now=now))
        assert again is None
        assert len(_periodic_records(tmp)) == 1

    def test_memorial_no_event_no_sediment(self, iso_env):
        tmp = iso_env
        now = _loc(22)
        today = now.date()
        _write_diary(tmp, AGENT, f"{today.year - 1}-{today.month:02d}-{today.day:02d}", [
            {"ts": "2025-01-01T08:00:00+00:00", "slot": "morning",
             "content": "有往年日記但今天沒有 calendar 事件", "source": "llm"},
        ])
        stub, _ = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        result = asyncio.run(sublimator.sublimate_memorial(now=now))
        assert result is None
        assert _periodic_records(tmp) == []

    def test_memorial_empty_past_fail_closed(self, iso_env):
        """有今日事件但往年聚合空 → fail-closed 不沉澱（紀念日本身極少為預期）。"""
        tmp = iso_env
        now = _loc(22)
        _write_perception(tmp, [{
            "event_type": "calendar_event", "accepted": True,
            "novelty_id": "cal_x", "timestamp": now.astimezone(
                timezone.utc).isoformat(),
        }])
        stub, _ = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        result = asyncio.run(sublimator.sublimate_memorial(now=now))
        assert result is None
        assert _periodic_records(tmp) == []

    def test_memorial_rejected_event_not_trigger(self, iso_env):
        """accepted=False 的 calendar_event → 不觸發。"""
        tmp = iso_env
        now = _loc(22)
        today = now.date()
        _write_perception(tmp, [{
            "event_type": "calendar_event", "accepted": False,
            "novelty_id": "cal_rej", "timestamp": now.astimezone(
                timezone.utc).isoformat(),
        }])
        _write_diary(tmp, AGENT, f"{today.year - 1}-{today.month:02d}-{today.day:02d}", [
            {"ts": "2025-01-01T08:00:00+00:00", "slot": "morning",
             "content": "往年素材", "source": "llm"},
        ])
        stub, _ = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        result = asyncio.run(sublimator.sublimate_memorial(now=now))
        assert result is None
        assert _periodic_records(tmp) == []


# ───────────────────────────────────────────────────────────
# scheduler night slot additive 分支（0 新定時器）
# ───────────────────────────────────────────────────────────


class TestSchedulerMount:
    def test_night_slot_fires_both_same_night(self, iso_env, monkeypatch):
        """night slot（22:00 當晚）→ 週記 + 紀念日同晚各沉澱 1 次（獨立冪等鍵）。"""
        tmp = iso_env
        now = _loc(22)
        mon = _monday(now)
        _write_diary(tmp, AGENT, mon.isoformat(), [
            {"ts": f"{mon.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "夜間檢查素材", "source": "llm"},
        ])
        _write_perception(tmp, [{
            "event_type": "calendar_event", "accepted": True,
            "novelty_id": "cal_night", "timestamp": now.astimezone(
                timezone.utc).isoformat(),
        }])
        today = now.date()
        _write_diary(tmp, AGENT, f"{today.year - 1}-{today.month:02d}-{today.day:02d}", [
            {"ts": "2025-01-01T08:00:00+00:00", "slot": "morning",
             "content": "往年今日素材", "source": "llm"},
        ])

        import src.soul.scheduler as scheduler_mod
        import src.soul.motive as motive_mod
        monkeypatch.setattr(scheduler_mod, "now_local", lambda: now)
        # scheduler 不注入 llm_call（生產走既有 default proxy）→ 測試替身綁定
        # default 通道, 驗證 additive 分支真實驗過 sublimator 全鏈（0 直寫）
        stub, _ = _llm_capture()
        monkeypatch.setattr(motive_mod, "_default_llm_call", stub)
        scheduler = SoulScheduler()
        scheduler.register(AGENT)
        asyncio.run(scheduler._fire_periodic_narrative())
        recs = _periodic_records(tmp)
        refs = sorted(r["provenance"]["trace_ref"] for r in recs)
        assert refs == [
            f"periodic:{_weekly_key(now)}",
            f"periodic:memorial:{today.isoformat()}",
        ]

    def test_daytime_zero_action(self, iso_env, monkeypatch):
        """非 night slot（白天）→ 0 動作（判據結構規則）。"""
        tmp = iso_env
        now_day = _loc(10)
        _write_diary(tmp, AGENT, _monday(now_day).isoformat(), [
            {"ts": "2026-01-01T08:00:00+00:00", "slot": "morning",
             "content": "素材再多白天也不觸發", "source": "llm"},
        ])
        import src.soul.scheduler as scheduler_mod
        monkeypatch.setattr(scheduler_mod, "now_local", lambda: now_day)
        scheduler = SoulScheduler()
        scheduler.register(AGENT)
        asyncio.run(scheduler._fire_periodic_narrative())
        assert _periodic_records(tmp) == []

    def test_no_agents_no_action(self, iso_env, monkeypatch):
        tmp = iso_env
        import src.soul.scheduler as scheduler_mod
        monkeypatch.setattr(scheduler_mod, "now_local", lambda: _loc(22))
        scheduler = SoulScheduler()
        asyncio.run(scheduler._fire_periodic_narrative())
        assert _periodic_records(tmp) == []


# ───────────────────────────────────────────────────────────
# 護欄（TL-8 同款 AST + 運行時 0 直寫 facts）
# ───────────────────────────────────────────────────────────

_FORBIDDEN_CALL_TOKENS = (
    "publish", "AGENCY_TRIGGER", "handler", "tool_registry", "actuator",
    "call_tool", "mark_transmitted", "send_message", "_fire",
)
_ASYNCIO_TOKENS: tuple = ("create_task", "ensure_future", "call_later",
                          "call_soon", "Timer")


def _audit_no_publish(path: Path) -> List[str]:
    src = path.read_text(encoding="utf-8")
    issues: List[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"源码解析失败: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if isinstance(name, str) and any(t in name for t in _FORBIDDEN_CALL_TOKENS):
                issues.append(f"禁止调用 {name}")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if any(t in alias.name for t in _FORBIDDEN_CALL_TOKENS):
                    issues.append(f"禁止导入 {alias.name}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(t in alias.name for t in _FORBIDDEN_CALL_TOKENS):
                    issues.append(f"禁止导入 {alias.name}")
    return issues


def _audit_no_timers(path: Path) -> List[str]:
    src = path.read_text(encoding="utf-8")
    issues: List[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"源码解析失败: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if isinstance(name, str) and name in _ASYNCIO_TOKENS:
                issues.append(f"禁止后台任务调用 {name}")
        if isinstance(node, ast.Import) and any(
            a.name == "asyncio" or a.name.startswith("asyncio.") for a in node.names
        ):
            issues.append("禁止导入异步调度模块")
        if isinstance(node, ast.ImportFrom) and (
            node.module == "asyncio" or (node.module or "").startswith("asyncio.")
        ):
            issues.append(f"禁止导入异步调度模块 {node.module}")
    return issues


_SORT_KEY_ALLOWED_FIELDS = frozenset({
    "timestamp", "last_advanced_at", "last_ts", "created_at", "ts",
    "axis", "goal_id", "event_id", "fact_id", "node_id",
})


def _audit_no_scoring(path: Path) -> List[str]:
    """No-Scoring: 0 score/affinity 字样; sort/min/max 的 key 只引用時間/結構白名單
    （全聚合不挑選 → 本模組預期無 key 排序, 純結構規則）。"""
    src = path.read_text(encoding="utf-8")
    issues: List[str] = []
    if "score" in src or "affinity" in src:
        issues.append("源码含 score/affinity 字样")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"源码解析失败: {e}"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func_name not in ("sort", "min", "max"):
            continue
        lambdas: List[ast.Lambda] = []
        if node.args and isinstance(node.args[0], ast.Lambda):
            lambdas.append(node.args[0])
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Lambda):
                lambdas.append(kw.value)
        for lam in lambdas:
            refs = {
                sub.attr
                for sub in ast.walk(lam)
                if isinstance(sub, ast.Attribute)
            }
            for sub in ast.walk(lam):
                if not isinstance(sub, ast.Subscript):
                    continue
                sl = sub.slice
                if isinstance(sl, ast.Index):
                    sl = sl.value
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    refs.add(sl.value)
            non_allowed = refs - _SORT_KEY_ALLOWED_FIELDS
            if non_allowed:
                issues.append(
                    f"{func_name} key 引用非白名单字段 {sorted(non_allowed)}"
                )
    return issues


def _audit_scheduler_mount() -> List[str]:
    """scheduler.py: 主循環仍只有既有 30s wake（2 處既有 sleep, 0 新定時器）;
    _run_loop 內 while 恰 1 個; _fire_periodic_narrative 以 await 掛為并列分支。"""
    issues: List[str] = []
    src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    sleep_count = src.count("asyncio.sleep(30)")
    if sleep_count > 2:
        issues.append(
            f"asyncio.sleep(30) 出现 {sleep_count} 次（既有基线 = 2: 主循环 + except 恢复）"
        )
    run_loop_start = src.find("async def _run_loop")
    run_loop_end = src.find("async def _goal_scan_all")
    if run_loop_start == -1 or run_loop_end == -1 or run_loop_end <= run_loop_start:
        issues.append("scheduler 主循环/扫描函数定位失败")
    else:
        body = src[run_loop_start:run_loop_end]
        while_count = body.count("while ")
        if while_count != 1:
            issues.append(f"_run_loop 内 while 数量 {while_count}（期望恰 1 个主循环）")
    if "await self._fire_periodic_narrative()" not in src:
        issues.append("_run_loop 未挂载 _fire_periodic_narrative")
    return issues


class TestGuardrails:
    def test_sublimator_no_direct_publish(self):
        path = ROOT / "src" / "goals" / "narrative_sublimator.py"
        assert _audit_no_publish(path) == []

    def test_sublimator_no_timers(self):
        path = ROOT / "src" / "goals" / "narrative_sublimator.py"
        assert _audit_no_timers(path) == []
        assert _audit_scheduler_mount() == []

    def test_sublimator_no_scoring(self):
        path = ROOT / "src" / "goals" / "narrative_sublimator.py"
        assert _audit_no_scoring(path) == []

    def test_zero_direct_facts_runtime(self, iso_env):
        """週記 + 紀念日全鏈沉澱後: facts 表 0 行（0 直寫 SAGE, 只走 producer）。"""
        tmp = iso_env
        now = _loc(22)
        mon = _monday(now)
        today = now.date()
        _write_diary(tmp, AGENT, mon.isoformat(), [
            {"ts": f"{mon.isoformat()}T08:00:00+00:00", "slot": "morning",
             "content": "素材", "source": "llm"},
        ])
        _write_perception(tmp, [{
            "event_type": "calendar_event", "accepted": True,
            "novelty_id": "cal_d", "timestamp": now.astimezone(
                timezone.utc).isoformat(),
        }])
        _write_diary(tmp, AGENT, f"{today.year - 1}-{today.month:02d}-{today.day:02d}", [
            {"ts": "2025-01-01T08:00:00+00:00", "slot": "morning",
             "content": "往年素材", "source": "llm"},
        ])
        stub, _ = _llm_capture()
        sublimator = PeriodicNarrativeSublimator(agent_id=AGENT, llm_call=stub)
        asyncio.run(sublimator.sublimate_weekly(now=now))
        asyncio.run(sublimator.sublimate_memorial(now=now))
        assert len(_periodic_records(tmp)) == 2
        assert _facts_count(tmp) == 0