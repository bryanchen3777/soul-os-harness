# -*- coding: utf-8 -*-
"""tests/test_sim_accel_thread.py — SIM-ACCEL-THREAD-DISSOLVE 的守衛測試 ＋ 變異實驗載體。

為什麼存在
==========
2026-10-19 上線的「生活線頭 → 昇華接縫」把 35 條線頭蓋章進行程內登記表
（`src/soul/life_thread_orchestrator.py::_ELEVATION_EMITTED`）⇒ 同一行程內不再重發。
「增量機制面對**未來**的新線頭是否真的暢通」因此被真實生活推進的時間卡住。

本檔守護三條語意（每一條都必須有**會轉紅**的變異實驗，否則就是盲腸守衛）：

A. **M2 的 age/idle 軸只是諮詢訊號**（`src/soul/life_thread_dissolution.py` 步驟 4）：
   active 線頭即使活了 14 天 + 1 秒，`should_mutate` 仍必須是 `False`、
   `extra_metadata["advisory"]` 仍是 `True` ⇒ **不得**變更任何狀態。
   （2026-10-20 主大腦工單更正：原票「靠虛擬時鐘讓線頭轉 completed」**不成立**。）

B. **接縫的 at-most-once**：預先播種 35 條歷史線頭 ⇒ 接縫一筆都不重發；
   只有本次推進的那一條新線頭會發射（第 36 筆）；同一行程再跑一次仍是一筆都不發。

C. **注入缺席必須 fail-closed**（`src/soul/life_thread_dissolution_exec.py`）：
   `llm_call is None` ⇒ `skipped_no_llm`（0 呼叫）；LLM 回傳形狀不符 ⇒ `parse_failed`
   ＋ **不得寫入事實**。

變異實驗怎麼跑
==============
本檔支援環境變數 `SIM_ACCEL_MUTANT=<變異名>`：命中時，守衛測試會改用**變異體**
（來源做字串替換後在獨立命名空間 exec，**不動 repo 內任何檔案**）⇒
同一條測試必須**轉紅**。例如：

    $env:SIM_ACCEL_MUTANT='advisory_as_dissolution'
    .venv\\Scripts\\python.exe -m pytest -q tests/test_sim_accel_thread.py::test_m2_active_advisory_never_mutates
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CLI_PATH = REPO_ROOT / "scripts" / "sim_accel_thread_cli.py"
DISSOLUTION_PATH = REPO_ROOT / "src" / "soul" / "life_thread_dissolution.py"
ORCHESTRATOR_PATH = REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
EXEC_PATH = REPO_ROOT / "src" / "soul" / "life_thread_dissolution_exec.py"

#: 變異實驗的環境變數（值＝變異名稱）。
MUTANT_ENV = "SIM_ACCEL_MUTANT"

#: 生產 agent（本票的預設目標）。
AGENT_ID = "agent_mahiru"


# ─────────────────────────────────────────────────────────────────────────────
# 變異錨點（逐字；**必須唯一命中**，否則 `_load_mutant` 會直接失敗）
# ─────────────────────────────────────────────────────────────────────────────
#: M1 —— 「讓 M2 的 advisory 誤發成真溶解」：諮詢訊號分支的 `should_mutate` 改成 True。
MUTANT_ANCHOR_ADVISORY = """            return DissolutionEvaluation(
                thread_id=thread_id,
                target_status=ThreadStatus.ACTIVE,
                reason=reason,
                should_mutate=False,
                reflection_prompt=None,
                extra_metadata=extra,
            )"""
MUTANT_REPLACE_ADVISORY = """            return DissolutionEvaluation(
                thread_id=thread_id,
                target_status=ThreadStatus.ACTIVE,
                reason=reason,
                should_mutate=True,
                reflection_prompt=None,
                extra_metadata=extra,
            )"""

#: M2 —— 移除接縫的 at-most-once（`_ELEVATION_EMITTED` 的跳過）。
MUTANT_ANCHOR_AT_MOST_ONCE = """            if thread_id in _ELEVATION_EMITTED:
                continue"""
MUTANT_REPLACE_AT_MOST_ONCE = """            if False:
                continue"""

#: M3 —— 讓「LLM 回傳形狀不符」被誤接受（非字串也當敘述文字）⇒ 事實會被寫進去。
MUTANT_ANCHOR_NARRATIVE = """    for key in NARRATIVE_KEYS:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_text(value, MAX_FIELD_CHARS)
    return None"""
MUTANT_REPLACE_NARRATIVE = """    for key in NARRATIVE_KEYS:
        value = mapping.get(key)
        if value is not None:
            return _normalize_text(str(value), MAX_FIELD_CHARS)
    return None"""

#: M4 —— 移除「依賴缺席就跳過」的閘門（`llm_call is None`）。
MUTANT_ANCHOR_NO_LLM = """    if llm_call is None:
        logger.info(
            "%sskip thread_id=%s status=%s", _LOG_PREFIX, thread_id, STATUS_SKIPPED_NO_LLM
        )"""
MUTANT_REPLACE_NO_LLM = """    if False:
        logger.info(
            "%sskip thread_id=%s status=%s", _LOG_PREFIX, thread_id, STATUS_SKIPPED_NO_LLM
        )"""

#: M5 —— 移除沙盒 env 漂移的自我檢查。
MUTANT_ANCHOR_SANDBOX = """    actual = Path(env_root).resolve()
    if actual != expected:
        raise SandboxIsolationError(
            "[refuse] env 與沙盒不一致：SOUL_OS_DATA_DIR=%s 期望=%s" % (actual, expected)
        )"""
MUTANT_REPLACE_SANDBOX = """    actual = Path(env_root).resolve()
    if False:
        raise SandboxIsolationError(
            "[refuse] env 與沙盒不一致：SOUL_OS_DATA_DIR=%s 期望=%s" % (actual, expected)
        )"""


# ─────────────────────────────────────────────────────────────────────────────
# 載入工具
# ─────────────────────────────────────────────────────────────────────────────
def _load_cli() -> types.ModuleType:
    """以檔案路徑載入 CLI 模組（`scripts/` 不是套件 ⇒ 不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("sim_accel_thread_cli_mod", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_mutant(path: Path, anchor: str, replacement: str, name: str) -> types.ModuleType:
    """把來源做**字串替換**後在獨立命名空間 exec ⇒ 取得變異體。

    🔴 **不動 repo 內任何檔案**（只在記憶體裡改一份副本）⇒ 變異實驗不可能污染工作樹，
    也不可能碰到生產資料。
    """
    source = path.read_text(encoding="utf-8")
    count = source.count(anchor)
    assert count == 1, "變異錨點必須唯一命中（實際命中 %d 次）：%r" % (count, anchor[:80])
    mutated = source.replace(anchor, replacement, 1)
    module = types.ModuleType(name)
    # 🔴 必須**先登錄** `sys.modules`：`@dataclass` 會用 `sys.modules[cls.__module__]`
    # 解析字串型別註記；未登錄會在 import 期就爆（那不是「守衛轉紅」，是變異體沒起來）。
    sys.modules[name] = module
    module.__dict__["__file__"] = str(path)
    exec(compile(mutated, str(path), "exec"), module.__dict__)
    return module


def _exec_module() -> Any:
    """取得執行層模組（`src.soul.life_thread_dissolution_exec`）。

    🔴 **不得**在本檔直接 `import` 執行層：`tests/soul/test_life_thread_dissolution_exec.py::test_t54_importers_are_the_wiring_module_and_its_two_test_files`
    凍結了「import 執行層的檔案**恰為 3 個**」（接線層 ＋ 2 個既有測試檔）。
    ⇒ 經**唯一合法的 importer**（接線層）取得：`lt_wiring.lt_exec`。
    """
    from src.soul import life_thread_consolidation_wiring as lt_wiring

    return lt_wiring.lt_exec


def _subject() -> SimpleNamespace:
    """回傳本檔守護的主體（真實模組 ＋ 變異體替換點）。"""
    from src.soul import life_thread_consolidation_wiring as lt_wiring
    from src.soul import life_thread_dissolution as lt_diss
    from src.soul import life_thread_orchestrator as orch

    subject = SimpleNamespace(
        evaluate=lt_diss.evaluate_thread_dissolution,
        consolidate=lt_wiring.lt_exec.consolidate_terminal_thread,
        seam_module=orch,
        cli=_load_cli(),
        mutant=os.environ.get(MUTANT_ENV) or "",
    )
    mutant = subject.mutant
    if not mutant:
        return subject
    if mutant == "advisory_as_dissolution":
        subject.evaluate = _load_mutant(
            DISSOLUTION_PATH, MUTANT_ANCHOR_ADVISORY, MUTANT_REPLACE_ADVISORY, "mutant_dissolution"
        ).evaluate_thread_dissolution
    elif mutant == "at_most_once_removed":
        subject.seam_module = _load_mutant(
            ORCHESTRATOR_PATH,
            MUTANT_ANCHOR_AT_MOST_ONCE,
            MUTANT_REPLACE_AT_MOST_ONCE,
            "mutant_orchestrator",
        )
    elif mutant == "llm_shape_accepted":
        subject.consolidate = _load_mutant(
            EXEC_PATH, MUTANT_ANCHOR_NARRATIVE, MUTANT_REPLACE_NARRATIVE, "mutant_exec"
        ).consolidate_terminal_thread
    elif mutant == "no_llm_guard_removed":
        subject.consolidate = _load_mutant(
            EXEC_PATH, MUTANT_ANCHOR_NO_LLM, MUTANT_REPLACE_NO_LLM, "mutant_exec"
        ).consolidate_terminal_thread
    elif mutant == "sandbox_check_removed":
        subject.cli = _load_mutant(
            CLI_PATH, MUTANT_ANCHOR_SANDBOX, MUTANT_REPLACE_SANDBOX, "mutant_cli"
        )
    else:
        raise RuntimeError("未知的變異名稱：%s" % mutant)
    return subject


# ─────────────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def cli() -> types.ModuleType:
    return _load_cli()


@pytest.fixture(autouse=True)
def _clean_process_state():
    """每個測試前後清空**行程內**狀態（否則前一個測試會洩漏到下一個）。

    🔴 三處行程內記憶體狀態：
      1. `life_thread_orchestrator._ELEVATION_EMITTED`（接縫的 at-most-once 登記表）
         ⇒ 不清會讓前一個測試的播種壓掉下一個測試的「不播種應發射全部」。
      2. `life_thread_dissolution_exec._CONSOLIDATED`（沉澱冪等 sentinel）
         ⇒ 不清會讓第二個測試拿到 `skipped_duplicate`（0 寫入）。
      3. `life_thread_consolidation_wiring._WRITERS`（per-agent SAGE writer 快取）
         ⇒ 不清會讓第二個測試**寫進第一個測試的沙盒**。

    三者都有現成的「測試專用」清空 helper（生產路徑不呼叫）。
    """
    from src.soul import life_thread_consolidation_wiring as lt_wiring
    from src.soul import life_thread_orchestrator as orch

    exec_module = _exec_module()

    def _clear() -> None:
        orch._ELEVATION_EMITTED.clear()
        exec_module._clear_consolidated_registry()
        lt_wiring._reset_writers()

    _clear()
    yield
    _clear()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """建立沙盒並把 `data_root()` 指向它（**env 先設、行程期快取再刷新**）。"""
    from src.paths import data_root, reset_data_root

    module = _load_cli()
    box = tmp_path / "sandbox"
    module.prepare_sandbox(box, AGENT_ID)
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(box))
    monkeypatch.setenv("LIFE_THREAD_ELEVATION_ENABLED", "1")
    monkeypatch.setenv("LIFE_THREAD_CONSOLIDATION_ENABLED", "1")
    monkeypatch.setenv("LIFE_THREAD_CATCHUP_ENABLED", "")
    monkeypatch.setenv("LIFE_THREAD_BOOTSTRAP_ENABLED", "")
    reset_data_root()
    assert data_root().resolve() == box.resolve()
    yield box
    reset_data_root()


