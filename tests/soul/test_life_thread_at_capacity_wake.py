# tests/soul/test_life_thread_at_capacity_wake.py
# FIX-A1-AUDIT-1（C3 補強）— **容量邊界上的喚醒**：滿 cap 情境的生產整合測試。
#
# 本票要救的生產情境（審計對象＝未 commit 的方案 A 修法）
# ───────────────────────────────────────────────────
# 缺陷原形：舊碼把「容量飽和」短路排在「到期／碰撞／張力」訊號之前 ⇒
# `active_count >= capacity_limit` 時，**到期的線頭也永遠喚不醒**（SLEEP
# ACTIVE_POOL_SATURATED）。方案 A 把訊號判定移到飽和短路之前。
#
# 本檔在 **pytest tmp 隔離環境**用真實 orchestrator 管線驗證：
#   1. `test_at_capacity_*`：`active == cap`（**不放寬容量**，就是預設 cap=2）
#      ＋一條到期線頭 ⇒ 喚醒成立、推進該線頭（`advanced` 非空），且
#      **`created == []`**（`create_thread()` 的容量防線未被繞過；見
#      `src/soul/life_threads.py` 的 §2.6.3 唯一強制點）。
#   2. `test_below_capacity_*`：對照組（`active = cap-1`）⇒ 同一條喚醒路徑
#      仍允許建立新線頭（`created` 非空）。
#
# 🔴 紅線自證
# ───────────
#   * **0 真實 LLM**：M4 一律注入 stub `llm_caller`（`_SpyLLM`）；本檔不含任何
#     真實 provider 端點。
#   * **0 網路**：autouse fixture 把 `HTTP_PROXY`／`HTTPS_PROXY`／`http_proxy`／
#     `https_proxy` 全部指向死代理 `http://127.0.0.1:1`（不可路由），並斷言
#     死代理確實生效 ⇒ 任何外呼都當場失敗而非靜默成功。
#   * **0 生產 `data/**` 寫入**：autouse fixture 把 `SOUL_OS_DATA_DIR` 指向
#     `tmp_path` 並 `reset_data_root()`；`test_00` 以模組真實解析函式自證落點。
#   * **0 行程操作**：不 spawn 外部程式、不殺任何行程、不綁任何埠、不對任何
#     執行中的服務發請求。
#   * **容量不得放寬**：本檔**不使用** `_widen_capacity` 這類 monkeypatch；
#     兩條測試都在**真實容量邊界**上驗證（`lt.capacity(AGENT) == cap` 自證）。
#
# 慣例沿用（**不修改**它們）
# ────────────────────────
#   `tests/soul/test_life_thread_e2e_chain.py`：`iso_env` / `_SpyLLM` /
#     `_patch_soul` / consumed 感知手法（M3 碰撞不成立、M4 種子仍合格）。
#   `tests/soul/test_life_thread_m5_wiring.py`：`iso_env` / `_run` / `reset_state()`。
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_wake_gate as lt_gate  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

#: 合成 agent（不在 `configs/default.yaml` ⇒ `capacity()` 走 fail-closed 預設值）。
AGENT = "agent_cap_boundary"

#: 容量邊界值（= `LIFE_THREAD_ACTIVE_CAP_DEFAULT`）。本檔**不得**放寬它。
CAP = 2

#: 人格上下文（M4 `build_origin_prompt` 的必填項；空 ⇒ 0 LLM ⇒ 管線走不完）。
_SOUL = "我是測試代理人。我清晨會看天氣，習慣把散步路線記下來。"

#: 合成感知的合格事實文字（唯一合法來源 = `extra["summary"]`，§5.2.4）。
_SUMMARY = "今天清晨城東下了第一場雪，路面結了一層薄冰。"

#: 世界碰撞的合格來源（`life_thread_wake_gate.QUALIFYING_WORLD_SOURCES` 之一）。
_SOURCE = "weather"

