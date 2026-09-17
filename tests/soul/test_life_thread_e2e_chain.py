# tests/soul/test_life_thread_e2e_chain.py
# LIFE-THREAD-E2E-SYNTH-1 — 合成感知貫通驗證：M1 → M2 → M3 → M4 → M2-EXEC。
#
# 目的（本票唯一的問題）
# ─────────────────────
# 生產現況是**冷啟動死結**：0 線頭（`data/**/life_threads*.jsonl` 檔數 = 0）⇒ 無到期
# 檢查點；近 4 小時**合格感知 0 筆** ⇒ 世界碰撞不成立 ⇒ M3 永不 WAKE ⇒ 永不生線頭。
# 本檔要在 **pytest tmp 隔離環境**注入**合法合成感知**，證明「一旦給予合格刺激，
# 全鏈路本身是通的」：能喚醒 → 生成線頭 → 推進 → 沉澱。
#
# 分層
# ────
#   L0 `test_00_isolation_data_root_is_tmp`   紅線自證：M1／M4 的實際資料根在 tmp 之下
#   L1 `test_10_*`  M3 世界碰撞 WAKE ⇒ M4 生線頭（M1 真檔長出 1 條），stub LLM 恰 1 次
#   L2 `test_20_*`  到期檢查點 WAKE（含「世界碰撞窗過期」對照），且不重複建線頭
#   L3 `test_30_*`  終態 ⇒ 旗標 ON ⇒ 執行層沉澱 ⇒ SAGE 真寫入 ⇒ `sage_fact_id` 回填 M1
#   L4 `test_40_*`  同情境旗標 OFF ⇒ 0 LLM／0 SAGE／0 任務殘留（可證明的 0 成本）
#
# 🔴 紅線自證
# ───────────
#   * **0 真實 LLM**：M4 一律注入 stub `llm_caller`；沉澱一律注入 fake proxy
#     （`_resolve_llm_proxy` seam）。本檔**不含任何真實 provider 端點**。
#   * **0 網路**：autouse fixture 把 `HTTP_PROXY`／`HTTPS_PROXY`／`http_proxy`／
#     `https_proxy` **全部**指向死代理 `http://127.0.0.1:1`（不可路由）⇒ 任何外呼
#     都會當場失敗而不是靜默成功；測試同時斷言死代理確實生效（見 `_dead_proxy`）。
#   * **0 生產 `data/**` 寫入**：autouse fixture 顯式把 `SOUL_OS_DATA_DIR` 指向
#     `tmp_path` 並 `reset_data_root()`；`test_00` 以**模組真實解析函式**斷言落點在
#      pytest tmp 之下且**不等於** repo 的 `data/`。
#   * **0 行程操作**：本檔不 spawn 任何外部程式、不殺任何行程、不綁任何埠、
#     不對任何執行中的服務發請求。
#   * **無 `skipif`**：本票不需要任何跳過條件（跳過會讓「鏈路是通的」變成未證明）。
#
# 慣例沿用（**不修改**它們）
# ────────────────────────
#   `tests/soul/test_life_thread_m5_wiring.py`：`iso_env` / `_SpyLLM` / `_widen_capacity`
#     / `_loc` / `reset_state()` 的用法。
#   `tests/soul/test_life_thread_consolidation_wiring.py`：旗標 env 切換、fake proxy 注入、
#     `add_fact` → `sage_fact_id` 回填的斷言手法。
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
from src.soul import life_thread_consolidation_wiring as lt_wiring  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_wake_gate as lt_gate  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

#: 沉澱**執行層**的物件（`ConsolidationResult` 型別、行程級預算／冪等 sentinel 的
#: 測試專用重置函式）一律**經由接線模組的既有 re-export** 取得（`lt_wiring.lt_exec`）。
#:
#: 🔴 為什麼不直接 `import ... as lt_exec`：`tests/soul/test_life_thread_dissolution_exec.py`
#: 的 T54 護欄把「全庫 import 執行層的檔案」鎖成**精確集合等值**
#: （1 生產檔 ＋ 該模組自己的 2 個測試檔）。本檔是**第三個**測試檔，若直接 import
#: 就會讓那道護欄變紅——而本票不得放寬護欄。經由接線模組取用**不新增任何 importer**，
#: 被測物仍是**真實執行層**（不是 mock）。

AGENT = "agent_e2e_synth"

#: 人格上下文（M4 `build_origin_prompt` 的必填項；空 ⇒ 0 LLM 花費 ⇒ 鏈路走不完）。
_SOUL = "我是測試代理人。我喜歡在清晨讀天氣預報，討厭悶熱的午後。"

#: 合成感知的**合格事實文字**（唯一合法來源 = `extra["summary"]`，§5.2.4）。
_SUMMARY_1 = "今天清晨城東下了今年的第一場雪，路面結了一層薄冰。"