def _thread(
    status: str,
    created_at: datetime,
    updated_at: Optional[datetime] = None,
    **extra: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "thread_id": "11111111-2222-3333-4444-555555555555",
        "status": status,
        "created_at": created_at.isoformat(),
        "updated_at": (updated_at or created_at).isoformat(),
        "title": "測試線頭",
        "narrative_content": "測試正文",
    }
    data.update(extra)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# A. M2 的語意（advisory 不得變更狀態）
# ─────────────────────────────────────────────────────────────────────────────
def test_m2_age_boundary_is_strictly_greater():
    """存活軸邊界是**嚴格大於**：14 天整不算超限，+1 秒才算。"""
    subject = _subject()
    base = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    thread = _thread("active", base, base + timedelta(days=13))

    at_boundary = subject.evaluate(thread, base + timedelta(days=14), 14, 7, False)
    assert at_boundary.should_mutate is False
    assert at_boundary.reason.value == "none", "14 天整不應命中存活軸"
    assert at_boundary.extra_metadata["advisory"] is False

    beyond = subject.evaluate(thread, base + timedelta(days=14, seconds=1), 14, 7, False)
    assert beyond.reason.value == "time_horizon_exceeded"
    assert beyond.should_mutate is False
    assert beyond.extra_metadata["advisory"] is True