#: 第 1 輪（D1 08:00 morning）的評估點；第 2 輪（D1 22:00 night）**換 slot** 以
#: 繞開 orchestrator 的 at-most-once 章（鍵為 `agent:slot:date`）。
_MORNING_D1 = datetime(2026, 9, 6, 8, 0, tzinfo=LOCAL_TZ)
_NIGHT_D1 = datetime(2026, 9, 6, 22, 0, tzinfo=LOCAL_TZ)

#: stub LLM（第 1 輪）：建出「到期線頭」A；`next_check_hours = 1`（clamp 下限）
#: ⇒ `check_after_ts` = D1 09:00 本地 ⇒ D1 22:00 時**已到期**。
_ACTION_CREATE_DUE = {
    "op": "create",
    "title": "雪後的路面",
    "narrative_content": "我看見薄冰，於是把散步的路線改走有日照的南側。",
    "next_check_hours": 1,
}

#: stub LLM（第 2 輪）：推進到期線頭 ＋ **同時**要求建一條新線頭。
#: 這正是生產情境：喚醒是真的，但「建新線頭」必須由 `create_thread()` 的容量
#: 防線獨立裁決（滿 cap ⇒ 拒絕）。
_ACTION_ADVANCE_DUE = {
    "op": "advance",
    "narrative_content": "我把窗上的霜擦掉，決定今天不出遠門。",
    "next_check_hours": 6,
}
_ACTION_CREATE_EXTRA = {
    "op": "create",
    "title": "屋簷下的水痕",
    "narrative_content": "我在屋簷下數著水滴，想著春天還有多遠。",
    "next_check_hours": 6,
}

#: 第 2 條線頭（只由測試直接建立，用來把 active 推到 cap 邊界）**刻意未到期**。
_UNRELATED_THREAD_TITLE = "南側的日照路線"
_UNRELATED_THREAD_NARRATIVE = "我把散步路線改到南側，讓太陽把影子拉長。"

#: 死代理（不可路由）——「0 外呼」的客觀證明（本機不可能有人監聽的埠）。
_DEAD_PROXY = "http://127.0.0.1:1"
_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