#: M3 世界碰撞的三條件（`life_thread_wake_gate._scan_world_collision`）：
#: `accepted is True` ∧ `source ∈ QUALIFYING_WORLD_SOURCES` ∧ 非空 `extra["summary"]`。
_SOURCE = "weather"

#: stub LLM 回的**合法** action JSON（`origin_type` 刻意缺席 ⇒ 由 M4 以閘門判定的
#: `world_collision` 補上，這是「線頭來源 = 世界碰撞」的關鍵證據）。
#: `next_check_hours = 1`（clamp 下限）⇒ 檢查點在 D1 09:00 本地即到期，
#: 讓 L2 能在**同一天**內用另一個 slot 觀察到期喚醒。
_TITLE_1 = "雪後的路面"
_ACTION_CREATE = {
    "op": "create",
    "title": _TITLE_1,
    "narrative_content": "我看見薄冰，於是把散步的路線改走有日照的南側。",
    "next_check_hours": 1,
}

#: 沉澱 fake LLM 回的合法 JSON（與 `test_life_thread_consolidation_wiring` 同格式）。
_DISSOLUTION_NARRATIVE = "我把那場雪記成了「願意為自己改路線」的證據。"
_DISSOLUTION_JSON = json.dumps(
    {"dissolution": _DISSOLUTION_NARRATIVE, "meaning_kind": "competence"},
    ensure_ascii=False,
)

#: 死代理（不可路由）——「0 外呼」的客觀證明。RFC 5737 的 TEST-NET 也會被某些 client
#: 直接拒絕，故用 `127.0.0.1:1`（本機上不可能有人監聽的埠，且**非**生產服務埠）。
_DEAD_PROXY = "http://127.0.0.1:1"
_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


# ══════════════════════════════════════════════════════════════
# fixtures / helpers
# ══════════════════════════════════════════════════════════════


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """顯式隔離資料根（0 生產 `data/**` 接觸）。

    沿用 M5 既有範式的形狀，但**多一步**：本票要求「用模組真實解析函式」自證落點，
     故此處只負責把根指向 `tmp_path`，落點斷言交由 `test_00`。
    """
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    yield tmp_path
    reset_data_root()


@pytest.fixture(autouse=True)
def _dead_proxy(monkeypatch):
    """🔴 全程死代理：任何真實外呼都必須當場失敗，而不是靜默成功。

    同時斷言死代理**確實生效**（設不起來 ⇒ 本檔的「0 外呼」證明不成立 ⇒ 直接紅）。
    """
    for key in _PROXY_ENV_KEYS:
        monkeypatch.setenv(key, _DEAD_PROXY)
    import os

    live = {k: os.environ.get(k) for k in _PROXY_ENV_KEYS}
    assert set(live.values()) == {_DEAD_PROXY}, f"死代理未生效：{live}"
    yield live


@pytest.fixture(autouse=True)
def _reset_process_state():
    """清空**行程級**狀態（否則跨測試互相污染）＋ 斷言 0 背景任務殘留。

    - orchestrator 的 at-most-once 章（`m5._LAST_PROCESSED`）。
    - 執行層的冪等 sentinel／預設預算。
    - 接線模組的 per-agent writer 快取（否則會沿用上一個測試的 tmp `graph.sqlite`）。
    """
    m5.reset_state()
    lt_wiring.lt_exec._clear_consolidated_registry()
    lt_wiring.lt_exec._reset_default_budget()
    lt_wiring._reset_writers()
    try:
        yield
        assert lt_wiring.pending_task_count() == 0, (
            "測試結束仍有未清理的背景沉澱任務（會出現 'Task was destroyed but it is pending'）"
        )
    finally:
        lt_wiring._BACKGROUND_TASKS.clear()
        m5.reset_state()
        lt_wiring.lt_exec._clear_consolidated_registry()
        lt_wiring.lt_exec._reset_default_budget()
        lt_wiring._reset_writers()


def _loc(hour: int = 8, *, day: int = 6, day_delta: int = 0) -> datetime:
    """本地時區固定時刻（2026-09-06 08:00 → morning；`day_delta` 逐日平移）。

    ⚠️ `day_delta` 直接加在 `day` 上（兩者皆為「9 月的第幾天」），**不是** `timedelta`
    的別名——這與 `test_life_thread_m5_wiring._loc` 的既有形狀不同，避免 `hour` 被
    `timedelta` 洗掉時區後造成日期語意混淆。
    """
    return datetime(2026, 9, day + day_delta, hour, tzinfo=LOCAL_TZ)