def test_m2_active_advisory_never_mutates():
    """🔴 變異 M1 的守衛：active 線頭命中存活軸 ⇒ **只是諮詢訊號**，不得變更狀態。

    （2026-10-20 工單更正：active 線頭**永遠**不會因時間流逝而轉 completed。）
    """
    subject = _subject()
    base = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    thread = _thread("active", base, base + timedelta(days=13))
    moment = base + timedelta(days=14, seconds=1)

    evaluation = subject.evaluate(thread, moment, 14, 7, False)
    assert evaluation.target_status.value == "active"
    assert evaluation.reason.value == "time_horizon_exceeded"
    assert evaluation.should_mutate is False, "advisory 不得變更任何狀態"
    assert evaluation.extra_metadata["advisory"] is True
    assert evaluation.reflection_prompt is None


def test_m2_terminal_thread_is_real_dissolution():
    """終態線頭（`completed` ＋ 未溶解）⇒ 真溶解（`should_mutate is True`）。"""
    subject = _subject()
    base = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    moment = base + timedelta(days=14, seconds=1)
    thread = _thread("completed", base, base + timedelta(days=13))

    evaluation = subject.evaluate(
        thread, moment, 14, 7, False, terminal_reason="time_horizon_exceeded"
    )
    assert evaluation.should_mutate is True
    assert evaluation.target_status.value == "completed"
    assert evaluation.reason.value == "time_horizon_exceeded"
    assert evaluation.extra_metadata["advisory"] is False


