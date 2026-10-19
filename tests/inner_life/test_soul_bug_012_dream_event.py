# -*- coding: utf-8 -*-
"""
SOUL-BUG-012 — dream:event 昇華鏈路的雙重阻斷修復守衛。

缺陷（在 commit 04da8a3 實測確認）：
  阻斷 A 時序倒置：`_event_writer_executor` 於 run_server.py:1606 呼叫
    `submission_gate.submit()`、:1610 呼叫 `_elevate_check()`，而日記寫入
    `_writer.write_event()` 延遲至 :1618。submit 觸發 Adapter 的 A-i 反查時
    日記尚未落盤 → 反查必然落空 → fail-closed 拒絕建立節點。
  實測：26 筆 dream:event 產出 0 顆圖譜節點（轉化率 0%）。

修法（三段式隔離，行為等價性經生產資料驗證）：
  段1 create_event（失敗隔離）→ 段2 write_event（無保護，必執行）
  → 段3 submit + _elevate_check（失敗隔離）

🔬 測試手法說明（為何不用 import）：
  `scripts/run_server.py` 模組層級有 5 個副作用 ——
    :83 mkdir、:87 open(..., "a") 開啟生產 faulthandler.log、
    :185 log 輪替、:248 faulthandler.enable()（不可逆地汙染 pytest 行程）、
    :230-240 basicConfig + setLevel（全域 logging）。
  且 `src/paths.data_root()` 是**行程期 singleton**，一旦被任何測試先觸發就凍結
  在生產根。⇒ `import run_server` 會寫進生產資料並污染全域狀態。
  故採 repo 既有慣例（見 tests/test_crash_obs2_faulthandler_split.py 的
  `_exec_run_server_isolated`）：**AST 只抽出該函式定義 → 在隔離 namespace 裡
  exec → 注入 7 個 stub**。自由變數面已用 AST 量得僅 7 個需 stub。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_SERVER = ROOT / "scripts" / "run_server.py"
EXECUTOR_NAME = "_event_writer_executor"

#: 自由變數面實測：11 個 FREE NAMES 扣掉 builtins（Exception/str/type/_e）後，
#: 真正需要 stub 的就是這 7 個。若將來新增依賴，此清單必須同步 —— 否則
#: exec 會以 NameError 失敗，測試會紅（這是刻意保留的失敗訊號）。
STUB_NAMES = (
    "Provenance",
    "TRIGGER_TYPE_DREAM_EVENT",
    "inner_life_writer",
    "submission_gate",
    "_elevate_check",
    "get_dream_event_writer",
    "logger",
)


# --------------------------------------------------------------------------- #
# Call Recorder
# --------------------------------------------------------------------------- #
class Recorder:
    """記錄實際呼叫順序 —— 斷言的是行為，不是原始碼行號。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def hit(self, name: str) -> None:
        self.calls.append(name)

    def index(self, name: str) -> int | None:
        return self.calls.index(name) if name in self.calls else None


class FakeEvent:
    def __init__(self) -> None:
        self.event_id = "evt-soul-bug-012"
        self.provenance = type("P", (), {"actor_id": "agent_test"})()


def _namespace(rec: Recorder, *, create_event_raises: bool = False) -> dict:
    class Provenance:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeInnerLifeWriter:
        # run_server.py:1599 為「同步」呼叫（非 await）—— stub 必須同步，
        # 否則回傳 coroutine 物件會讓 _event.event_id 拋 AttributeError 被
        # except 吃掉，導致 submit 從未被觀察到（MUT-01 假紅）。
        def create_event(self, provenance=None):
            rec.hit("create_event")
            if create_event_raises:
                raise RuntimeError("simulated InnerLifeWriter.create_event failure")
            return FakeEvent()

    class FakeSubmissionGate:
        def submit(self, event_id, agent_id=None):
            rec.hit("submit")

    class FakeDreamEventWriter:
        async def write_event(self, agent_id, inner_life_event_id=None):
            rec.hit(f"write_event(id={inner_life_event_id})")

    class FakeLogger:
        def warning(self, *a, **kw):
            rec.hit("logger.warning")

    return {
        "Provenance": Provenance,
        "TRIGGER_TYPE_DREAM_EVENT": "dream:event",
        "inner_life_writer": FakeInnerLifeWriter(),
        "submission_gate": FakeSubmissionGate(),
        "_elevate_check": lambda: rec.hit("_elevate_check"),
        "get_dream_event_writer": lambda: FakeDreamEventWriter(),
        "logger": FakeLogger(),
    }


def _load_executor():
    """AST 抽出 _event_writer_executor 定義，exec 進隔離 namespace。"""
    src = RUN_SERVER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == EXECUTOR_NAME:
            node = n
            break
    assert node is not None, f"找不到 {EXECUTOR_NAME}"
    assert not node.decorator_list, f"{EXECUTOR_NAME} 出現 decorator，隔離假設需重審"

    segment = ast.get_source_segment(src, node)
    assert segment, "ast.get_source_segment 回傳空 —— 抽取手法失效"
    assert segment.lstrip().startswith("async def"), "抽出的不是函式定義"

    ns: dict = {}
    exec(compile(segment, str(RUN_SERVER), "exec"), ns)  # noqa: S102 — 測試內受控 exec
    fn = ns[EXECUTOR_NAME]
    return fn, node, src