#: L1 的評估點：D1 08:00 morning（`MODULE_SLOT_VALUES` 之一）。
_MORNING_D1 = _loc(8)
#: L2 第 2／3 輪：**同一天晚上 22:00 night**。刻意換 slot（而非換日期）：
#: orchestrator 的 at-most-once 章是 `(agent, slot, date)` ⇒ 換 slot 即可在同一「日期」
#: 內合法地跑第 2 輪，讓「世界碰撞窗過期」與「22:00 的評估點」對齊。
_NIGHT_D1 = _loc(22)


def _iso(moment: datetime) -> str:
    """UTC ISO-8601 字串（感知 trace 的時間欄位形狀）。"""
    return moment.astimezone(timezone.utc).isoformat()


def _perception_record(
    *, summary: str = _SUMMARY_1, when: Optional[datetime] = None
) -> Dict[str, Any]:
    """一筆**合格**的合成感知紀錄（對齊 `WorldPerceptionTrace` 的最小 schema）。

    三個必要條件逐條對齊 `life_thread_wake_gate._scan_world_collision`：
    `accepted is True` ∧ `source == "weather"` ∧ 非空 `extra["summary"]`；
    時間欄位對齊 `_parse_ts` 可解析的 ISO-8601。
    """
    moment = when if when is not None else datetime.now(timezone.utc)
    return {
        "event_id": "evt-synth-e2e-1",
        "event_type": "weather",
        "source": _SOURCE,
        "accepted": True,
        "timestamp": _iso(moment),
        "novelty_id": "synth-e2e-1",
        "reason": "synthetic e2e fixture",
        "extra": {"summary": summary},
    }


def _write_perceptions(records: List[Dict[str, Any]]) -> Path:
    """把合成感知寫進 **M4 真實解析**的感知軌跡檔（tmp 內；`_perception_trace_path()`）。"""
    path = lt_origins._perception_trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


def _patch_soul(monkeypatch, value: str = _SOUL) -> None:
    """人格上下文一律走 M4 的載入函式（不碰 persona 檔）。"""
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: value)


class _SpyLLM:
    """M4 的 stub LLM（**0 真實 LLM／0 網路**）：記錄每次呼叫並回固定字串。"""

    def __init__(self, response: Any = None):
        self.calls: List[Dict[str, Any]] = []
        self.response = response

    def __call__(self, messages, agent_id):
        self.calls.append({"messages": messages, "agent_id": agent_id})
        return self.response

    def snapshot(self) -> int:
        return len(self.calls)


class _RecordingProxy:
    """沉澱路徑的 fake LLM proxy（**0 真實 LLM**）：`generate_text` 只回記憶體內字串。"""

    def __init__(self, text: Optional[str] = _DISSOLUTION_JSON):
        self.calls: List[Dict[str, Any]] = []
        self.text = text

    async def generate_text(self, **kwargs) -> Optional[str]:
        self.calls.append(dict(kwargs))
        return self.text


def _run(agent_ids, now: datetime, slot: str, *, llm_caller=None):
    """跑一輪 orchestrator 管線（await）。"""
    return asyncio.run(m5.run_slot_pipeline(agent_ids, now, slot, llm_caller=llm_caller))


def _lt_states(agent_id: str = AGENT) -> Dict[str, Dict[str, Any]]:
    """M1 **真檔** fold 後的當前狀態（`thread_id` → state）。"""
    return lt.fold(agent_id)


def _only_thread(agent_id: str = AGENT) -> Dict[str, Any]:
    """M1 真檔裡唯一的線頭狀態（多於 1 條 ⇒ 視為測試前提被破壞）。"""
    states = _lt_states(agent_id)
    assert len(states) == 1, f"預期恰 1 條線頭，實得 {len(states)}：{list(states)}"
    return next(iter(states.values()))


def _pending_llm_proxy(monkeypatch, proxy: _RecordingProxy) -> None:
    """把沉澱 adapter 的 proxy seam 換成 fake（**唯一**會被執行的 LLM 通道）。"""
    monkeypatch.setattr(lt_wiring, "_resolve_llm_proxy", lambda: proxy)