# ─────────────────────────────────────────────────────────────────────────────
# B. 接縫的 at-most-once（第 36 筆）
# ─────────────────────────────────────────────────────────────────────────────
def _run_simulation(subject: SimpleNamespace, box: Path, *, seed_registry: bool = True) -> Dict[str, Any]:
    return asyncio.run(
        subject.cli.run_simulation(
            sandbox=box,
            agent_id=AGENT_ID,
            slot="night",
            seed_registry=seed_registry,
            seam_module=subject.seam_module,
        )
    )


def test_sandbox_run_emits_exactly_one_new_event(sandbox):
    """🔴 變異 M2 的守衛：35 筆歷史線頭零重發，本次只新增 1 個（第 36 筆）。"""
    subject = _subject()
    before = subject.cli.production_sha_snapshot()
    before_sizes = subject.cli.production_sizes()

    report = _run_simulation(subject, sandbox)

    registry = report["registry"]
    assert registry["seeded"] >= 35, "已發射過的歷史線頭應有 35 條以上：%s" % registry["seeded"]
    assert report["seam_pre"]["total_events"] == 0, "歷史線頭不得重發"
    assert report["seam_post"]["total_events"] == 1, "本次只應新增 1 個事件"
    assert report["seam_again"]["total_events"] == 0, "同一行程不得重發"

    target_id = report["target"]["thread_id"]
    events = report["seam_post"]["events"]
    assert len(events) == 1
    assert events[0]["thread_id"] == target_id
    assert events[0]["narrative"].strip(), "新事件必須帶正文"
    assert events[0]["reflection"].strip(), "新事件必須帶感悟"

    # 溶解執行層：1 次腳本化 LLM 呼叫、0 次真實 LLM 呼叫。
    assert report["consolidation"]["status"] == "consolidated"
    assert report["consolidation"]["llm_calls"] == 1
    assert report["llm_calls"]["real_llm_calls"] == 0
    assert report["llm_calls"]["m5_scripted"] == 1

    # 生產資料逐位元未動。
    # 🔴 精確口徑（2026-10-21 修正）：線上伺服器會在**真實對話投影**時並行 append
    #    `elevation/elevation_projection_trace.jsonl`；那是伺服器的合法寫入，
    #    **不是**沙盒汙染（2026-10-21 實測：窗內 15 筆 emergent_projected 來自線上
    #    agent_yua/agent_ruka 的真實對話，帶 sim 標記者 0 筆）。
    #    ⇒ ① 沙盒**不可能寫入**的檔案：位元級必須完全相同（強保護）；
    #       ② 併發檔：以「新增位元組有無沙盒產物標記」判定；縮短／消失一律違規。
    after = subject.cli.production_sha_snapshot()
    strict_before = {k: v for k, v in before.items()
                     if k not in subject.cli.SERVER_CONCURRENT_FILES}
    strict_after = {k: v for k, v in after.items()
                    if k not in subject.cli.SERVER_CONCURRENT_FILES}
    assert strict_before == strict_after, "沙盒執行不得寫入生產資料"
    violations = subject.cli.production_delta_has_sandbox_artifacts(before_sizes)
    assert not violations, "沙盒執行不得寫入生產資料：%s" % violations


