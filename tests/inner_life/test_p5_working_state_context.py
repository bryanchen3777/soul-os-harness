# tests/inner_life/test_p5_working_state_context.py
"""CAMPAIGN-B Phase 5（P5-①）張力思考狀態 唯讀上下文接線 — 守衛 P6-T1～P6-T15。

工單：``logs/CAMPAIGN-B-P5-THINKING-CONTEXT-WIRING-WORK-ORDER.md``（v2）。

本票只做**讀取面接線**：把「角色尚未想通的事」以唯讀方式注入
``src/llm/proxy.py`` 的 Inner Life 提示詞組裝（group ＋ private 兩條路徑）。

🔴 **本票未接資料面**：``data/inner_life/working_state.jsonl`` 在生產上仍不存在
（working_state 由 P5-② 負責產生）⇒ 「角色已能感知思考狀態」**尚不成立**。
本檔守的是「**接線面**正確且無資料時 prompt 逐位元不變」，不是「資料面已通」。

🔺 **本檔恰有 15 個測試函式**（P6-T1～P6-T15 各一）；同一守衛項的多個斷言
合併在同一函式內，**不拆成多支** ⇒ 與工單「15 筆 ＋ 既有 70 筆 ＝ 85 筆」的
驗收口徑一致。

邊界（工單 §2.5 / §5）：
  * 0 subprocess / 0 os.spawn* / 0 os.system / 0 os.popen（R5 護欄；
    ``tests/infra/test_no_production_spawn_guard.py`` 的白名單大小鎖死 22，本票不得擴張）
  * 0 啟動伺服器 / 0 綁 :8000 / :8765-8767 / 0 對生產發真實請求
  * 0 真實 LLM 呼叫 / 0 外部 API / 0 網路
  * 0 寫入 ``data/**``（本檔只寫 pytest 的 tmp 目錄）
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

# ─────────────────────────────────────────────────────────────────────────
# P6-T12 golden：以**未實作**（proxy.py 尚未接線）時組出的 prompt 逐位元雜湊。
#
# 量得方式：``%TEMP%\p5_harness\gen_golden.py`` 在**改動 proxy.py 之前**執行，
# 以固定的 agent_id／soul／memory stub 組 group 與 private 兩條路徑，
# 對 ``json.dumps(messages, ensure_ascii=False, sort_keys=True)`` 取 sha256。
# 連跑 3 次（每次不同 tmp 資料根）結果一致 ⇒ 與 wall-clock 無關
# （proxy.py 的時間相依 helper 在 ts<=0 時一律回 None）。
# ─────────────────────────────────────────────────────────────────────────
GOLDEN_GROUP_SHA256 = "e13c004fd2a4b3acb919572c7e3d2fec68c79bcf18c2490d476e2cd8dd3a382d"
GOLDEN_GROUP_BYTES = 1663
GOLDEN_PRIVATE_SHA256 = "35e14ad57374242ef33ff5226537f6ed25ac8416872ce6fdcf86b1042621e0cf"
GOLDEN_PRIVATE_BYTES = 1174

#: 測試用的固定 agent / soul（必須與 golden 生成時逐位元相同）。
TEST_AGENT_ID = "agent_test"
TEST_SOUL = "SOUL-TEST-BODY"
TEST_MEMORY_CONTEXT = "MEMORY-CONTEXT-BODY"
TEST_WORLD_CONTEXT = "WORLD-CONTEXT-BODY"
TEST_CURRENT_INPUT = "hello"

#: P6-T10：11 個 frozen contract 檔的 sha256（永久紅線）。
#:
#: 🔺 **釘死內容**而不是「git diff 沒出現」——後者需要 subprocess（本票禁用），
#: 前者更嚴：任何**內容**被改動都會轉紅。🔺 但基準必須**免於行尾**（見下方實作）：repo 內行尾本身就是混的（有的檔 LF、有的檔 CRLF），同一個 blob 在不同 checkout 會落成不同位元組 ⇒ 釘死工作副本 sha 會假紅。
FROZEN_DIRS_SHA256: Dict[str, str] = {
    "src/agency/stages.py": "4f1560a3ec71473d039e425e6ab15d740265722a9424076e10b61e2fe6f66613",
    "src/agency/trigger_handler.py": "8350d7dd3b1075e29acce92565d2d83a270a3f3a196393064a1402ee4a36ae9b",
    "src/agency/event_handler.py": "fc4838b9a7662b6571244ff15bb39d75ffe7fb65a0b4bb6f1812c6cfaa1b35a7",
    "src/agency/dream_handler.py": "db8243c38f38c0f1dacde37402c252f1ffc05c226e79337b22ec178dd1cee595",
    "src/agency/diary_handler.py": "49a8ae701b1f850b6867a37ba81467c0ea8f90cf6542f5c19d8d1073c827bbdb",
    "src/inner_life/submission_gate.py": "13879fe212958b02ce00e876409602df8bcc79f4beafe7f51c420cc4943203c9",
    "src/social/identity_firewall.py": "b32da452db2d1d42543c5251bb11ff5997486919caf503fd3e595ab6c9e06856",
    "src/social/producer_gate.py": "569d5a235814e88cd6d22eb85d3f9a8e71cda89ac6ef585bbb66fafb50a50f6c",
    "src/world/middleware.py": "708adf216e63b7a87cf02628de5f67ff8d9aa0e9776cfdf25f6d1e0d560019ec",
    "src/memory/sage/writer.py": "01cd6c51b30bd23ece6638e19d38e04f760bc9b2c0b5b3ebf1c3d86a3f9d5f9f",
    "src/soul/decision.py": "508781d9f6be6065ca0f87d33bbc0cfb263bf117990cb7d83e5168a649a9c0f3",
}

#: P6-T11：既有 70 筆守衛檔（Phase 4 交付）的 sha256 與測試函式數。
#:
#: 🔺 這個 sha256 是**刻意的契約**：它證明「本票沒有削弱既有守衛檔」。
#: 日後若要擴充該檔，必須同步更新此處（與 repo 既有的
#: ``EXPECTED_SUBPROCESS_ALLOWLIST_SIZE`` 鎖法同款）。
PHASE4_GUARD_FILE = "tests/inner_life/test_tension_closure_loop.py"
PHASE4_GUARD_SHA256 = "21e768c12d59ea90222f6a65993f85ab88b88f1219efdceb56db6efe70b02540"
PHASE4_GUARD_TEST_FUNC_COUNT = 70

#: P6-T9：新增區段內**不得**出現的網路／LLM／寫檔呼叫。
#:
#: 🔺 刻意**不**列入 ``get``／``post``／``chat`` 之類泛用名稱：
#: ``_read_working_state_lines()`` 內有 ``record.get("decided")``
#: （``.get()`` 是字典取值，不是網路呼叫）⇒ 列入會讓守衛恆紅而失效。
#: 網路／LLM 的真實訊號由 **import 禁令**把關（``requests``／``httpx``／
#: ``urllib``／``socket``／``openai`` … 一律不得出現）。
BANNED_IMPORT_ROOTS = frozenset(
    {
        "requests", "httpx", "urllib", "urllib3", "aiohttp", "socket",
        "http", "http.client", "ftplib", "telnetlib", "smtplib",
        "openai", "anthropic", "google", "cohere", "mistralai",
        "transformers", "torch", "llm_call", "litellm",
    }
)
BANNED_CALL_NAMES = frozenset(
    {
        # 寫檔
        "write_text", "write_bytes", "open", "remove", "unlink", "rmdir",
        "rename", "replace", "mkdir", "touch",
        # 行程
        "system", "popen", "spawn", "spawnl", "spawnle", "spawnlp",
        "spawnv", "spawnve", "spawnvp", "spawnvpe", "run", "Popen", "call",
        "check_call", "check_output",
        # 網路
        "urlopen", "urlretrieve", "connect", "sendall", "sendto",
    }
)


# ─────────────────────────────────────────────────────────────────────────
# 共用 helper
# ─────────────────────────────────────────────────────────────────────────
class _StubMemory:
    """兩個 builder 只碰這兩個 method。

    群聊歷史放一則**非 Bry** 的發言（Bry 在線判定依賴 wall clock），
    讓 group 與 private 的 prompt 內容不同（golden 才有意義）。
    """

    def get_group_history(self, limit: int = 20):
        return [
            {
                "speaker": "agent_other",
                "content": "群聊歷史一則",
                "is_private": False,
            }
        ]

    def get_recent_with_meta(self, session_id: str, limit: int = 20):
        return []


def _assemble_messages() -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """以 golden 生成時**完全相同**的輸入組出 group / private 兩條路徑。"""
    from src.llm import proxy

    kwargs = dict(
        soul=TEST_SOUL,
        current_input=TEST_CURRENT_INPUT,
        memory_context=TEST_MEMORY_CONTEXT,
        memory=_StubMemory(),
        mood=0.0,
        user_id="bryan",
        current_time="",
        event_ts=None,
        bry_latest_ts=0,
        world_context=TEST_WORLD_CONTEXT,
        last_interaction_ts=0,
    )
    group = proxy._build_messages_group(TEST_AGENT_ID, **kwargs)
    private = proxy._build_messages_private(TEST_AGENT_ID, **kwargs)
    return group, private


def _digest(messages: List[Dict[str, str]]) -> Tuple[str, int]:
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), len(blob)


def _system_content(messages: List[Dict[str, str]]) -> str:
    return messages[0]["content"]


def _production_store_dir() -> Path:
    """生產 working_state 的承載目錄（**與介面內部推導的是同一條路徑**）。

    ``src.paths.data_root()`` 是行程期快取；pytest 的 autouse fixture
    （``tests/conftest.py``）會把 ``SOUL_OS_DATA_DIR`` 指向本次測試的 tmp 目錄
    並 ``reset_data_root()`` ⇒ 這裡拿到的是**隔離根**，不是生產 ``data/``。
    """
    from src.paths import data_root

    return Path(data_root()) / "inner_life"


def _working_state_row(
    *,
    agent_id: str = TEST_AGENT_ID,
    ts: str = "2026-10-24T00:00:00Z",
    decided: bool = False,
    remaining_gap_count: int = 3,
    partial_resolution: float = 0.25,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "agent_id": agent_id,
        "ts": ts,
        "resolved_tension_id": "tension_test_1",
        "drive_code": "drive_test",
        "drives_touched": [],
        "open_gap_count_after": remaining_gap_count,
        "open_contradiction_count_after": 0,
        "open_gap_count_before": remaining_gap_count + 1,
        "partial_resolution": partial_resolution,
        "remaining_gap_count": remaining_gap_count,
        "decided": decided,
    }
    if extra:
        row.update(extra)
    return row


def _write_working_state(
    rows: List[Dict[str, Any]], *, store_dir: Path | None = None
) -> Path:
    """把 working_state 記錄寫進**隔離的**生產路徑（tmp 資料根之下）。"""
    from src.inner_life.tension_closure_loop import WORKING_STATE_FILENAME

    store = store_dir if store_dir is not None else _production_store_dir()
    store.mkdir(parents=True, exist_ok=True)
    path = store / WORKING_STATE_FILENAME
    blob = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(blob, encoding="utf-8")
    return path


def _seed_diary(agent_id: str) -> None:
    """寫一則 diary（slot=dream / source=llm）讓 inner_life 區塊非空。"""
    from datetime import datetime

    from src.llm import proxy

    diary_dir = Path(proxy.INNER_LIFE_DATA_DIR) / agent_id / "diary"
    diary_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    entry = {"slot": "dream", "source": "llm", "content": "夢到一條很長的河。"}
    (diary_dir / f"{date_str}.jsonl").write_text(
        json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{path} 內找不到函式 {name}")


def _assert_pure_read(node: ast.FunctionDef) -> None:
    offenders: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.name.split(".")[0] in BANNED_IMPORT_ROOTS:
                    offenders.append(f"import {alias.name}")
        elif isinstance(sub, ast.ImportFrom):
            if (sub.module or "").split(".")[0] in BANNED_IMPORT_ROOTS:
                offenders.append(f"from {sub.module} import ...")
        elif isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALL_NAMES:
                offenders.append(f"call {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in BANNED_CALL_NAMES:
                offenders.append(f"call .{func.attr}()")
    assert not offenders, (
        f"{node.name} 內不得出現網路／LLM／寫檔呼叫，實得 {offenders}"
    )


@pytest.fixture(autouse=True)
def _isolate_inner_life_dir(tmp_path):
    """把 ``proxy.INNER_LIFE_DATA_DIR`` 指向本次測試的空目錄。

    ``proxy.py`` 在**匯入期**就把 ``data_root()`` 解析成常數
    （``INNER_LIFE_DATA_DIR = str(data_root() / "soul")``）；該常數會殘留
    「第一個匯入者」的資料根 ⇒ 必須在測試內重新指向，否則 golden 雜湊會被
    「上一支測試留下的 diary」污染。

    用**自建** ``pytest.MonkeyPatch()``（而非 ``monkeypatch`` fixture），
    與 ``tests/conftest.py`` 的 autouse fixture 同款做法：不佔用
    ``monkeypatch`` fixture ⇒ 不改變既有 fixture 的 setup/teardown 順序。
    """
    from src.llm import proxy

    empty = tmp_path / "soul_empty"
    empty.mkdir(parents=True, exist_ok=True)
    mp = pytest.MonkeyPatch()
    mp.setattr(proxy, "INNER_LIFE_DATA_DIR", str(empty))
    try:
        yield
    finally:
        mp.undo()


# ─────────────────────────────────────────────────────────────────────────
# P6-T1：無 working_state 檔 ⇒ block 回 ""；prompt 與未實作前逐位元相同
# ─────────────────────────────────────────────────────────────────────────
class TestP6T1NoDataYieldsEmptyBlock:
    """P6-T1 — 沒有 ``working_state.jsonl`` ⇒ 區塊空字串，prompt 逐位元不變。"""

    def test_p6_t1_no_data_yields_empty_block_and_identical_prompt(self):
        from src.llm import proxy

        store = _production_store_dir()
        assert not (store / "working_state.jsonl").exists(), (
            "前置條件：本測試必須在沒有 working_state 檔的狀態下執行"
        )
        assert proxy._format_working_state_block(TEST_AGENT_ID) == ""

        group, private = _assemble_messages()
        assert _digest(group) == (GOLDEN_GROUP_SHA256, GOLDEN_GROUP_BYTES)
        assert _digest(private) == (GOLDEN_PRIVATE_SHA256, GOLDEN_PRIVATE_BYTES)
        assert "[思考狀態]" not in _system_content(group)
        assert "[思考狀態]" not in _system_content(private)


# ─────────────────────────────────────────────────────────────────────────
# P6-T2：有未消解張力 ⇒ block 含該行且逐位元正確
# ─────────────────────────────────────────────────────────────────────────
class TestP6T2UnresolvedTensionLineIsExact:
    """P6-T2 — 有未消解張力 ⇒ block 含
    ``- [YYYY-MM-DD 張力未解] 還有 N 個未解的問題（已想過 X%）`` 逐位元正確。"""

    def test_p6_t2_block_contains_the_exact_unresolved_line(self):
        from src.llm import proxy

        _write_working_state(
            [
                _working_state_row(
                    decided=False, remaining_gap_count=3, partial_resolution=0.25
                )
            ]
        )
        block = proxy._format_working_state_block(TEST_AGENT_ID)
        expected = "- [2026-10-24 張力未解] 還有 3 個未解的問題（已想過 25%）"
        assert expected in block, f"block={block!r} 未逐位元包含預期行"
        assert "[思考狀態]" in block
        assert "以下是你最近尚未想通的事。" in block

        # 已想完（decided=True）⇒ 無未解張力 ⇒ 沒有區塊。
        _write_working_state([_working_state_row(decided=True, remaining_gap_count=0)])
        assert proxy._format_working_state_block(TEST_AGENT_ID) == ""


# ─────────────────────────────────────────────────────────────────────────
# P6-T3：介面拋例外 ⇒ block＝""、不拋出、prompt 逐位元不變
# ─────────────────────────────────────────────────────────────────────────
class TestP6T3InterfaceExceptionIsIsolated:
    """P6-T3 — ``format_working_state_lines_production`` 拋例外 ⇒ 雙重失敗隔離。"""

    def test_p6_t3_exception_yields_empty_block_and_identical_prompt(self, monkeypatch):
        from src.llm import proxy
        from src.inner_life import tension_closure_loop as tcl

        def _boom(*_args, **_kwargs):
            raise RuntimeError("P6-T3 模擬介面例外")

        monkeypatch.setattr(tcl, "format_working_state_lines_production", _boom)
        # 不得拋出。
        assert proxy._format_working_state_block(TEST_AGENT_ID) == ""

        group, private = _assemble_messages()
        assert _digest(group) == (GOLDEN_GROUP_SHA256, GOLDEN_GROUP_BYTES)
        assert _digest(private) == (GOLDEN_PRIVATE_SHA256, GOLDEN_PRIVATE_BYTES)


# ─────────────────────────────────────────────────────────────────────────
# P6-T4：import 失敗 ⇒ block＝""、prompt 逐位元不變
# ─────────────────────────────────────────────────────────────────────────
class TestP6T4ImportFailureIsIsolated:
    """P6-T4 — 模擬 import 失敗（``sys.modules[name] = None`` ⇒ ImportError）。"""

    def test_p6_t4_import_failure_yields_empty_block_and_identical_prompt(
        self, monkeypatch
    ):
        from src.llm import proxy

        monkeypatch.setitem(sys.modules, "src.inner_life.tension_closure_loop", None)
        assert proxy._format_working_state_block(TEST_AGENT_ID) == ""

        group, private = _assemble_messages()
        assert _digest(group) == (GOLDEN_GROUP_SHA256, GOLDEN_GROUP_BYTES)
        assert _digest(private) == (GOLDEN_PRIVATE_SHA256, GOLDEN_PRIVATE_BYTES)


# ─────────────────────────────────────────────────────────────────────────
# P6-T5：有界（≤3 行、每行 ≤60 字元、總字元 ≤200）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T5BlockIsBounded:
    """P6-T5 — 餵超量資料 ⇒ 區塊必須被有界化。"""

    def test_p6_t5_block_respects_line_char_and_total_bounds(self, monkeypatch):
        from src.llm import proxy
        from src.inner_life import tension_closure_loop as tcl

        huge = [("超" * 200) for _ in range(10)]
        monkeypatch.setattr(
            tcl, "format_working_state_lines_production", lambda *_a, **_k: huge
        )
        block = proxy._format_working_state_block(TEST_AGENT_ID)
        assert block, "有資料時必須有區塊"

        parts = block.split("\n")
        assert parts[0] == "", f"區塊應以換行開頭：{block!r}"
        assert parts[1] == "[思考狀態] 以下是你最近尚未想通的事。", (
            f"區塊模板標題不符：{parts[1]!r}"
        )
        data_lines = [p for p in parts[2:] if p != ""]
        assert 1 <= len(data_lines) <= 3, f"資料行數 {len(data_lines)} 必須介於 1 與 3"
        for line in data_lines:
            assert len(line) <= 60, f"單行長度 {len(line)} 不得大於 60"
        assert len(block) <= 200, f"區塊總長 {len(block)} 不得大於 200"


# ─────────────────────────────────────────────────────────────────────────
# P6-T6：0 洩漏（路徑／完整 32 字元 UUID）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T6NoPathOrUuidLeak:
    """P6-T6 — 區塊不得含檔案路徑或完整 32 字元 UUID。"""

    def test_p6_t6_block_leaks_no_path_and_no_full_uuid(self):
        from src.llm import proxy

        uuid32 = "0123456789abcdef0123456789abcdef"
        _write_working_state(
            [
                _working_state_row(
                    decided=False,
                    remaining_gap_count=3,
                    extra={
                        "thread_id": uuid32,
                        "trace_path": (
                            "C:\\Users\\bryan\\soul-os-harness\\data\\inner_life\\x.jsonl"
                        ),
                    },
                )
            ]
        )
        block = proxy._format_working_state_block(TEST_AGENT_ID)
        assert block, "有資料時必須有區塊（否則本守衛會空轉）"
        assert "C:\\" not in block, f"區塊洩漏了 Windows 路徑：{block!r}"
        assert "data\\" not in block, f"區塊洩漏了 data/ 路徑片段：{block!r}"
        assert uuid32 not in block, f"區塊洩漏了完整 32 字元 UUID：{block!r}"
        assert str(_production_store_dir()) not in block, "區塊洩漏了資料根路徑"
        assert not re.search(r"\b[0-9a-f]{32}\b", block), (
            f"區塊含疑似 32 字元 UUID：{block!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# P6-T7：ordering（[最近內在生活] 之後、world_context 之前）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T7InjectionOrdering:
    """P6-T7 — ``[思考狀態]`` 必須排在 ``[最近內在生活]`` 之後、world_context 之前。"""

    def test_p6_t7_working_state_sits_between_inner_life_and_world_context(self):
        from src.llm import proxy

        _seed_diary(TEST_AGENT_ID)
        _write_working_state([_working_state_row(decided=False)])

        group, private = _assemble_messages()
        for messages in (group, private):
            content = _system_content(messages)
            assert "[最近內在生活]" in content, "前置條件：inner_life 區塊必須非空"
            assert "[思考狀態]" in content, "前置條件：思考狀態區塊必須非空"
            assert content.index("[最近內在生活]") < content.index("[思考狀態]")
            assert content.index("[思考狀態]") < content.index(TEST_WORLD_CONTEXT)


# ─────────────────────────────────────────────────────────────────────────
# P6-T8：group 與 private 兩條路徑皆注入；且只有這兩條
# ─────────────────────────────────────────────────────────────────────────
class TestP6T8OnlyTwoAssemblyPathsAreWired:
    """P6-T8 — 兩條路徑皆注入；proxy.py 內**只有**這兩個函式引用該 helper。"""

    def test_p6_t8_both_paths_inject_and_only_two_functions_are_wired(self):
        source = (REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        callers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(sub, ast.Name) and sub.id == "_format_working_state_block"
                for sub in ast.walk(node)
            )
        }
        assert callers == {"_build_messages_group", "_build_messages_private"}, (
            f"引用 _format_working_state_block 的函式應恰為兩條路徑，實得 {callers}"
        )

        builders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "system_parts"
                    for t in sub.targets
                ):
                    builders.append(node.name)
                    break
        assert sorted(builders) == ["_build_messages_group", "_build_messages_private"], (
            f"system_parts 組裝點應恰為兩處，實得 {sorted(builders)}"
        )

        _write_working_state([_working_state_row(decided=False)])
        group, private = _assemble_messages()
        assert "[思考狀態]" in _system_content(group)
        assert "[思考狀態]" in _system_content(private)


# ─────────────────────────────────────────────────────────────────────────
# P6-T9：AST 守衛（新增區段內不得有 HTTP／LLM／網路／寫檔呼叫）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T9NoNetworkOrWriteInNewSections:
    """P6-T9 — ``proxy.py`` 與 ``tension_closure_loop.py`` 的新增區段是純讀。"""

    def test_p6_t9_new_sections_contain_no_network_llm_or_write_calls(self):
        proxy_src = REPO_ROOT / "src" / "llm" / "proxy.py"
        tcl_src = REPO_ROOT / "src" / "inner_life" / "tension_closure_loop.py"
        targets = (
            (proxy_src, "_format_working_state_block"),
            (tcl_src, "format_working_state_lines_production"),
            (tcl_src, "_read_working_state_lines"),
        )
        for path, name in targets:
            _assert_pure_read(_function_node(path, name))


# ─────────────────────────────────────────────────────────────────────────
# P6-T10：0 frozen contract 變更
# ─────────────────────────────────────────────────────────────────────────
class TestP6T10FrozenContractsUnchanged:
    """P6-T10 — 11 個 frozen contract 檔**內容**未變（以**換行正規化**後的文字為基準；不用工作副本原始位元組，否則換 checkout 會假紅）。"""

    def test_p6_t10_frozen_contract_files_are_byte_identical(self):
        assert len(FROZEN_DIRS_SHA256) == 11, "frozen 檔必須恰為 11 個"
        assert sorted(FROZEN_DIRS_SHA256) == [
            "src/agency/diary_handler.py",
            "src/agency/dream_handler.py",
            "src/agency/event_handler.py",
            "src/agency/stages.py",
            "src/agency/trigger_handler.py",
            "src/inner_life/submission_gate.py",
            "src/memory/sage/writer.py",
            "src/social/identity_firewall.py",
            "src/social/producer_gate.py",
            "src/soul/decision.py",
            "src/world/middleware.py",
        ]
        for rel, expected in FROZEN_DIRS_SHA256.items():
            path = REPO_ROOT / rel
            assert path.is_file(), f"frozen 檔不存在：{rel}"

            # EOL-NORMALIZED HASH: hash the newline-normalized text, not raw working-copy bytes.
            #   The repo mixes LF and CRLF files, so the same blob lands as different bytes
            #   in different checkouts => pinning raw sha256 would false-red across checkouts.
            actual = hashlib.sha256(
                path.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
            assert actual == expected, f"frozen contract 檔被改動：{rel}"


# ─────────────────────────────────────────────────────────────────────────
# P6-T11：既有 70 筆守衛未被本票削弱
# ─────────────────────────────────────────────────────────────────────────
class TestP6T11Phase4GuardsNotWeakened:
    """P6-T11 — 既有守衛檔的內容被**釘死**，且測試函式數仍為 70。

    🔴 **不得**用 subprocess 跑另一個 pytest 來實作（``tests/**`` 禁用
    subprocess／os.spawn*／os.system／os.popen；R5 護欄白名單大小鎖死 22）。
    「70 筆實際全綠」是**驗收層級**的事實，由兩個檔合併執行（85 passed）證成。

    🔺 2026-10-24 主大腦修正（合入 P5-① 時）：本票**確實**改動了該檔 —— 修掉
    P5-T12 的**競態假紅**（原斷言快照整棵 ``data/`` 樹位元組，會與**活著的線上服務**
    爭 ``data/state/event_loop_alive.json`` 心跳檔而假紅）⇒ 那是**強化**（斷言改成
    比對介面實際可能寫入的面），不是削弱。⇒ 釘死值為**修正後**內容的**換行正規化**
    雜湊（不用工作副本原始位元組，否則換 checkout 會假紅）。
    """

    def test_p6_t11_phase4_guard_file_is_unchanged_and_still_has_70_tests(self):
        path = REPO_ROOT / PHASE4_GUARD_FILE

        # EOL-normalized (same reason as P6-T10): raw working-copy bytes differ
        # between checkouts => pinning them would false-red.
        actual = hashlib.sha256(
            path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        assert actual == PHASE4_GUARD_SHA256, (
            f"{PHASE4_GUARD_FILE} 被本票改動 ⇒ 既有 70 筆守衛已被削弱"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        assert len(funcs) == PHASE4_GUARD_TEST_FUNC_COUNT, (
            f"既有守衛檔的測試函式數為 {len(funcs)}，應為 "
            f"{PHASE4_GUARD_TEST_FUNC_COUNT}"
        )


# ─────────────────────────────────────────────────────────────────────────
# P6-T12：生產等價（byte-identity）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T12ProductionEquivalence:
    """P6-T12 — 生產現況（無 working_state 檔）組出的 prompt 與「未實作」逐位元相同。"""

    def test_p6_t12_prompts_match_pre_implementation_golden(self):
        group, private = _assemble_messages()
        assert _digest(group) == (GOLDEN_GROUP_SHA256, GOLDEN_GROUP_BYTES)
        assert _digest(private) == (GOLDEN_PRIVATE_SHA256, GOLDEN_PRIVATE_BYTES)

        # 🔴 本票**未接資料面**：生產 working_state 仍不存在。
        path = REPO_ROOT / "data" / "inner_life" / "working_state.jsonl"
        assert not path.exists(), (
            "P5-② 產生 working_state 之後，這條斷言需要改寫（不是本票的失敗）"
        )


# ─────────────────────────────────────────────────────────────────────────
# P6-T13：生產唯讀介面（檔案不存在 ⇒ []、不 raise、不建立檔案）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T13ProductionInterfaceMissingFile:
    """P6-T13 — 檔案不存在 ⇒ 回 ``[]``、不 raise、**不建立檔案**。"""

    def test_p6_t13_returns_empty_list_without_raising_or_creating_files(self):
        from src.inner_life.tension_closure_loop import (
            format_working_state_lines_production,
        )

        store = _production_store_dir()
        assert not (store / "working_state.jsonl").exists()
        assert format_working_state_lines_production(TEST_AGENT_ID) == []

        before = sorted(p.name for p in store.glob("*")) if store.is_dir() else []
        format_working_state_lines_production(TEST_AGENT_ID)
        after = sorted(p.name for p in store.glob("*")) if store.is_dir() else []
        assert after == before, "唯讀介面不得建立任何檔案"


# ─────────────────────────────────────────────────────────────────────────
# P6-T14：生產唯讀介面只讀（位元不變）
# ─────────────────────────────────────────────────────────────────────────
class TestP6T14ProductionInterfaceIsReadOnly:
    """P6-T14 — 呼叫前後 ``working_state.jsonl``／``epistemic_tensions.jsonl`` 位元不變。"""

    def test_p6_t14_files_are_byte_identical_after_the_call(self):
        from src.inner_life.tension_closure_loop import (
            TENSION_STORE_FILENAME,
            WORKING_STATE_FILENAME,
            format_working_state_lines_production,
        )

        store = _production_store_dir()
        store.mkdir(parents=True, exist_ok=True)
        (store / WORKING_STATE_FILENAME).write_text(
            json.dumps(_working_state_row(decided=False), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (store / TENSION_STORE_FILENAME).write_text(
            '{"kind": "tension_resolved"}\n', encoding="utf-8"
        )
        names = sorted(p.name for p in store.iterdir() if p.is_file())
        before = {name: (store / name).read_bytes() for name in names}

        lines = format_working_state_lines_production(TEST_AGENT_ID)
        assert len(lines) == 1, f"應讀出一行，實得 {lines}"

        after_names = sorted(p.name for p in store.iterdir() if p.is_file())
        after = {name: (store / name).read_bytes() for name in after_names}
        assert after == before, "唯讀介面不得改動任何位元組"
        assert after_names == names, "唯讀介面不得建立新檔案"


# ─────────────────────────────────────────────────────────────────────────
# P6-T15：路徑不可注入 ＋ 沙盒守門仍在
# ─────────────────────────────────────────────────────────────────────────
class TestP6T15PathNotInjectableAndSandboxGateIntact:
    """P6-T15 — ① 簽章不含 ``store_dir``／``path``；② 沙盒守門未被放寬。"""

    def test_p6_t15_signature_is_path_free_and_sandbox_gate_still_refuses(self):
        from src.inner_life.tension_closure_loop import (
            format_working_state_lines,
            format_working_state_lines_production,
        )

        params = inspect.signature(format_working_state_lines_production).parameters
        assert "store_dir" not in params, f"簽章不得含 store_dir：{list(params)}"
        assert "path" not in params, f"簽章不得含 path：{list(params)}"
        assert list(params) == ["agent_id", "max_entries", "max_chars"], (
            f"生產唯讀介面的簽章應恰為這三個參數，實得 {list(params)}"
        )

        # 沙盒介面對「生產路徑」仍回 ``[]``——即使該路徑**確實有資料**。
        store = _production_store_dir()
        _write_working_state(
            [_working_state_row(decided=False, remaining_gap_count=3)],
            store_dir=store,
        )
        assert (store / "working_state.jsonl").exists(), "前置條件：該路徑必須有資料"
        assert format_working_state_lines(TEST_AGENT_ID, store) == [], (
            "沙盒守門被放寬了：format_working_state_lines 竟讀得到生產路徑"
        )

        # 真實 repo ``data/inner_life`` 亦必須被守門擋下（唯讀，不寫）。
        repo_store = REPO_ROOT / "data" / "inner_life"
        assert format_working_state_lines(TEST_AGENT_ID, repo_store) == []