def _synthesize_thread(monkeypatch, *, action: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """L1 的合成刺激：1 筆合格感知 + stub LLM 的 1 個 create action ⇒ M1 真檔長出線頭。

    回傳 `{"id": thread_id, "spy": stub, "out": 管線輸出}`。
    """
    _write_perceptions([_perception_record(when=_MORNING_D1)])
    _patch_soul(monkeypatch)
    spy = _SpyLLM(json.dumps({"actions": [action or _ACTION_CREATE]}, ensure_ascii=False))
    out = _run([AGENT], _MORNING_D1, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is True, out[AGENT]
    thread = _only_thread()
    return {"id": thread["thread_id"], "spy": spy, "out": out}


def _sage_fact_count(agent_id: str) -> int:
    """該 agent 的 tmp SAGE graph 目前有幾筆 fact（**唯讀**）。

    必須走**同一個 store 實例**（`lt_wiring._WRITERS` 的 per-agent 快取）才看得到剛寫入的
    資料：`GraphStore.add_fact` 是**批次提交**（`_pending_writes < batch_size` 時不 commit），
    另開一條連線會讀到未提交前的狀態。

    尚無 writer（例如旗標 OFF、或從未寫過）時退回唯讀 sqlite 查詢；DB 不存在 ⇒ 0
    （且**不建立** DB：走 `exists()` 守門）。
    """
    cached = lt_wiring._WRITERS.get(agent_id)
    if cached is not None:
        return len(cached.store.get_all_facts(include_invalidated=True))

    import sqlite3

    db = lt_origins._goal_db_path(agent_id)
    if not db.exists():
        return 0
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
    return int(row[0]) if row else 0


def _drive_dissolution_hook(
    agent_id: str, thread_id: str, status: str = "completed", *, probe=None
):
    """在 running loop 內觸發**真實接線 hook**，並把背景沉澱任務 await 完（0 殘留）。

    回傳 `{"tasks": [...], "results": [...], "probe_after_hook": ...}`；
    `probe_after_hook` 是「hook 剛返回、背景任務尚未被 await」時對 `probe()` 的取值
    （用來證明 hook 內**不得** inline await：LLM 必須由背景任務執行）。
    """
    out: Dict[str, Any] = {"tasks": [], "results": [], "probe_after_hook": None}
    hook = lt_wiring.build_dissolve_hook()

    async def _driver():
        hook(agent_id, thread_id, status)
        if probe is not None:
            out["probe_after_hook"] = probe()
        tasks = list(lt_wiring._BACKGROUND_TASKS)
        out["tasks"] = tasks
        if tasks:
            out["results"] = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # 讓 done-callback 跑完（清 set）

    asyncio.run(_driver())
    return out


# ══════════════════════════════════════════════════════════════
# L0 — 隔離自證（本票最重要的紅線證明）
# ══════════════════════════════════════════════════════════════


def test_00_isolation_data_root_is_tmp(iso_env, monkeypatch):
    """🔴 紅線：M1／M4 **實際解析**出的資料根必須在 pytest tmp 之下、且**不等於** repo `data/`。

    全部用**模組真實解析函式**（不硬編路徑）：
      * `src.paths.data_root()` —— 所有落地路徑的唯一來源。
      * `life_threads.life_threads_path()` —— **M1** 的線頭檔落點。
      * `life_thread_origins._perception_trace_path()` —— **M4** 的感知軌跡落點。
      * `life_thread_origins._goal_db_path()` —— **M2-EXEC** 的 SAGE 落點。

    這四條是「本檔不碰生產」的全部通道；任何一條跑到 repo `data/` 之下 ⇒ 本測試紅。
    """
    tmp_root = Path(iso_env).resolve()
    repo_data = (_REPO_ROOT / "data").resolve()

    resolved = data_root().resolve()
    assert str(resolved).startswith(str(tmp_root)), (
        f"資料根不在 pytest tmp 之下：{resolved}（tmp={tmp_root}）"
    )
    assert resolved != repo_data, f"🔴 資料根解析到 repo 生產 data/：{resolved}"

    m1_path = lt.life_threads_path(AGENT).resolve()
    m4_perception = lt_origins._perception_trace_path().resolve()
    sage_db = lt_origins._goal_db_path(AGENT).resolve()

    for label, path in (
        ("M1 life_threads.jsonl", m1_path),
        ("M4 perception_trace.jsonl", m4_perception),
        ("M2-EXEC SAGE graph.sqlite", sage_db),
    ):
        assert str(path).startswith(str(tmp_root)), (
            f"{label} 不在 pytest tmp 之下：{path}（tmp={tmp_root}）"
        )
        assert repo_data not in path.parents, f"🔴 {label} 落在 repo 生產 data/：{path}"

    # M1 檔名形狀（真解析函式，不硬編 `data/...`）
    assert m1_path.parent == resolved / "soul" / AGENT, m1_path
    assert m1_path.name == lt.LIFE_THREADS_FILENAME, m1_path.name
    # M4 感知軌跡（**不得**硬編 `data/world/...`）
    assert m4_perception == resolved / "world" / "perception_trace.jsonl", m4_perception
    assert m4_perception.parent.parent == resolved, m4_perception

    # 死代理自證：任何外呼都會打到不可路由位址，而不是靜默成功
    import os

    assert {k: os.environ[k] for k in _PROXY_ENV_KEYS} == {
        k: _DEAD_PROXY for k in _PROXY_ENV_KEYS
    }

    # 起點：M1 檔**不存在**（冷啟動死結的形狀）；感知檔亦不存在
    assert not m1_path.exists(), "隔離環境起點就已有 M1 檔 ⇒ 前一個測試洩漏"
    assert not m4_perception.exists()


# ══════════════════════════════════════════════════════════════
# L1 — 世界碰撞喚醒 ⇒ 生成線頭
# ══════════════════════════════════════════════════════════════


def test_10_world_collision_wakes_and_creates_thread_once(iso_env, monkeypatch):
    """合成感知 → M3 判 `WORLD_COLLISION_WAKE` → M4（stub LLM 恰 1 次）→ M1 真的多 1 條線頭。

    這是「合格刺激 ⇒ 全鏈路能醒」的第一段證明：**0 線頭起步**（`test_00` 已證起點不存在）
    ⇒ 注入 1 筆合格感知 ⇒ 管線自己把線頭生出來。
    """
    perception_path = lt_origins._perception_trace_path().resolve()
    assert not perception_path.exists(), "起點不得有感知檔"

    out = _write_perceptions([_perception_record(when=_MORNING_D1)])
    assert out == perception_path, f"感知寫到非解析路徑：{out} vs {perception_path}"

    _patch_soul(monkeypatch)
    spy = _SpyLLM(json.dumps({"actions": [_ACTION_CREATE]}, ensure_ascii=False))
    before = _lt_states()
    result = _run([AGENT], _MORNING_D1, "morning", llm_caller=spy)
    summary = result[AGENT]

    # ── M3：醒了，且理由逐字是「世界碰撞」──
    assert summary["woke"] is True, summary
    assert summary["reason"] == lt_gate.REASON_WORLD_COLLISION_WAKE, summary["reason"]
    assert summary["reason"] == "WORLD_COLLISION_WAKE", summary["reason"]
    assert summary["origin_type"] == "world_collision", summary["origin_type"]
    assert "wake_blocked" not in summary, f"人格上下文非空 ⇒ 不得提前跳過：{summary}"

    # ── M4：真的有跑、且**恰 1 次** LLM 呼叫 ──
    round_result = summary["origin_round"]
    assert round_result["called"] is True, round_result
    assert round_result["prompt_available"] is True, round_result
    assert round_result["llm_calls"] == 1, round_result
    assert len(spy.calls) == 1, f"stub LLM 必須恰被呼叫 1 次，實得 {len(spy.calls)}"

    # prompt 逐字帶入合成事實（證明刺激真的流進 M4，而不是被丟掉）
    user_msg = spy.calls[0]["messages"][1]["content"]
    assert _SUMMARY_1 in user_msg, user_msg[:400]

    # ── M1：真檔真的多出 1 條線頭，且欄位值正確 ──
    after = _lt_states()
    assert len(before) == 0, before
    assert len(after) == 1, f"🔴 M1 必須多出恰 1 條線頭：{list(after)}"
    assert round_result["created"] == [next(iter(after))], round_result

    thread = next(iter(after.values()))
    assert thread["status"] == "active", thread
    assert thread["origin_type"] == "world_collision", thread
    assert thread["title"] == _TITLE_1, thread
    assert thread["narrative_content"] == _ACTION_CREATE["narrative_content"], thread
    assert thread["dissolved_at"] is None, thread
    assert thread["sage_fact_id"] is None, thread

    # `next_check_hours=1` ⇒ `check_after_ts` = 評估時刻 + 1h（**真值**，不是 None）
    check_after = datetime.fromisoformat(thread["check_after_ts"])
    expected = _MORNING_D1 + timedelta(hours=1)
    assert abs((check_after - expected).total_seconds()) < 1.0, (
        f"check_after_ts 應為 {expected.isoformat()}，實得 {thread['check_after_ts']}"
    )

    # ── 真檔逐行（`created` 事件的實際欄位）──
    raw = lt.life_threads_path(AGENT).read_text(encoding="utf-8").splitlines()
    assert len(raw) == 1, raw
    entry = json.loads(raw[0])
    assert entry["event_type"] == "created" and entry["event_seq"] == 1, entry
    assert entry["thread_id"] == thread["thread_id"], entry


# ══════════════════════════════════════════════════════════════
# L2 — 到期檢查點喚醒（並證明不會重複建線頭）
# ══════════════════════════════════════════════════════════════


def test_20_checkpoint_due_wakes_the_same_thread_and_creates_no_duplicate(
    iso_env, monkeypatch
):
    """L1 生出的線頭到期 ⇒ 下一輪理由 = `CHECKPOINT_DUE_WAKE`，且**不重複建立**同一條。

    兩輪，全部走真實 orchestrator 管線（**0 monkeypatch 閘門常數**）。at-most-once 章的
    鍵是 `(agent, slot, date)`，故「同一天內跑兩輪」必須換 slot——本測試用
    `morning` → `night`：

      1. **D1 08:00 morning**：1 筆合格合成感知 ⇒ `WORLD_COLLISION_WAKE` ⇒ 建線頭。
         stub 回 `next_check_hours = 1` ⇒ `check_after_ts` = D1 09:00 本地（**當天**即到期）。
      2. **D1 22:00 night**（換 slot）：**同一筆**感知改標 `consumed_by_wake = true`
         ⇒ M3 步驟 2 跳過它（`_scan_world_collision` 的既有旗標）⇒ 碰撞不再成立；
         線頭檢查點（D1 09:00）早已到期 ⇒ 只剩步驟 3a
         ⇒ 理由 = `CHECKPOINT_DUE_WAKE`，推進**同一條**線頭（`advanced == [同一 id]`）。

    為什麼要在第 2 輪改標而不是刪掉感知：M4 的 `world_collision` prompt **要求**
    `collect_world_seeds` 至少有一筆合格事實（`extra["summary"]` 非空），否則
    `prompt_available=False`、0 LLM 呼叫。`consumed_by_wake` 只影響 M3 的碰撞判定、
    **不影響** M4 的種子蒐集 ⇒ 兩個條件可同時滿足，測試因此是決定性的。
    線頭總數全程為 1 ⇒ 「世界碰撞/到期喚醒都不得把既有線頭複製成新線頭」。
    """
    # ── 第 1 輪（D1 08:00 morning）：合成刺激 ⇒ 生線頭 ──
    first = _synthesize_thread(monkeypatch)
    thread_id = first["id"]
    assert first["out"][AGENT]["reason"] == lt_gate.REASON_WORLD_COLLISION_WAKE
    thread = _only_thread()
    assert thread["status"] == "active", thread
    check_after_utc = datetime.fromisoformat(thread["check_after_ts"])
    assert check_after_utc.timestamp() == (_MORNING_D1 + timedelta(hours=1)).timestamp(), thread
    assert check_after_utc.timestamp() <= _NIGHT_D1.timestamp(), (
        "前提：第 2 輪時線頭已到期"
    )

    # ── 第 2 輪（D1 22:00 night）：感知改標 consumed ⇒ 碰撞不成立 ⇒ 到期檢查點勝出 ──
    # 時間戳取「評估點前 1h」⇒ 在 M4 種子蒐集的 4h 窗內（碰撞側已被 consumed 擋掉）。
    consumed = _perception_record(when=_NIGHT_D1 - timedelta(hours=1))
    consumed["consumed_by_wake"] = True
    _write_perceptions([consumed])

    # 自證前提：同一份紀錄在「碰撞判定」下不合格、但在「種子蒐集」下仍合格
    assert lt_gate._scan_world_collision([consumed], _NIGHT_D1.timestamp()) is None, (
        "consumed_by_wake ⇒ M3 世界碰撞必須不成立"
    )
    seeds = lt_origins.collect_world_seeds(AGENT, _NIGHT_D1, [consumed])
    assert [f["summary"] for f in seeds["facts"]] == [_SUMMARY_1], seeds

    m1_before = lt.life_threads_path(AGENT).read_bytes()
    spy2 = _SpyLLM(
        json.dumps(
            {
                "actions": [
                    {
                        "op": "advance",
                        "thread_id": thread_id,
                        "narrative_content": "我把窗上的霜擦掉，決定今天不出遠門。",
                        "next_check_hours": 6,
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    out2 = _run([AGENT], _NIGHT_D1, "night", llm_caller=spy2)
    summary2 = out2[AGENT]

    assert summary2["woke"] is True, summary2
    assert summary2["reason"] == lt_gate.REASON_CHECKPOINT_DUE_WAKE, summary2["reason"]
    assert summary2["reason"] == "CHECKPOINT_DUE_WAKE", summary2["reason"]
    assert summary2["reason"] != lt_gate.REASON_WORLD_COLLISION_WAKE, summary2["reason"]
    assert summary2["origin_type"] == "world_collision", summary2["origin_type"]
    assert summary2["origin_round"]["prompt_available"] is True, summary2["origin_round"]
    assert summary2["origin_round"]["advanced"] == [thread_id], summary2["origin_round"]
    assert summary2["origin_round"]["created"] == [], "🔴 到期喚醒不得建新線頭"
    assert len(spy2.calls) == 1, spy2.calls
    assert lt.life_threads_path(AGENT).read_bytes() != m1_before, "advance 必須落盤"

    # ── 線頭仍是同一條 1 條；`updated` 事件推進它、`check_after_ts` 改期 ──
    states = _lt_states()
    assert list(states) == [thread_id], f"🔴 不得重複建立線頭：{list(states)}"
    assert states[thread_id]["status"] == "active", states[thread_id]
    assert states[thread_id]["origin_type"] == "world_collision", states[thread_id]
    assert states[thread_id]["narrative_content"] == (
        "我把窗上的霜擦掉，決定今天不出遠門。"
    ), states[thread_id]

    entries = [
        json.loads(line)
        for line in lt.life_threads_path(AGENT).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [e["event_type"] for e in entries] == ["created", "updated"], entries
    assert entries[1]["thread_id"] == thread_id, entries[1]
    assert entries[1]["event_seq"] == 2, entries[1]
    assert datetime.fromisoformat(entries[1]["check_after_ts"]) == (
        _NIGHT_D1 + timedelta(hours=6)
    ), entries[1]["check_after_ts"]


# ══════════════════════════════════════════════════════════════
# L3 — 沉澱貫通（旗標 ON）：M1 → M2 → M2-EXEC → SAGE → 回填 M1
# ══════════════════════════════════════════════════════════════


def test_30_consolidation_on_writes_sage_fact_and_backfills_m1(iso_env, monkeypatch):
    """終態線頭 ＋ 旗標 ON ＋ fake LLM ⇒ 沉澱被觸發、SAGE 真的多 1 筆 fact、
    `sage_fact_id` 真的回填進 M1 真檔；全程 0 真實網路。

    線頭**由 L1 的合成鏈路生出**（不是憑空寫檔）：證明「合成刺激產生的線頭，真的能被
    沉澱層接收」。
    """
    synth = _synthesize_thread(monkeypatch)
    thread_id = synth["id"]
    # M4 這一側（合成 → 生線頭）恰 1 次 stub LLM；沉澱是**另一條**通道（見下）
    assert len(synth["spy"].calls) == 1, synth["spy"].calls

    # 推進到終態（`completed`）：唯一合法的 M1 狀態轉移寫入 API。
    assert lt.append_transition(AGENT, thread_id, "completed") is True
    terminal = _only_thread()
    assert terminal["status"] == "completed", terminal
    assert terminal["dissolved_at"] is None and terminal["sage_fact_id"] is None, terminal

    # ── 旗標 ON ＋ fake LLM proxy（唯一 LLM 通道）──
    monkeypatch.setenv(lt_wiring.CONSOLIDATION_ENABLED_ENV, "1")
    assert lt_wiring.consolidation_enabled() is True
    proxy = _RecordingProxy(_DISSOLUTION_JSON)
    _pending_llm_proxy(monkeypatch, proxy)

    facts_before = _sage_fact_count(AGENT)
    assert len(proxy.calls) == 0, "起點不得已有沉澱呼叫"

    out = _drive_dissolution_hook(AGENT, thread_id, probe=lambda: len(proxy.calls))
    # 🔴 hook 內**不得** inline await：hook 剛返回時 LLM 尚未被呼叫
    assert out["probe_after_hook"] == 0, "hook 內不得 inline await LLM（必須丟背景任務）"
    assert len(out["tasks"]) == 1, out["tasks"]

    result = out["results"][0]
    assert not isinstance(result, BaseException), f"沉澱任務失敗：{result!r}"
    assert isinstance(result, lt_wiring.lt_exec.ConsolidationResult), result

    # ① 沉澱被觸發、恰 1 次 fake LLM 呼叫（且參數逐項對齊 M2-WIRING 契約）
    assert result.status == lt_wiring.lt_exec.STATUS_CONSOLIDATED, result.reason
    assert result.status == "consolidated", result.status
    assert result.llm_calls == 1, result
    assert len(proxy.calls) == 1, f"fake LLM 必須恰 1 次，實得 {len(proxy.calls)}"
    call = proxy.calls[0]
    assert call["reasoning_effort"] == lt_wiring.CONSOLIDATION_REASONING_EFFORT
    assert call["max_retries"] == 0
    assert call["agent_id"] == AGENT, call

    # ② SAGE 端真的多出 1 筆 fact（寫在 tmp store）
    facts_after = _sage_fact_count(AGENT)
    assert facts_after == facts_before + 1, (
        f"SAGE fact 必須恰增 1 筆：{facts_before} → {facts_after}"
    )

    # ③ `sage_fact_id` 真的回填到 M1（讀 M1 真檔），且與 SAGE 的 fact_id 一致
    after = _only_thread()
    assert after["dissolved_at"], "M1 必須回填 dissolved_at"
    fact_id = after["sage_fact_id"]
    assert isinstance(fact_id, str) and fact_id.strip(), f"🔴 sage_fact_id 未回填：{after}"

    writer = lt_wiring._WRITERS[AGENT]
    stored = writer.store.get_fact(fact_id)
    assert stored is not None, f"SAGE 查無回填的 fact_id：{fact_id}"
    assert stored.predicate == lt_wiring.lt_exec.FACT_PREDICATE, stored
    assert stored.predicate == "life_thread_dissolved", stored.predicate
    assert stored.object == _DISSOLUTION_NARRATIVE, stored
    assert stored.subject == AGENT, stored

    # ④ 全程 0 真實網路（死代理）；且 M4 的 stub 未被沉澱路徑二次呼叫（兩條通道分離）
    import os

    assert len(synth["spy"].calls) == 1, synth["spy"].calls
    assert {k: os.environ[k] for k in _PROXY_ENV_KEYS} == {
        k: _DEAD_PROXY for k in _PROXY_ENV_KEYS
    }

    # 真檔逐行：`created` → `transitioned` → `dissolved`
    entries = [
        json.loads(line)
        for line in lt.life_threads_path(AGENT).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [e["event_type"] for e in entries] == [
        "created",
        "transitioned",
        "dissolved",
    ], entries
    assert entries[-1]["sage_fact_id"] == fact_id, entries[-1]
    assert entries[-1]["dissolved_at"], entries[-1]


def test_31_wiring_module_passes_its_own_hook_to_m4(iso_env, monkeypatch):
    """M4 的 `dissolve_hook` **逐字**是接線模組的 hook（鏈尾確實接上 L3 那條路）。

    沒有這條，`test_30` 只證明「接線模組自己跑得通」，不證明「M4 會用它」。
    """
    _write_perceptions([_perception_record(when=_MORNING_D1)])
    _patch_soul(monkeypatch)
    captured: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        captured.append({"args": a, "kwargs": k})
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    _run(
        [AGENT],
        _MORNING_D1,
        "morning",
        llm_caller=_SpyLLM(json.dumps({"actions": []}, ensure_ascii=False)),
    )

    assert len(captured) == 1, captured
    hook = captured[0]["kwargs"]["dissolve_hook"]
    assert hook is not None, "沉澱層已接線 ⇒ dissolve_hook 不得是 None"
    assert hook.__module__ == "src.soul.life_thread_consolidation_wiring", hook.__module__


# ══════════════════════════════════════════════════════════════
# L4 — 旗標 OFF ⇒ 0 成本（同 L3 情境）
# ══════════════════════════════════════════════════════════════


def test_40_consolidation_off_is_provably_zero_cost(iso_env, monkeypatch):
    """同一條終態線頭，旗標 **OFF** ⇒ 0 LLM 呼叫、0 SAGE 寫入、0 任務殘留。

    與 `test_30` 成對：兩者唯一差異是旗標值，故「成本完全由旗標決定」是可證明的。
    """
    thread_id = _synthesize_thread(monkeypatch)["id"]
    assert lt.append_transition(AGENT, thread_id, "completed") is True

    monkeypatch.setenv(lt_wiring.CONSOLIDATION_ENABLED_ENV, "0")
    assert lt_wiring.consolidation_enabled() is False

    proxy = _RecordingProxy(_DISSOLUTION_JSON)
    _pending_llm_proxy(monkeypatch, proxy)

    facts_before = _sage_fact_count(AGENT)
    writers_before = dict(lt_wiring._WRITERS)

    out = _drive_dissolution_hook(AGENT, thread_id, probe=lambda: len(proxy.calls))

    assert out["tasks"] == [], "旗標 OFF ⇒ 不得建立背景任務"
    assert out["probe_after_hook"] == 0
    assert out["results"] == []
    assert proxy.calls == [], "旗標 OFF ⇒ 0 LLM 呼叫"
    assert _sage_fact_count(AGENT) == facts_before, "旗標 OFF ⇒ 0 SAGE 寫入"
    assert lt_wiring._WRITERS == writers_before, "旗標 OFF ⇒ 連 writer 都不得被建"
    assert lt_wiring.pending_task_count() == 0, "旗標 OFF ⇒ 0 任務殘留"

    # M1 逐位元不變（0 回填、0 讀寫副作用）
    after = _only_thread()
    assert after["dissolved_at"] is None, after
    assert after["sage_fact_id"] is None, after
    assert after["status"] == "completed", after


def test_41_flag_absent_is_also_zero_cost(iso_env, monkeypatch):
    """旗標**缺席**（生產預設）與 `"0"` 等效 ⇒ 同樣 0 成本（生產行為與接線前一致）。"""
    thread_id = _synthesize_thread(monkeypatch)["id"]
    assert lt.append_transition(AGENT, thread_id, "completed") is True

    monkeypatch.delenv(lt_wiring.CONSOLIDATION_ENABLED_ENV, raising=False)
    assert lt_wiring.consolidation_enabled() is False

    proxy = _RecordingProxy(_DISSOLUTION_JSON)
    _pending_llm_proxy(monkeypatch, proxy)
    facts_before = _sage_fact_count(AGENT)

    out = _drive_dissolution_hook(AGENT, thread_id)

    assert out["tasks"] == []
    assert proxy.calls == []
    assert _sage_fact_count(AGENT) == facts_before
    assert lt_wiring.pending_task_count() == 0