def test_seam_without_seeding_emits_all_36(sandbox):
    """不預先播種（模擬全新行程）⇒ 接縫會發射全部 36 筆（35 歷史 ＋ 1 新）。"""
    subject = _subject()
    report = _run_simulation(subject, sandbox, seed_registry=False)

    assert report["seam_pre"]["total_events"] >= 35, "未播種時歷史線頭應全部發射"
    assert report["seam_post"]["total_events"] == 1, "新線頭另外發射 1 筆"
    assert report["seam_again"]["total_events"] == 0, "發射過後不得重發"

    emitted = {e["thread_id"] for e in report["seam_pre"]["events"]}
    emitted |= {e["thread_id"] for e in report["seam_post"]["events"]}
    assert len(emitted) == report["seam_pre"]["total_events"] + 1
    assert report["target"]["thread_id"] in emitted


def test_virtual_now_is_created_at_plus_14_days_plus_one_second(sandbox):
    """虛擬時點 ＝ `created_at + 14 天 + 1 秒`（存活軸嚴格大於 ⇒ 必須 +1 秒）。"""
    subject = _subject()
    report = _run_simulation(subject, sandbox)
    target = report["target"]
    created = datetime.fromisoformat(target["created_at"])
    virtual_now = datetime.fromisoformat(target["virtual_now"])
    assert virtual_now - created == timedelta(days=14, seconds=1)
    assert target["age_seconds"] == 14 * 86400 + 1


# ─────────────────────────────────────────────────────────────────────────────
# C. 注入缺席與形狀不符 ⇒ fail-closed
# ─────────────────────────────────────────────────────────────────────────────
def _terminal_evaluation() -> Any:
    from src.soul import life_thread_dissolution as lt_diss

    base = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    thread = _thread("completed", base, base + timedelta(days=13))
    return thread, lt_diss.evaluate_thread_dissolution(
        thread, base + timedelta(days=14, seconds=1), 14, 7, False
    )


def _unlimited_budget() -> Any:
    """注入「不限額」的預算（工單 §3.1 步驟 7）。

    🔴 執行層的預設預算是**行程級**計數器（per-agent 3／全域 200）⇒ 同一個
    pytest 行程裡前面的案例會把它用完 ⇒ 直接測執行層時必須注入自己的預算。
    """
    return _exec_module().ConsolidationBudget(per_agent_limit=100000, global_limit=100000)


def test_exec_layer_skips_when_llm_absent():
    """🔴 變異 M4 的守衛：`llm_call is None` ⇒ `skipped_no_llm`、0 呼叫、0 寫入。"""
    subject = _subject()
    thread, evaluation = _terminal_evaluation()
    written: List[Any] = []

    result = asyncio.run(
        subject.consolidate(
            agent_id=AGENT_ID,
            thread=thread,
            evaluation=evaluation,
            llm_call=None,
            fact_writer=lambda fact: written.append(fact) or "fact-id",
            budget=_unlimited_budget(),
        )
    )
    assert result.status == "skipped_no_llm"
    assert result.llm_calls == 0
    assert written == [], "依賴缺席時不得寫入事實"


def test_exec_layer_rejects_malformed_llm_shape():
    """🔴 變異 M3 的守衛：LLM 回傳形狀不符 ⇒ `parse_failed`、**不得寫入事實**。"""
    subject = _subject()
    thread, evaluation = _terminal_evaluation()
    written: List[Any] = []

    def _malformed_llm(prompt: str, max_tokens: int = 0) -> str:
        # `dissolution` 必須是非空字串；給一個非字串 ⇒ 形狀不符。
        return '{"dissolution": 12345, "meaning_kind": "relief"}'

    result = asyncio.run(
        subject.consolidate(
            agent_id=AGENT_ID,
            thread=thread,
            evaluation=evaluation,
            llm_call=_malformed_llm,
            fact_writer=lambda fact: written.append(fact) or "fact-id",
            budget=_unlimited_budget(),
        )
    )
    assert result.status == "parse_failed", "形狀不符必須 parse_failed（實際 %s）" % result.status
    assert result.llm_calls == 1
    assert written == [], "形狀不符時不得寫入事實"