# ══════════════════════════════════════════════════════════════
# fixtures / helpers
# ══════════════════════════════════════════════════════════════


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """顯式隔離資料根（0 生產 `data/**` 接觸）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    yield tmp_path
    reset_data_root()


@pytest.fixture(autouse=True)
def _dead_proxy(monkeypatch):
    """🔴 全程死代理：任何真實外呼都必須當場失敗，而不是靜默成功。"""
    for key in _PROXY_ENV_KEYS:
        monkeypatch.setenv(key, _DEAD_PROXY)
    import os

    live = {k: os.environ.get(k) for k in _PROXY_ENV_KEYS}
    assert set(live.values()) == {_DEAD_PROXY}, f"死代理未生效：{live}"
    yield live


@pytest.fixture(autouse=True)
def _reset_process_state():
    """清空 orchestrator 的 at-most-once 章（跨測試不互相污染）。"""
    m5.reset_state()
    try:
        yield
    finally:
        m5.reset_state()


def _iso(moment: datetime) -> str:
    """UTC ISO-8601 字串（感知 trace 與 `check_after_ts` 的時間欄位形狀）。"""
    return moment.astimezone(timezone.utc).isoformat()


def _perception_record(when: datetime) -> Dict[str, Any]:
    """一筆**合格**的合成感知紀錄（對齊 `_scan_world_collision` 的必要條件）。"""
    return {
        "event_id": "evt-cap-boundary-1",
        "event_type": "weather",
        "source": _SOURCE,
        "accepted": True,
        "timestamp": _iso(when),
        "novelty_id": "cap-boundary-1",
        "reason": "synthetic at-capacity fixture",
        "extra": {"summary": _SUMMARY},
    }


def _write_perceptions(records: List[Dict[str, Any]]) -> None:
    """寫進 M4／orchestrator 真實解析的感知軌跡檔（tmp 內）。"""
    path = lt_origins._perception_trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _patch_soul(monkeypatch) -> None:
    """人格上下文一律走 M4 的載入函式（不碰 persona 檔）。"""
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: _SOUL)


class _SpyLLM:
    """M4 的 stub LLM（**0 真實 LLM／0 網路**）：記錄呼叫並回固定字串。"""

    def __init__(self, response: Any = None):
        self.calls: List[Dict[str, Any]] = []
        self.response = response

    def __call__(self, messages, agent_id):
        self.calls.append({"messages": messages, "agent_id": agent_id})
        return self.response


def _run(agent_ids, now: datetime, slot: str, *, llm_caller=None):
    """跑一輪 orchestrator 管線（await）。"""
    return asyncio.run(m5.run_slot_pipeline(agent_ids, now, slot, llm_caller=llm_caller))


def _actions(*items: Dict[str, Any]) -> str:
    return json.dumps({"actions": list(items)}, ensure_ascii=False)


def _entries(agent_id: str = AGENT) -> List[Dict[str, Any]]:
    """M1 真檔的逐筆事件（唯讀）。"""
    return [
        json.loads(line)
        for line in lt.life_threads_path(agent_id).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _due_thread_ids(active_threads: List[Dict[str, Any]], now: datetime) -> List[str]:
    return [
        thread["thread_id"]
        for thread in active_threads
        if datetime.fromisoformat(thread["check_after_ts"]).timestamp() <= now.timestamp()
    ]


def _build_scenario(monkeypatch, *, full_cap: bool) -> Dict[str, Any]:
    """建好「到期線頭 ＋（選用）滿 cap」情境，並回傳所有前提資料。

    1. **D1 08:00 morning**：1 筆合格合成感知 ⇒ `WORLD_COLLISION_WAKE` ⇒ 生出
       線頭 A（`next_check_hours = 1` ⇒ D1 09:00 到期）。
    2. `full_cap=True` 時，**直接**經 M1 公開 API 再建一條**未到期**線頭 B
       （`active 1 < cap 2` ⇒ 合法）⇒ active 恰好等於 cap（**不放寬容量**）。
    3. **D1 22:00 night**：感知改標 `consumed_by_wake = True` ⇒ M3 碰撞不成立，
       到期線頭成為唯一訊號；並直接在閘門上取得本次修法要救的那個判定。
    """
    _write_perceptions([_perception_record(_MORNING_D1)])
    _patch_soul(monkeypatch)
    spy1 = _SpyLLM(_actions(_ACTION_CREATE_DUE))
    out1 = _run([AGENT], _MORNING_D1, "morning", llm_caller=spy1)

    summary1 = out1[AGENT]
    assert summary1["woke"] is True, summary1
    assert summary1["reason"] == lt_gate.REASON_WORLD_COLLISION_WAKE, summary1
    assert len(summary1["origin_round"]["created"]) == 1, summary1["origin_round"]

    states = lt.fold(AGENT)
    assert len(states) == 1, f"第 1 輪應恰生 1 條線頭，實得 {list(states)}"
    thread_a_id = next(iter(states))  # `fold()` 回 `{thread_id: state}`
    assert states[thread_a_id]["status"] == "active", states[thread_a_id]

    thread_b_id: Optional[str] = None
    if full_cap:
        thread_b_id = lt.create_thread(
            AGENT,
            title=_UNRELATED_THREAD_TITLE,
            narrative_content=_UNRELATED_THREAD_NARRATIVE,
            origin_type="goal_driven",
            check_after_ts=_iso(_NIGHT_D1 + timedelta(hours=48)),
        )
        assert thread_b_id is not None, "前提：未滿 cap 時建立第二條線頭必須成功"

    # ── 第 2 輪：感知改標 consumed（碰撞不成立、種子蒐集仍合格）──
    consumed = _perception_record(_NIGHT_D1 - timedelta(hours=1))
    consumed["consumed_by_wake"] = True
    _write_perceptions([consumed])
    assert lt_gate._scan_world_collision([consumed], _NIGHT_D1.timestamp()) is None, (
        "前提：`consumed_by_wake` ⇒ M3 世界碰撞必須不成立"
    )
    seeds = lt_origins.collect_world_seeds(AGENT, _NIGHT_D1, [consumed])
    assert [fact["summary"] for fact in seeds["facts"]] == [_SUMMARY], seeds

    active_threads = lt.list_active(AGENT)
    decision = lt_gate.evaluate_wake_gate(
        AGENT,
        _NIGHT_D1.timestamp(),
        "night",
        active_threads,
        lt.capacity(AGENT),
        recent_perceptions=[consumed],
        enforce_strict_capacity=True,
    )
    return {
        "spy1": spy1,
        "thread_a_id": thread_a_id,
        "thread_b_id": thread_b_id,
        "active_threads": active_threads,
        "consumed": consumed,
        "decision": decision,
    }


# ══════════════════════════════════════════════════════════════
# L0 — 隔離自證
# ══════════════════════════════════════════════════════════════


def test_00_isolation_data_root_is_tmp(iso_env):
    """🔴 紅線自證：M1 的實際資料根在 tmp 之下、**不等於** repo 的 `data/`。"""
    assert data_root() == iso_env.resolve(), (data_root(), iso_env)
    assert data_root() != (_REPO_ROOT / "data").resolve()


# ══════════════════════════════════════════════════════════════
# L1 — 滿 cap：訊號仍勝出，但新建仍被 M1 容量防線拒絕
# ══════════════════════════════════════════════════════════════


def test_at_capacity_due_thread_wakes_advances_and_create_is_rejected(iso_env, monkeypatch):
    """滿 cap（`active == cap`）＋到期線頭 ⇒ 喚醒成立、推進到期線頭，
    且**不得**繞過 `create_thread()` 的容量防線建新線頭（`created == []`）。"""
    scenario = _build_scenario(monkeypatch, full_cap=True)
    thread_a_id = scenario["thread_a_id"]
    thread_b_id = scenario["thread_b_id"]
    active_threads = scenario["active_threads"]
    now_ts = _NIGHT_D1.timestamp()

    # ── 前提自證：容量**未**被放寬，且真的站在邊界上 ──
    assert lt.capacity(AGENT) == CAP, "本測試不得放寬容量"
    assert CAP == lt.LIFE_THREAD_ACTIVE_CAP_DEFAULT, "前提：預設 cap 即本測試的邊界"
    assert len(active_threads) == CAP, [t["thread_id"] for t in active_threads]
    assert lt.active_count(AGENT) == CAP
    assert lt_gate._pool_saturated(active_threads, CAP) is True, (
        "前提：閘門看到的確實是『飽和』"
    )
    assert _due_thread_ids(active_threads, _NIGHT_D1) == [thread_a_id], (
        "前提：恰一條到期線頭（B 刻意未到期）"
    )

    # ── 本修法真正要救的判定：滿 cap **不得**否決訊號 ──
    decision = scenario["decision"]
    assert decision.should_wake is True, (
        "🔴 滿 cap 不得把『到期線頭』訊號短路成 SLEEP（Owner A：訊號先於容量）"
    )
    assert decision.reason == lt_gate.REASON_CHECKPOINT_DUE_WAKE, decision.reason
    assert decision.reason != lt_gate.REASON_ACTIVE_POOL_SATURATED, decision.reason
    assert decision.origin_type == "world_collision", decision.origin_type

    # ── 整條生產管線：WAKE → M4 推進 A；stub 同時要求建新線頭 ⇒ 必須被拒絕 ──
    m1_before = lt.life_threads_path(AGENT).read_bytes()
    spy2 = _SpyLLM(_actions(dict(_ACTION_ADVANCE_DUE, thread_id=thread_a_id),
                            _ACTION_CREATE_EXTRA))
    summary = _run([AGENT], _NIGHT_D1, "night", llm_caller=spy2)[AGENT]

    assert summary["woke"] is True, summary
    assert summary["reason"] == lt_gate.REASON_CHECKPOINT_DUE_WAKE, summary["reason"]
    assert summary["reason"] != lt_gate.REASON_ACTIVE_POOL_SATURATED, summary["reason"]
    assert summary["origin_round"]["prompt_available"] is True, summary["origin_round"]
    assert len(spy2.calls) == 1, spy2.calls
    assert summary["origin_round"]["advanced"] == [thread_a_id], summary["origin_round"]
    assert summary["origin_round"]["created"] == [], "🔴 滿 cap 不得建新線頭"
    assert "create:rejected" in summary["origin_round"]["skipped"], summary["origin_round"]

    # ── M1 真檔為準：容量防線仍是**唯一**強制點（線頭數／事件數都不變）──
    states = lt.fold(AGENT)
    assert set(states) == {thread_a_id, thread_b_id}, list(states)
    assert lt.active_count(AGENT) == CAP
    assert lt.life_threads_path(AGENT).read_bytes() != m1_before, "advance 必須落盤"

    entries = _entries()
    assert [e["event_type"] for e in entries].count("created") == CAP, entries
    assert [e["event_type"] for e in entries].count("updated") == 1, entries
    assert states[thread_a_id]["narrative_content"] == _ACTION_ADVANCE_DUE[
        "narrative_content"
    ], states[thread_a_id]


# ══════════════════════════════════════════════════════════════
# L2 — 對照組：未滿 cap 時允許建立（同一條喚醒路徑）
# ══════════════════════════════════════════════════════════════


def test_below_capacity_due_thread_wakes_and_create_is_allowed(iso_env, monkeypatch):
    """對照組：`active = cap - 1` ＋到期線頭 ⇒ 喚醒成立，且**允許**建立新線頭。

    與 L1 的唯一差別是「未滿 cap」⇒ `created` 非空、且 `create_thread()` 不被拒絕。
    """
    scenario = _build_scenario(monkeypatch, full_cap=False)
    thread_a_id = scenario["thread_a_id"]
    active_threads = scenario["active_threads"]

    assert lt.capacity(AGENT) == CAP, "本測試不得放寬容量"
    assert len(active_threads) == CAP - 1, [t["thread_id"] for t in active_threads]
    assert lt_gate._pool_saturated(active_threads, CAP) is False, (
        "前提：對照組**未**飽和"
    )
    assert _due_thread_ids(active_threads, _NIGHT_D1) == [thread_a_id]

    decision = scenario["decision"]
    assert decision.should_wake is True, decision
    assert decision.reason == lt_gate.REASON_CHECKPOINT_DUE_WAKE, decision.reason

    spy2 = _SpyLLM(_actions(dict(_ACTION_ADVANCE_DUE, thread_id=thread_a_id),
                            _ACTION_CREATE_EXTRA))
    summary = _run([AGENT], _NIGHT_D1, "night", llm_caller=spy2)[AGENT]

    assert summary["woke"] is True, summary
    assert summary["reason"] == lt_gate.REASON_CHECKPOINT_DUE_WAKE, summary["reason"]
    assert summary["origin_round"]["advanced"] == [thread_a_id], summary["origin_round"]
    created = summary["origin_round"]["created"]
    assert len(created) == 1, f"未滿 cap ⇒ 允許建立（對照組），實得 {created}"
    assert "create:rejected" not in summary["origin_round"]["skipped"], summary[
        "origin_round"
    ]

    states = lt.fold(AGENT)
    assert set(states) == {thread_a_id, created[0]}, list(states)
    assert lt.active_count(AGENT) == CAP, "1 條既有 ＋ 1 條新建 == cap（剛好滿）"
    assert [e["event_type"] for e in _entries()].count("created") == CAP
