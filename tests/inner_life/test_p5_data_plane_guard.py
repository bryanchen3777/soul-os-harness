# tests/inner_life/test_p5_data_plane_guard.py
"""CAMPAIGN-B-P5-② §3 P7-T1～P7-T12 — 張力驅動思考 資料面接線守衛。

工單：logs/CAMPAIGN-B-P5-2-DATA-PLANE-WORK-ORDER.md

覆蓋（12 條守衛）:
  - P7-T1  **旗標缺省 OFF** ⇒ 排程器 ``_fire_tension_drive_loop()`` 完全不動作
            （0 LLM 呼叫、0 寫入、0 副作用）。
  - P7-T2  **旗標 ON + night slot + 未重複** ⇒ 觸發**恰好 1 個 agent**、
            **恰好 1 次** thinker 呼叫。
  - P7-T3  **at-most-once**：同一 ``agent:date`` 再觸發 ⇒ **不再呼叫**。
  - P7-T4  **每日上限**：多個 agent 候選時，單輪**最多 1 個** agent 被處理。
  - P7-T5  **寫入面**：只寫 ``working_state.jsonl``；``epistemic_tensions.jsonl``／
            ``trace.jsonl``／其他生產檔**位元不變、且無新檔**。
  - P7-T6  **路徑不可注入**：``drive_loop_async_production()`` 與
            ``format_working_state_lines_production()`` 的簽章都**不含**路徑參數。
  - P7-T7  **沙盒守門未放寬**：``drive_loop_async(agent, now, <生產路徑>)``
            仍回空／不寫（``check_store_dir`` 未被放寬）。
  - P7-T8  **預算硬編碼**：``max_tokens=400``／``max_calls=1``／``max_retries=0``
            是**字面值**，不可由環境變數改變（AST 斷言）。
  - P7-T9  **不繞過 proxy**：接線 helper **不得** import ``requests``／``httpx``／
            ``urllib``、不得自建 HTTP 客戶端（AST 斷言）。
  - P7-T10 **0 新計時器／0 新 task／0 新 sleep**：``_fire_tension_drive_loop()``
            內無 ``sleep``／``while``／``create_task``（AST 斷言）。
  - P7-T11 **fail-silent**：thinker 拋例外 ⇒ 排程器**不 raise**、主循環不受影響
            （只 log warning）。
  - P7-T12 **0 真實 LLM**：本檔**不得**出現真實 provider 呼叫（thinker 一律是
            本檔自帶的 Mock）。

🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
🔴 本檔**零真實 provider 呼叫**（P7-T12 以 AST ＋ 執行期計數器雙重把關）。
🔴 本檔**不啟動伺服器**、**不綁任何埠**、**不對生產發真實請求**。
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.inner_life import tension_closure_loop as tcl  # noqa: E402
from src.inner_life import tension_thinking as tt  # noqa: E402
from src.paths import data_root  # noqa: E402
from src.soul.scheduler import SoulScheduler  # noqa: E402

#: 本檔自身（守衛檔）的路徑（AST 斷言用）。
GUARD_FILE = Path(__file__).resolve()

#: 被測的三個模組（AST 斷言用）。
CLOSURE_PY = REPO_ROOT / "src" / "inner_life" / "tension_closure_loop.py"
THINKING_PY = REPO_ROOT / "src" / "inner_life" / "tension_thinking.py"
SCHEDULER_PY = REPO_ROOT / "src" / "soul" / "scheduler.py"

#: 沙盒假時鐘（epoch 秒）——固定值，0 真實等待。
FAKE_NOW = 1_700_000_000

#: 沙盒線頭：2 筆 active 資訊缺口（``updated_at`` 很舊 ⇒ 過 24h 門檻）。
SANDBOX_ROWS: List[Dict[str, Any]] = [
    {
        "thread_id": "gap-1",
        "status": "active",
        "sage_fact_id": None,
        "title": "缺口一",
        "updated_at": "2023-11-13T14:00:00+00:00",
    },
    {
        "thread_id": "gap-2",
        "status": "active",
        "sage_fact_id": None,
        "title": "缺口二",
        "updated_at": "2023-11-12T14:00:00+00:00",
        "contradiction": "與舊推論衝突",
    },
]

#: 「真實 provider 呼叫」計數器（P7-T12：本檔**必須**全程為 0）。
REAL_LLM_CALLS: Dict[str, int] = {"attempted": 0}


def _real_llm_call_marker() -> None:
    """**本檔絕不呼叫**的唯一計數器遞增點。

    P7-T12 以 AST 驗證「本檔內**沒有**任何 ``_real_llm_call_marker()`` 呼叫點」
    ⇒ 該計數器在結構上**不可能**被推高（非空轉斷言）。
    """
    REAL_LLM_CALLS["attempted"] += 1


# ── Mock thinker（**零網路**）────────────────────────────────────────────
class _MockThinker:
    """Mock 思考器（**零網路、零真實 provider**）。

    只記錄呼叫次數並回一個固定的 :class:`ThinkOutcome`。
    """

    def __init__(self, gap_count: int = 2, decided: bool = False) -> None:
        self.calls = 0
        self.prompts: List[str] = []
        self.requests: List[Any] = []
        self._gap_count = int(gap_count)
        self._decided = bool(decided)

    async def think(self, req: Any, prompt: str) -> tt.ThinkOutcome:
        self.calls += 1
        self.prompts.append(prompt)
        self.requests.append(req)
        remaining = max(0, self._gap_count - 1)
        return tt.ThinkOutcome(
            partial_resolution=(1.0 / self._gap_count) if self._gap_count else 1.0,
            remaining_gap_count=remaining,
            decided=bool(self._decided and remaining == 0),
            reason="mock_thinker",
            think_prompt_sha256=hashlib.sha256(
                str(prompt).encode("utf-8")
            ).hexdigest(),
        )


class _RaisingThinker:
    """會拋例外的 Mock 思考器（測 fail-silent）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def think(self, req: Any, prompt: str) -> tt.ThinkOutcome:
        self.calls += 1
        raise RuntimeError("mock thinker failure (P7-T11)")