# ─────────────────────────────────────────────────────────────────────────────
# D. 沙盒隔離
# ─────────────────────────────────────────────────────────────────────────────
def test_child_refuses_when_env_drifts(tmp_path):
    """🔴 變異 M5 的守衛：`SOUL_OS_DATA_DIR` 與沙盒不一致 ⇒ 子行程必須拒絕執行。"""
    subject = _subject()
    expected = tmp_path / "sandbox"
    expected.mkdir(parents=True, exist_ok=True)
    drifted = tmp_path / "somewhere_else"
    drifted.mkdir(parents=True, exist_ok=True)

    with pytest.raises(Exception) as excinfo:
        subject.cli.assert_sandbox_isolated(expected, str(drifted))
    assert type(excinfo.value).__name__ == "SandboxIsolationError"

    # env 與沙盒一致時必須放行。
    assert subject.cli.assert_sandbox_isolated(expected, str(expected)) == expected.resolve()


def test_scripted_llm_rejects_unexpected_prompt():
    """腳本化 LLM 必須 100% 覆蓋：非預期 prompt 一律 raise（杜絕真實推理與花費）。"""
    subject = _subject()
    proxy = subject.cli.ScriptedM5Proxy()
    with pytest.raises(RuntimeError):
        asyncio.run(proxy.generate_text(messages=[{"role": "user", "content": "你好"}], agent_id="x"))

    ok = asyncio.run(
        proxy.generate_text(
            messages=[
                {
                    "role": "user",
                    "content": '請只回 JSON：{"dissolution": "…", "meaning_kind": "relief"}',
                }
            ],
            agent_id="x",
        )
    )
    assert "dissolution" in ok


def test_sandbox_copies_are_independent_inodes(sandbox):
    """🔴 沙盒副本必須是**獨立 inode**的實體複製（SIM-ACCEL-RECALL-30D 事故根因）。

    該事故：變異把沙盒複製改成**硬連結** ⇒ 沙盒與生產**共用 inode** ⇒
    CLI 的 append 直接寫進生產檔（805 筆假事件 ＋ 513 顆節點狀態被改）。

    ⇒ 本檔的沙盒一律 `shutil.copy2`（實體複製）；此測試把該性質釘死：
      1. 沙盒檔的 `st_ino` ≠ 生產檔的 `st_ino`（兩者皆可用時）；
      2. 生產檔與沙盒檔的 `st_nlink` 都是 1（**沒有第二個硬連結**）。
    """
    import os

    from src.soul import life_thread_orchestrator as orch

    production = Path(orch.__file__).resolve().parents[2] / "data"
    pairs = [
        (
            production / "soul" / AGENT_ID / "life_threads.jsonl",
            sandbox / "soul" / AGENT_ID / "life_threads.jsonl",
        ),
        (
            production / "memory" / AGENT_ID / "graph.sqlite",
            sandbox / "memory" / AGENT_ID / "graph.sqlite",
        ),
    ]
    for prod, sbx in pairs:
        if not prod.exists() or not sbx.exists():
            continue
        prod_stat = os.stat(prod)
        sbx_stat = os.stat(sbx)
        if prod_stat.st_ino and sbx_stat.st_ino:
            assert prod_stat.st_ino != sbx_stat.st_ino, (
                "沙盒副本與生產檔共用 inode（=%s）⇒ append 會落在生產上"
                % prod_stat.st_ino
            )
        assert prod_stat.st_nlink == 1, (
            "生產檔有 %d 個硬連結 ⇒ 有別處以硬連結共用它（%s）"
            % (prod_stat.st_nlink, prod)
        )
        assert sbx_stat.st_nlink == 1, (
            "沙盒副本是硬連結（nlink=%d）⇒ 必須改成實體複製（%s）"
            % (sbx_stat.st_nlink, sbx)
        )


def test_cli_has_no_forbidden_imports():
    """CLI 不得 import 任何網路／程序建立 API（`os.spawnv` 換行程是唯一例外且已記錄）。"""
    source = CLI_PATH.read_text(encoding="utf-8")
    for banned in ("subprocess", "requests", "httpx", "socket", "uvicorn", "fastapi"):
        assert ("import %s" % banned) not in source, "CLI 不得 import %s" % banned