def _run(*, create_event_raises: bool = False) -> Recorder:
    fn, _, _ = _load_executor()
    rec = Recorder()
    ns = _namespace(rec, create_event_raises=create_event_raises)
    # 把 stub 覆蓋進已 exec 出函式的 globals
    fn.__globals__.update(ns)
    asyncio.run(fn("agent_test"))
    return rec


# --------------------------------------------------------------------------- #
# MUT-01：時序守衛（本測試的主要防線）
# --------------------------------------------------------------------------- #
def test_m1_diary_is_written_before_event_is_submitted():
    """MUT-01：write_event 必須先於 submit，否則 A-i 反查必然落空。"""
    rec = _run()
    iw, isub = rec.index("write_event(id=evt-soul-bug-012)"), rec.index("submit")
    assert isub is not None, f"沙箱未觀察到 submit，測量本身失效：{rec.calls}"
    assert iw is not None, f"沙箱未觀察到 write_event，測量本身失效：{rec.calls}"
    assert iw < isub, (
        f"日記寫入落後於事件提交 → A-i 反查會查不到日記 → fail-closed 拒絕建節點。"
        f"實際順序：{rec.calls}"
    )


# --------------------------------------------------------------------------- #
# 失敗路徑行為等價性（防「閹割日記生成」的結構性退縮）
# --------------------------------------------------------------------------- #
def test_failure_path_still_writes_diary_with_none_id():
    """create_event 失敗時：logger.warning → write_event(id=None)，且不得 submit。"""
    rec = _run(create_event_raises=True)
    assert rec.calls[0] == "create_event"
    assert "logger.warning" in rec.calls, f"失敗必須被記錄：{rec.calls}"
    assert any(c.startswith("write_event(id=None)") for c in rec.calls), (
        f"create_event 失敗仍必須落盤日記（既有合約）：{rec.calls}"
    )
    assert rec.calls.index("logger.warning") < next(
        i for i, c in enumerate(rec.calls) if c.startswith("write_event")
    ), f"必須先記錄失敗再落盤：{rec.calls}"
    assert "submit" not in rec.calls, (
        f"事件未建立時不得提交（A-i 反查會查無此事）：{rec.calls}"
    )


def test_happy_path_has_no_warning():
    rec = _run()
    assert "logger.warning" not in rec.calls, f"正常路徑不該有失敗告警：{rec.calls}"
    assert rec.calls == ["create_event", "write_event(id=evt-soul-bug-012)",
                         "submit", "_elevate_check"], f"happy path 順序不符：{rec.calls}"


# --------------------------------------------------------------------------- #
# 結構防線：三段式隔離
# --------------------------------------------------------------------------- #
def test_executor_has_three_isolated_segments():
    """段1 try / 段2 裸 write_event / 段3 try —— 防止日後又被打回單一 try。"""
    _, node, src = _load_executor()
    stmts = [s for s in node.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    kinds = [s.__class__.__name__ for s in stmts]
    assert kinds == ["Assign", "Try", "Assign", "Expr", "Try"], (
        f"executor 應為 5 條陳述句（賦值/段1try/取writer/落盤/段3try），實得 {kinds}"
    )
    # 段 2 必須在「兩個 try 之間」且不在任何 try 內 —— 這是日記必落盤的結構保證
    write_call = None
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Call):
                nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if nm == "write_event":
                    write_call = n
                    break
        if write_call:
            break
    assert write_call is not None, "找不到 write_event 呼叫"
    assert isinstance(stmts[3], ast.Expr), "write_event 必須是獨立陳述句"


def test_stub_surface_matches_measured_free_names():
    """自由變數面漂移守衛：新增依賴卻沒加 stub → 這裡會先紅，而不是在測試中炸。"""
    _, node, _ = _load_executor()
    assigned = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                and not isinstance(n.ctx, ast.Load)}
    # `except ... as _e` 的綁定名存在於 ExceptHandler.name（純字串），不是 Name 節點，
    # 必須手動納入 assigned，否則會被誤判成需要 stub 的自由變數。
    assigned |= {h.name for h in ast.walk(node)
                 if isinstance(h, ast.ExceptHandler) and h.name}
    params = {a.arg for a in node.args.args}
    loads = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
             and isinstance(n.ctx, ast.Load)}
    free = loads - assigned - params
    import builtins
    needed = free - set(dir(builtins))
    missing = needed - set(STUB_NAMES)
    assert not missing, (
        f"executor 新增了自由變數 {sorted(missing)} 但 STUB_NAMES 未同步 —— "
        f"隔離 namespace 會 NameError"
    )