# tests/soul/test_dissolve_elevation_consume.py
# ELEVATION-CONSUME-1 — dissolve 事實直通昇華鏈（fire-and-forget consume）測試。
#
# 受測接縫：`src/soul/life_thread_consolidation_wiring.py`
#   `fact_writer` 成功分支 → `_consume_dissolved_fact`（不經 Gate.submit）。
#
# 本檔紅線自證：
#   * **0 真實 LLM、0 網路**：LLM 走假 proxy（`_RecordingProxy`）；昇華側用
#     `StubElevationLLM`（確定性樁，生產同款預設）。
#   * **0 生產 `data/**` 寫入**：`tests/conftest.py` 的 autouse 資料根隔離把
#     `SOUL_OS_DATA_DIR` 指向 pytest tmp；昇華 store（`elevation/`）與 SAGE 皆落 tmp。
#   * 不啟停任何服務、不綁任何埠、不跑 `scripts/manual_*.py`、只用 `.venv`。
#   * 背景任務一律在測試結束前被 `await`（0 殘留斷言）。
#   * 不碰 Frozen（Agency／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入主幹）：
#     只讀 fact dict、不改寫路徑。
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.soul.life_thread_consolidation_wiring as w  # noqa: E402
import src.soul.life_thread_dissolution_exec as ex  # noqa: E402

AGENT = "agent_dissolve_consume"
NARRATIVE = "我把簡報做完了，這讓我覺得自己能扛事。"
LLM_JSON = json.dumps({"dissolution": NARRATIVE, "meaning_kind": "competence"})
FACT_ID = "fact-dissolve-consume-001"


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """清空行程級狀態＋斷言 0 背景任務殘留（對齊既有 wiring 測試矩陣）。"""
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()
    yield
    assert w.pending_task_count() == 0, "測試結束仍有未清理的背景任務"
    w._BACKGROUND_TASKS.clear()
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()


def _terminal_thread(thread_id: str) -> Dict[str, Any]:
    """M1 fold 後形狀的終態線頭（M2 步驟 2 的觸發條件）。"""
    return {
        "thread_id": thread_id,
        "status": "completed",
        "title": "完成一份簡報",
        "narrative_content": "她把簡報做完了。",
        "origin_type": "goal_driven",
        "created_at": "2026-09-10T12:00:00+00:00",
        "updated_at": "2026-09-17T12:00:00+00:00",
        "check_after_ts": None,
        "share_target": "none",
        "dissolved_at": None,
        "sage_fact_id": None,
    }


class _RecordingProxy:
    """假 LLMProxy（0 真實 LLM）：只回固定合法 JSON。"""

    def __init__(self, text: Optional[str] = LLM_JSON):
        self.calls: List[Dict[str, Any]] = []
        self.text = text

    async def generate_text(self, **kwargs) -> Optional[str]:
        self.calls.append(dict(kwargs))
        return self.text


def _drive(
    agent_id: str = AGENT, thread_id: str = "th-consume-1", status: str = "completed"
) -> Dict[str, Any]:
    """在 running loop 內呼叫同步 hook，並把背景任務 `await` 完（0 殘留）。"""
    out: Dict[str, Any] = {"tasks": [], "results": []}

    async def _driver():
        hook = w.build_dissolve_hook()
        hook(agent_id, thread_id, status)
        tasks = list(w._BACKGROUND_TASKS)
        out["tasks"] = tasks
        if tasks:
            out["results"] = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # 讓 done-callback 跑完（清 set）

    asyncio.run(_driver())
    return out


def _arm_success_path(monkeypatch: pytest.MonkeyPatch, fact_id: str = FACT_ID):
    """把 dissolve 成功路徑的 seams 換成 fake（唯獨昇華 consume 走真實）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)
    monkeypatch.setattr(w, "_write_fact", lambda fact, agent_id: fact_id)
    monkeypatch.setattr(w, "_append_dissolved", lambda a, t, fid: True)
    return proxy


def _edges_path() -> Path:
    from src.paths import data_root

    return data_root() / "elevation" / "elevation_edges.jsonl"


def _read_edges() -> List[Dict[str, Any]]:
    path = _edges_path()
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_dissolve_success_triggers_consume_with_fact_source_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """dissolve 成功 ⇒ 昇華 consume 被觸發，且邊的 `source_id == fact_id`。"""
    _arm_success_path(monkeypatch)
    monkeypatch.setattr(
        w, "_read_thread_state", lambda a, t: _terminal_thread("th-consume-1")
    )

    out = _drive(thread_id="th-consume-1")
    assert len(out["results"]) == 1
    result = out["results"][0]
    assert isinstance(result, ex.ConsolidationResult), result
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason

    edges = _read_edges()
    sage_edges = [e for e in edges if e.get("source_type") == "sage_fact"]
    assert sage_edges, "dissolve 成功後 elevation_edges 必須有 sage_fact 邊"
    assert any(e.get("source_id") == FACT_ID for e in sage_edges), sage_edges


def test_repeat_dissolve_same_fact_skips_second_consume(
    monkeypatch: pytest.MonkeyPatch,
):
    """重複 dissolve 同一 fact ⇒ 第二次 consume 跳過（edges 檔逐位元不變）。"""
    _arm_success_path(monkeypatch)
    monkeypatch.setattr(
        w,
        "_read_thread_state",
        lambda a, t: _terminal_thread(t),  # 每次用呼叫端的 thread_id 建終態線頭
    )

    out1 = _drive(thread_id="th-consume-dup-1")
    assert out1["results"][0].status == ex.STATUS_CONSOLIDATED
    first_bytes = _edges_path().read_bytes()

    out2 = _drive(thread_id="th-consume-dup-2")  # 不同線頭、同一 fact_id
    assert out2["results"][0].status == ex.STATUS_CONSOLIDATED
    assert _edges_path().read_bytes() == first_bytes, "第二次必須跳過 consume（0 追加）"


def test_consume_exception_is_fail_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """consume 異常 ⇒ 不拋（fail-silent 只 warning），dissolve 結果不受影響。"""

    def _boom(agent_id: str, fact: Dict[str, Any], fact_id: str) -> None:
        raise RuntimeError("elevation exploded")

    _arm_success_path(monkeypatch)
    monkeypatch.setattr(w, "_consume_dissolved_fact", _boom)
    monkeypatch.setattr(
        w, "_read_thread_state", lambda a, t: _terminal_thread("th-consume-boom")
    )

    with caplog.at_level(logging.WARNING, logger=w.logger.name):
        out = _drive(thread_id="th-consume-boom")  # 不得拋

    result = out["results"][0]
    assert isinstance(result, ex.ConsolidationResult), result
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason
    assert any("elevation consume" in r.message for r in caplog.records), caplog.text