class _DriveSpy:
    """``drive_loop_async_production`` 的觀察用包裝（**不改語意**）。

    記錄「哪個 agent 被驅動過」＋「驅動結果」⇒ 可驗證「單輪最多 1 個 agent」。
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self.calls: List[Tuple[str, Any]] = []

    async def __call__(self, agent_id: str, *, thinker: Any, now: Optional[int] = None) -> Any:
        result = await self._real(agent_id, thinker=thinker, now=now)
        self.calls.append((agent_id, result))
        return result

    @property
    def agents(self) -> List[str]:
        return [agent_id for agent_id, _ in self.calls]


# ── 沙盒資料種子 ─────────────────────────────────────────────────────────
def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _seed_sandbox_data(agent_id: str = "agent_akane") -> None:
    """把線頭檔與關係檔寫進**隔離**資料根（沙盒；不碰生產）。"""
    agent_dir = Path(data_root()) / "soul" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    rows_out = []
    for row in SANDBOX_ROWS:
        item = dict(row)
        item.setdefault("created_at", item["updated_at"])
        item.setdefault("dissolved_at", None)
        rows_out.append(item)
    (agent_dir / "life_threads.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows_out) + "\n",
        encoding="utf-8",
    )
    relationships = {
        "agent_id": agent_id,
        "schema_version": 1,
        "others": {
            "user_bryan": {
                "impression_tags": ["sandbox"],
                "relational_band": "familiar",
                "interaction_count": 42,
                "last_interaction_at": _iso(FAKE_NOW - 30 * 3600),
            }
        },
    }
    (agent_dir / "relationships.json").write_text(
        json.dumps(relationships, ensure_ascii=False), encoding="utf-8"
    )


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


# ── AST 工具 ─────────────────────────────────────────────────────────────
def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_src(path))


def _imported_module_names(path: Path) -> set:
    """檔內**所有** import 的模組名（含 ``from X import Y`` 的 ``X.Y`` 形式）。"""
    tree = _tree(path)
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相對 import（本 repo 不用 ⇒ 略過）
                continue
            base = node.module or ""
            names.add(base)
            for alias in node.names:
                names.add(f"{base}.{alias.name}")
    return names


def _find_function(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    raise AssertionError(f"{path} 內找不到函式 {name}")


def _find_method(path: Path, cls_name: str, meth_name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == meth_name:
                        return item  # type: ignore[return-value]
    raise AssertionError(f"class {cls_name} 內找不到方法 {meth_name}")


def _dotted_name(node: ast.AST) -> str:
    """把 ``a.b.c`` 形式的呼叫目標還原成點號字串（``cfg.get`` ⇒ ``"cfg.get"``）。"""
    parts: List[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        parts.append("?")
    return ".".join(reversed(parts))


def _call_names(node: ast.AST) -> List[str]:
    """節點內所有「被呼叫的函式名」（含 ``a.b.c()`` 的最後一段）。"""
    names: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def _call_dotted_names(node: ast.AST) -> List[str]:
    """節點內所有「被呼叫的函式」的**完整點號名**（``cfg.get`` ⇒ ``"cfg.get"``）。"""
    names: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            names.append(_dotted_name(child.func))
    return names


# ── fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> bool:
    """開啟本票旗標（沙盒）。"""
    monkeypatch.setenv(tcl.FEATURE_ENV, "1")
    return True


@pytest.fixture
def disabled(monkeypatch: pytest.MonkeyPatch) -> bool:
    """旗標**未設**（缺省 OFF）。"""
    monkeypatch.delenv(tcl.FEATURE_ENV, raising=False)
    return False


@pytest.fixture
def mock_thinker(monkeypatch: pytest.MonkeyPatch) -> _MockThinker:
    """把接線 helper 換成 Mock thinker（**零真實 provider**）。"""
    thinker = _MockThinker()

    def _fake_build(cfg: Any, bus: Any, *, calls: Any = None) -> Any:
        return thinker

    monkeypatch.setattr(tt, "build_llm_thinker_from_config", _fake_build)
    return thinker


@pytest.fixture
def drive_spy(monkeypatch: pytest.MonkeyPatch) -> _DriveSpy:
    """觀察 ``drive_loop_async_production`` 被呼叫了幾次、對哪個 agent。"""
    spy = _DriveSpy(tcl.drive_loop_async_production)
    monkeypatch.setattr(tcl, "drive_loop_async_production", spy)
    return spy


@pytest.fixture
def fake_cfg(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """把 ``configs.loader.load_config`` 換成固定設定（不讀 .env、不碰 API key）。"""
    cfg: Dict[str, Any] = {"llm": {"provider": "mock", "model": "mock-model"}}
    monkeypatch.setattr("configs.loader.load_config", lambda: cfg)
    return cfg


def _make_scheduler(agents: List[str]) -> SoulScheduler:
    """建立一個只掛指定 agent 的 scheduler（**不動全域註冊表**）。

    🔴 直接指派 ``_all_agents``（而非 ``register()``）是刻意的：
    ``register()`` 會呼叫 ``src.soul.motive.register_agent_id()`` 污染**全域**狀態
    ⇒ 會讓本檔的結果依賴測試執行順序。
    """
    sched = SoulScheduler()
    sched._all_agents = list(agents)
    return sched


def _night_now(sched: SoulScheduler) -> datetime:
    """落在既有 night slot 檢查窗內（±60s）的時刻。"""
    base = datetime.now(tz=timezone.utc)
    return base.replace(
        hour=sched.night_time.hour,
        minute=sched.night_time.minute,
        second=5,
        microsecond=0,
    )


# ── P7-T1：旗標缺省 OFF ⇒ 排程器分支完全不動作 ──────────────────────────
def test_p7_t1_flag_off_scheduler_branch_is_inert(
    disabled: bool,
    mock_thinker: _MockThinker,
    drive_spy: _DriveSpy,
    fake_cfg: Dict[str, Any],
) -> None:
    """旗標未設 ⇒ 0 次驅動、0 次 thinker 呼叫、0 檔案（連設定都不載入）。"""
    _seed_sandbox_data()
    sched = _make_scheduler(["agent_akane"])
    inner_life = Path(data_root()) / "inner_life"
    before = set(p.relative_to(inner_life).as_posix() for p in inner_life.glob("*")) if inner_life.is_dir() else set()

    asyncio.run(sched._fire_tension_drive_loop(_night_now(sched)))

    assert drive_spy.calls == [], "旗標 OFF ⇒ 不得驅動任何 agent"
    assert mock_thinker.calls == 0, "旗標 OFF ⇒ 不得呼叫 thinker"
    after = set(p.relative_to(inner_life).as_posix() for p in inner_life.glob("*")) if inner_life.is_dir() else set()
    assert after == before, "旗標 OFF ⇒ 不得建立或修改任何檔案"


# ── P7-T2：旗標 ON + night slot + 未重複 ⇒ 恰好 1 agent、恰好 1 次呼叫 ──
def test_p7_t2_flag_on_night_slot_triggers_exactly_one(
    enabled: bool,
    mock_thinker: _MockThinker,
    drive_spy: _DriveSpy,
    fake_cfg: Dict[str, Any],
) -> None:
    """旗標 ON ＋ night slot ＋ 首次 ⇒ 恰好 1 個 agent、恰好 1 次 thinker 呼叫。"""
    _seed_sandbox_data()
    sched = _make_scheduler(["agent_akane"])

    asyncio.run(sched._fire_tension_drive_loop(_night_now(sched)))

    assert drive_spy.agents == ["agent_akane"], "應恰好驅動 1 個 agent"
    assert mock_thinker.calls == 1, "應恰好呼叫 thinker 1 次"
    rows = _read_jsonl(Path(data_root()) / "inner_life" / tcl.WORKING_STATE_FILENAME)
    assert len(rows) == 1, f"working state 應恰好 1 筆，實得 {len(rows)}"
    assert rows[0]["agent_id"] == "agent_akane"


# ── P7-T3：at-most-once（同一 agent:date 再觸發 ⇒ 不再呼叫）────────────
def test_p7_t3_at_most_once_same_agent_same_date(
    enabled: bool,
    mock_thinker: _MockThinker,
    drive_spy: _DriveSpy,
    fake_cfg: Dict[str, Any],
) -> None:
    """同一 ``agent:date`` 再觸發 ⇒ 呼叫計數**不增加**（30s tick 重入不重複花費）。"""
    _seed_sandbox_data()
    sched = _make_scheduler(["agent_akane"])
    now = _night_now(sched)

    asyncio.run(sched._fire_tension_drive_loop(now))
    first_calls = mock_thinker.calls
    assert first_calls == 1

    # 30s tick 重入同一槽窗（同一日期、同一 agent）。
    asyncio.run(sched._fire_tension_drive_loop(now))
    asyncio.run(sched._fire_tension_drive_loop(now))

    assert mock_thinker.calls == first_calls, "at-most-once：同一 agent:date 不得重複呼叫"
    assert len(drive_spy.calls) == 1, "at-most-once：同一 agent:date 不得重複驅動"


# ── P7-T4：每日上限（多個 agent 候選時，單輪最多 1 個）─────────────────
def test_p7_t4_at_most_one_agent_per_round(
    enabled: bool,
    mock_thinker: _MockThinker,
    drive_spy: _DriveSpy,
    fake_cfg: Dict[str, Any],
) -> None:
    """多個 agent 候選時：**單輪**最多處理 1 個 agent；**整日**最多 1 個 agent。"""
    _seed_sandbox_data()
    sched = _make_scheduler(["agent_akane", "agent_anna", "agent_aoi"])
    now = _night_now(sched)

    asyncio.run(sched._fire_tension_drive_loop(now))
    first_round = list(drive_spy.agents)
    assert len(first_round) <= 1, f"單輪最多 1 個 agent，實得 {first_round}"
    assert len(first_round) == 1, "應確實處理 1 個 agent（否則守衛空轉）"
    assert mock_thinker.calls <= 1, "單輪最多 1 次 thinker 呼叫"

    # 30s tick 重入同一槽窗：當日名額已被用掉 ⇒ **不得**再處理另一個 agent。
    asyncio.run(sched._fire_tension_drive_loop(now))
    asyncio.run(sched._fire_tension_drive_loop(now))

    distinct = set(drive_spy.agents)
    assert len(distinct) <= 1, f"每日最多 1 個 agent，實得 {sorted(distinct)}"
    assert len(drive_spy.agents) == len(first_round), (
        "同一日不得再驅動其他 agent（每日上限）"
    )


# ── P7-T5：寫入面（只寫 working_state.jsonl）────────────────────────────
def test_p7_t5_write_face_only_touches_working_state(
    enabled: bool,
) -> None:
    """生產寫入面**只**寫 ``working_state.jsonl``；其餘生產檔位元不變、無新檔。"""
    _seed_sandbox_data()
    inner_life = Path(data_root()) / "inner_life"
    inner_life.mkdir(parents=True, exist_ok=True)

    # 先放兩個「其他生產檔」，驗證它們**位元不變**（不是「不存在」）。
    tension_store = inner_life / tcl.TENSION_STORE_FILENAME
    trace_store = inner_life / "trace.jsonl"
    tension_store.write_text('{"pre":"existing"}\n', encoding="utf-8")
    trace_store.write_text('{"pre":"existing"}\n', encoding="utf-8")
    tension_before = tension_store.read_bytes()
    trace_before = trace_store.read_bytes()

    result = asyncio.run(
        tcl.drive_loop_async_production(
            "agent_akane", thinker=_MockThinker(), now=FAKE_NOW
        )
    )
    assert result is not None, "生產寫入面應產出結果"
    assert result["wrote_tension_event"] is False

    # ① working_state.jsonl 恰好 1 筆。
    rows = _read_jsonl(inner_life / tcl.WORKING_STATE_FILENAME)
    assert len(rows) == 1, f"working state 應恰好 1 筆，實得 {len(rows)}"
    assert rows[0]["agent_id"] == "agent_akane"

    # ② 其他生產檔**位元不變**。
    assert tension_store.read_bytes() == tension_before, "epistemic_tensions.jsonl 不得被改動"
    assert trace_store.read_bytes() == trace_before, "trace.jsonl 不得被改動"

    # ③ **無新檔**（除 working_state.jsonl 外）。
    produced = {p.name for p in inner_life.iterdir()}
    assert produced == {
        tcl.WORKING_STATE_FILENAME,
        tcl.TENSION_STORE_FILENAME,
        "trace.jsonl",
    }, f"生產寫入面建立了非預期檔案：{sorted(produced)}"

    # ④ P5-① 的生產唯讀面讀得到（資料面接線的對稱性）。
    lines = tcl.format_working_state_lines_production("agent_akane")
    assert len(lines) == 1, f"生產唯讀面應回 1 行，實得 {lines}"
    assert "張力未解" in lines[0]


# ── P7-T6：路徑不可注入 ──────────────────────────────────────────────────
def test_p7_t6_production_interfaces_take_no_path() -> None:
    """兩個生產介面的簽章都**不含**任何路徑參數（也沒有 ``**kwargs`` 逃生門）。"""
    banned = {"store_dir", "store", "path", "dir", "root", "data_dir", "file"}
    for func_name in ("drive_loop_async_production", "format_working_state_lines_production"):
        func = getattr(tcl, func_name)
        sig = inspect.signature(func)
        for name, param in sig.parameters.items():
            assert name not in banned, f"{func_name} 不得接受路徑參數 {name}"
            assert param.kind not in (
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ), f"{func_name} 不得以 *args/**kwargs 夾帶路徑"


# ── P7-T7：沙盒守門未放寬 ────────────────────────────────────────────────
def test_p7_t7_sandbox_gate_still_rejects_production_store_dir(
    enabled: bool,
) -> None:
    """``check_store_dir`` 仍拒絕生產路徑；``drive_loop_async`` 對生產路徑仍回空、不寫。"""
    _seed_sandbox_data()
    prod_store = Path(data_root()) / "inner_life"

    allowed, reason = tcl.check_store_dir(prod_store)
    assert allowed is False, "生產路徑必須被沙盒守門拒絕"
    assert "生產資料根" in reason or "repo data" in reason, reason

    result = asyncio.run(
        tcl.drive_loop_async(
            "agent_akane", FAKE_NOW, prod_store, thinker=_MockThinker()
        )
    )
    assert result is None, "沙盒路徑對生產 store_dir 必須回 None"
    assert not (prod_store / tcl.WORKING_STATE_FILENAME).exists(), "不得寫入生產 working state"
    assert not (prod_store / tcl.TENSION_STORE_FILENAME).exists(), "不得寫入生產張力承載"


# ── P7-T8：預算硬編碼（字面值、不可由環境變數改變）──────────────────────
def test_p7_t8_budget_literals_are_hardcoded() -> None:
    """``max_tokens=400``／``max_calls=1``／``max_retries=0`` 必須是**字面值**。"""
    fn = _find_function(THINKING_PY, "build_llm_thinker_from_config")

    # ① AST：LLMThinker(...) 的三個預算關鍵字必須是 int 常數。
    llm_calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LLMThinker"
    ]
    assert len(llm_calls) == 1, f"應恰好有一處 LLMThinker(...) 構造，實得 {len(llm_calls)}"
    kwargs = {kw.arg: kw.value for kw in llm_calls[0].keywords if kw.arg}
    for name, expected in (("max_tokens", 400), ("max_calls", 1), ("max_retries", 0)):
        assert name in kwargs, f"接線處必須明寫 {name}"
        node = kwargs[name]
        assert isinstance(node, ast.Constant), f"{name} 必須是字面值（不可由變數／環境取得）"
        assert isinstance(node.value, int) and not isinstance(node.value, bool)
        assert node.value == expected, f"{name} 必須是 {expected}，實得 {node.value}"

    # ② AST：不得讀環境變數來決定這三個參數。
    env_reads = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute)
        and node.attr in {"environ", "getenv"}
    ]
    assert not env_reads, "預算不得由環境變數讀取"

    # ③ 行為：用 mock provider 組出來的 thinker，預算就是 400/1/0。
    thinker = tt.build_llm_thinker_from_config(
        {"llm": {"provider": "mock", "model": "mock-model"}}, None
    )
    assert thinker.max_tokens == 400
    assert thinker.max_calls == 1
    assert thinker.max_retries == 0
    # 本檔**不呼叫**它 ⇒ 呼叫計數維持 0（0 真實 provider 呼叫）。
    assert int(thinker.calls.get("attempted", 0)) == 0


# ── P7-T9：不繞過 proxy（不自建 HTTP 客戶端）───────────────────────────
def test_p7_t9_wiring_helper_does_not_bypass_proxy() -> None:
    """接線 helper 不得 import 網路套件、不得自建 HTTP 客戶端。"""
    fn = _find_function(THINKING_PY, "build_llm_thinker_from_config")

    # ① 模組層：不得 import requests／httpx／urllib／socket／aiohttp。
    banned = {"requests", "httpx", "urllib", "socket", "aiohttp", "http"}
    imported = _imported_module_names(THINKING_PY)
    for name in imported:
        head = name.split(".")[0]
        assert head not in banned, f"tension_thinking 不得 import {name}"

    # ② 函式層：不得 import 網路套件。
    local_imports = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            local_imports.add(node.module or "")
    for name in local_imports:
        assert name.split(".")[0] not in banned, f"接線 helper 不得 import {name}"

    # ③ 不得自建 HTTP 客戶端（以**完整點號名**比對 ⇒ ``cfg.get`` 不會誤判）。
    banned_exact = {"urlopen", "Client", "AsyncClient", "Session", "AsyncSession"}
    banned_prefixes = (
        "requests.",
        "httpx.",
        "urllib.",
        "aiohttp.",
        "http.client.",
        "http.client",
    )
    for dotted in _call_dotted_names(fn):
        assert dotted not in banned_exact, f"接線 helper 不得自建 HTTP 客戶端（{dotted}）"
        assert not dotted.startswith(
            banned_prefixes
        ), f"接線 helper 不得自建 HTTP 客戶端（{dotted}）"

    # ④ 行為：組出來的 thinker 其 proxy 必須是既有 LLMProxy（不是自造物件）。
    from src.llm.proxy import LLMProxy

    thinker = tt.build_llm_thinker_from_config(
        {"llm": {"provider": "mock", "model": "mock-model"}}, None
    )
    assert isinstance(getattr(thinker, "_proxy", None), LLMProxy)


# ── P7-T10：0 新計時器／0 新 task／0 新 sleep（AST）────────────────────
def test_p7_t10_no_new_timer_task_or_sleep() -> None:
    """``_fire_tension_drive_loop()`` 內不得有 sleep／while／create_task。"""
    fn = _find_method(SCHEDULER_PY, "SoulScheduler", "_fire_tension_drive_loop")

    assert not any(
        isinstance(node, ast.While) for node in ast.walk(fn)
    ), "分支內不得有 while（0 新迴圈）"

    banned_calls = {
        "sleep",
        "create_task",
        "ensure_future",
        "wait_for",
        "run_in_executor",
        "Timer",
    }
    for name in _call_names(fn):
        assert name not in banned_calls, f"分支內不得呼叫 {name}（0 新計時器／0 新 task／0 新 sleep）"

    # 掛點必須在既有 30s wake 內（additive 分支）。
    run_loop = _find_method(SCHEDULER_PY, "SoulScheduler", "_run_loop")
    assert "_fire_tension_drive_loop" in _call_names(run_loop), "分支必須掛在既有 30s wake 內"


# ── P7-T11：fail-silent（thinker 拋例外 ⇒ 排程器不 raise）─────────────
def test_p7_t11_thinker_exception_is_fail_silent(
    enabled: bool,
    fake_cfg: Dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """thinker 拋例外 ⇒ 排程器**不 raise**、只 log warning（主循環不受影響）。"""
    _seed_sandbox_data()
    sched = _make_scheduler(["agent_akane"])
    now = _night_now(sched)

    # ① 閉環層的 fail-silent：thinker 拋例外 ⇒ 回 None、零寫入。
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            tcl.drive_loop_async_production(
                "agent_akane", thinker=_RaisingThinker(), now=FAKE_NOW
            )
        )
    assert result is None, "thinker 拋例外 ⇒ 生產寫入面必須回 None"
    assert not (
        Path(data_root()) / "inner_life" / tcl.WORKING_STATE_FILENAME
    ).exists(), "失敗不得寫半套記錄"

    # ② 排程器層的 fail-silent：驅動函式本身拋例外 ⇒ 排程器不得 raise。
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("mock drive failure (P7-T11)")

    original = tcl.drive_loop_async_production
    tcl.drive_loop_async_production = _boom  # type: ignore[assignment]
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            asyncio.run(sched._fire_tension_drive_loop(now))  # **不得** raise
    finally:
        tcl.drive_loop_async_production = original  # type: ignore[assignment]

    assert any(
        "tension drive loop" in record.getMessage() for record in caplog.records
    ), f"應有 warning 記錄，實得 {[r.getMessage() for r in caplog.records]}"


# ── P7-T12：0 真實 LLM（本檔零真實 provider 呼叫）──────────────────────
def test_p7_t12_guard_file_makes_no_real_provider_call() -> None:
    """本檔**零真實 provider 呼叫**：無網路 import、無真實 proxy/backend 構造點。"""
    tree = _tree(GUARD_FILE)

    # ① 不得 import 任何網路／行程啟動套件。
    banned = {"requests", "httpx", "urllib", "socket", "aiohttp", "subprocess"}
    for name in _imported_module_names(GUARD_FILE):
        assert name.split(".")[0] not in banned, f"守衛檔不得 import {name}"

    # ② 不得構造真實 proxy／backend。
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in {
                "create_llm_proxy",
                "create_llm_backend",
                "LLMProxy",
                "ClaudeBackend",
                "OpenAIBackend",
            }, f"守衛檔不得構造真實 provider（{name}）"

    # ③ 「真實呼叫計數器」在**結構上**不可能被推高（無呼叫點）。
    marker_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_real_llm_call_marker"
    ]
    assert not marker_calls, "守衛檔不得呼叫真實 provider 計數器"
    assert REAL_LLM_CALLS["attempted"] == 0, "守衛檔的真實 provider 呼叫數必須為 0"

    # ④ 本檔使用的思考器一律是本檔自帶的 Mock（不得是 LLMThinker）。
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LLMThinker"
        for node in ast.walk(tree)
    ), "守衛檔不得構造 LLMThinker（避免任何真實 provider 路徑）"
