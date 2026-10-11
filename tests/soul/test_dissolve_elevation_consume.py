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
#   * **不 import 執行層**（`life_thread_dissolution_exec` 的 T54 importer 護欄指名
#     恰 3 個 importer；本檔以字面狀態碼＋duck-typing 斷言，哨兵／預算則以
#     「每測試獨立 agent＋獨立 thread_id」天然隔離，0 行程級重置需求）。
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

#: 執行層成功狀態碼的字面值（來源 `life_thread_dissolution_exec.STATUS_CONSOLIDATED`；
#: 本檔不 import 執行層，見檔頭 T54 說明；漂移時此斷言會紅，即為哨兵）。
_STATUS_CONSOLIDATED = "consolidated"

NARRATIVE = "我把簡報做完了，這讓我覺得自己能扛事。"
LLM_JSON = json.dumps({"dissolution": NARRATIVE, "meaning_kind": "competence"})
FACT_ID = "fact-dissolve-consume-001"


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """丟棄 writer 快取＋斷言 0 背景任務殘留（本檔不碰執行層行程級狀態）。"""
    w._reset_writers()
    yield
    assert w.pending_task_count() == 0, "測試結束仍有未清理的背景任務"
    w._BACKGROUND_TASKS.clear()
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


def _drive(agent_id: str, thread_id: str, status: str = "completed") -> Dict[str, Any]:
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


def _assert_consolidated(out: Dict[str, Any]) -> Any:
    """duck-typing 斷言：任務成功且狀態為 consolidated（不 import 執行層）。"""
    assert len(out["results"]) == 1, out["results"]
    result = out["results"][0]
    assert not isinstance(result, BaseException), result
    assert getattr(result, "status", None) == _STATUS_CONSOLIDATED, result
    return result


def test_dissolve_success_triggers_consume_with_fact_source_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """dissolve 成功 ⇒ 昇華 consume 被觸發，且邊的 `source_id == fact_id`。"""
    _arm_success_path(monkeypatch)
    monkeypatch.setattr(
        w, "_read_thread_state", lambda a, t: _terminal_thread("th-consume-1")
    )

    _assert_consolidated(_drive("agent_consume_1", "th-consume-1"))

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

    _assert_consolidated(_drive("agent_consume_2", "th-consume-dup-1"))
    first_bytes = _edges_path().read_bytes()

    # 不同線頭、同一 fact_id ⇒ 執行層會再調 fact_writer，但昇華側必須跳過。
    _assert_consolidated(_drive("agent_consume_2", "th-consume-dup-2"))
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
        _assert_consolidated(
            _drive("agent_consume_3", "th-consume-boom")  # 不得拋
        )

    assert any("elevation consume" in r.message for r in caplog.records), caplog.text
