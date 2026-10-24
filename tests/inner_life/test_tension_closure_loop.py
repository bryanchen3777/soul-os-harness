# tests/inner_life/test_tension_closure_loop.py
"""CAMPAIGN-B-P1 §4.1 T7/T8/T9/T10 — 短期思考閉環（沙盒原型 ＋ 隔離 ＋ 開關）。

工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md
CAMPAIGN-B-P2 §3.3 P2-T1～P2-T16 — 張力驅動思考（沙盒；**不燒 LLM**）。

工單：logs/CAMPAIGN-B-P2-D1-THINKING-LOOP-SANDBOX-WORK-ORDER.md

覆蓋：
  - **T7** 閉環三段可觀測（沙盒 ``working_state.jsonl`` 恰好 +1 筆且欄位齊全；
    沙盒 ``epistemic_tensions.jsonl`` 恰好 +1 筆，且**頂層欄位形狀與生產一致**）。
  - **T8** 沙盒隔離（閉環執行前後**生產** ``data/inner_life/epistemic_tensions.jsonl``
    的 bytes 恆等）。
  - **T9** 開關預設 OFF（``feature_enabled()`` 預設 ``False``；OFF 呼叫
    ``drive_loop`` ⇒ 不建立任何檔案、回 ``None``）。
  - **T10** 拒絕生產 ``store_dir``（⇒ 拒絕執行、回 ``None``、且生產檔案 bytes 恆等）。
  - **P2-T1～P2-T16** Phase 2 守衛（提示詞決定性／不膨脹／無副作用；Mock 的
    部分消解語意；Null 對照組；事件分流；working state 新欄位；沙盒隔離；
    append-only；旗標預設 OFF；**AST 層無 LLM 路徑**）。

沙盒：線頭檔與關係檔寫進 ``SOUL_OS_DATA_DIR`` 指向的**隔離 tmp 資料根**
（``tests/conftest.py`` 的 autouse fixture）；輸出只寫 ``tmp_path`` 下的沙盒。

🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
🔴 本檔的「載入點覆寫」（變異實驗用）**只接受 %TEMP% 之下**的路徑。
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

# ── 變異 harness 的「載入點覆寫」（工單 §3.4／§9.3）────────────────────────
#:
#: 變異實驗**改副本**（不碰 repo 檔）：把 ``tension_thinking.py`` 與
#: ``tension_closure_loop.py`` 的**副本**放在 %TEMP% 底下，用本環境變數指向它
#: ⇒ 守衛測試改載副本、repo 檔全程唯讀。
#:
#: 🔴 **只接受 %TEMP% 之下的路徑**；其餘一律 ``RuntimeError`` 拒絕 ⇒ CI／生產
#: 永遠載入 repo 的真檔（環境變數不存在 ⇒ 走正常 import）。
P2_MODULE_OVERRIDE_ENV = "SOUL_OS_P2_MODULE_OVERRIDE_DIR"

#: 覆寫時要載入的模組（⇒ 副本檔名）。
_OVERRIDE_MODULES = (
    ("src.inner_life.tension_thinking", "tension_thinking.py"),
    ("src.inner_life.tension_closure_loop", "tension_closure_loop.py"),
)


def _resolve_override_dir() -> Optional[Path]:
    """解析覆寫目錄；**非 %TEMP% 之下 ⇒ RuntimeError 拒絕**（fail-closed）。"""
    raw = os.environ.get(P2_MODULE_OVERRIDE_ENV, "")
    if not raw.strip():
        return None
    candidate = Path(raw).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not (candidate == temp_root or temp_root in candidate.parents):
        raise RuntimeError(
            f"{P2_MODULE_OVERRIDE_ENV} 只允許 {temp_root} 之下的路徑，收到：{candidate}"
        )
    return candidate


def _install_overridden_modules(override_dir: Path) -> None:
    """把 %TEMP% 底下的模組**副本**載入成專案模組（變異實驗專用）。"""
    import src.inner_life as pkg

    for module_name, filename in _OVERRIDE_MODULES:
        path = override_dir / filename
        if not path.is_file():
            raise RuntimeError(f"覆寫目錄缺少 {filename}：{override_dir}")
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        setattr(pkg, module_name.rsplit(".", 1)[1], module)


_OVERRIDE_DIR = _resolve_override_dir()
if _OVERRIDE_DIR is not None:
    _install_overridden_modules(_OVERRIDE_DIR)

from src.inner_life import tension_closure_loop as tcl
from src.inner_life import tension_thinking as tt
from src.inner_life.epistemic_action import TENSION_EVENT_FIELDS
from src.paths import data_root

#: 生產張力承載（唯讀對帳用）。
PRODUCTION_TENSION_STORE = REPO_ROOT / "data" / "inner_life" / "epistemic_tensions.jsonl"

#: 沙盒假時鐘（epoch 秒）——固定值，0 真實等待。
FAKE_NOW = 1_700_000_000

#: 沙盒線頭：2 筆資訊缺口（其一同時帶矛盾欄位）。
SANDBOX_ROWS = [
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


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _seed_sandbox_data(
    agent_id: str = "agent_akane", rows: Optional[List[Dict[str, Any]]] = None
) -> None:
    """把線頭檔與關係檔寫進**隔離**資料根（沙盒；不碰生產）。

    ``rows`` 缺省＝:data:`SANDBOX_ROWS`（既有行為**不變**）；給定時改用指定的
    線頭列（Phase 2 用它控制 ``info_gap_count``：1 列 vs 2 列）。
    """
    source_rows = list(SANDBOX_ROWS if rows is None else rows)
    agent_dir = Path(data_root()) / "soul" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    rows_out = []
    for row in source_rows:
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


def _read_jsonl(path: Path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def _production_snapshot():
    """生產張力承載的 bytes 快照（不存在 ⇒ ``None``）。"""
    if not PRODUCTION_TENSION_STORE.is_file():
        return None
    return PRODUCTION_TENSION_STORE.read_bytes()


# ── Phase 2 輔助 ─────────────────────────────────────────────────────────
def _dir_snapshot(root: Path) -> Dict[str, str]:
    """目錄內容快照（相對路徑 → sha256）；目錄不存在 ⇒ 空 dict。"""
    snapshot: Dict[str, str] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return snapshot


def _production_files_snapshot() -> Dict[str, str]:
    """生產張力承載 ＋ ``data/soul/*/life_threads.jsonl`` 的 sha256 快照。"""
    snapshot: Dict[str, str] = {}
    if PRODUCTION_TENSION_STORE.is_file():
        rel = PRODUCTION_TENSION_STORE.relative_to(REPO_ROOT).as_posix()
        snapshot[rel] = hashlib.sha256(PRODUCTION_TENSION_STORE.read_bytes()).hexdigest()
    soul_dir = REPO_ROOT / "data" / "soul"
    if soul_dir.is_dir():
        for path in sorted(soul_dir.glob("*/life_threads.jsonl")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _make_request(
    *,
    agent_id: str = "agent_akane",
    drive_code: str = "D1",
    temporal_state: str = "牵挂",
    decay_bucket: str = "24h-7d",
    info_gap_count: int = 3,
    unresolved_count: int = 1,
    source_rows: Optional[List[Dict[str, Any]]] = None,
):
    """組一筆 :class:`ThinkRequest`（**合成 fixture 與生產同形**：欄位齊備）。

    預設的 ``source_rows`` 逐欄對齊生產 ``tension_scoring._source_row`` 的形狀
    （``factor``／``thread_id``／``status``／``updated_at``／``title``／
    ``sage_fact_id``／``contradiction``）—— 🔴 坑 #1：fixture 必須**同形**，
    否則守衛會「意外全綠」而變異失守。
    """
    rows: List[Dict[str, Any]] = [
        {
            "factor": "info_gap",
            "thread_id": f"gap-{idx + 1}",
            "status": "active",
            "updated_at": "2023-11-13T14:00:00+00:00",
            "title": f"缺口{idx + 1}",
            "sage_fact_id": None,
            "contradiction": None,
        }
        for idx in range(max(0, int(info_gap_count)))
    ]
    if source_rows is not None:
        rows = list(source_rows)
    return tcl.ThinkRequest(
        agent_id=agent_id,
        drive_code=drive_code,
        temporal_state=temporal_state,
        decay_bucket=decay_bucket,
        info_gap_count=int(info_gap_count),
        unresolved_count=int(unresolved_count),
        source_rows=rows,
    )


def _effective_module_path(module: Any) -> Path:
    """模組**實際**被載入的來源檔（變異實驗下＝%TEMP% 底下的副本）。"""
    source = inspect.getsourcefile(module)
    assert source, f"模組 {module!r} 沒有來源檔"
    return Path(source).resolve()


def _imported_module_names(path: Path) -> Set[str]:
    """檔案內**所有** import 的模組名（含 ``from X import Y`` 的 ``X.Y`` 形式）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: Set[str] = set()
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


def _imports_any(names: Set[str], banned: str) -> bool:
    """是否 import 了 ``banned``（含其子模組）。"""
    return any(name == banned or name.startswith(banned + ".") for name in names)


@pytest.fixture
def sandbox_store(tmp_path: Path) -> Path:
    """沙盒輸出目錄（位於 temp 之下 ⇒ 隔離守門應放行）。"""
    store = tmp_path / "sandbox"
    store.mkdir(parents=True, exist_ok=True)
    return store


@pytest.fixture
def enabled(monkeypatch):
    """開啟本票旗標（沙盒）。"""
    monkeypatch.setenv(tcl.FEATURE_ENV, "1")
    return True


# ── T7：閉環三段可觀測 ───────────────────────────────────────────────────
class TestT7ClosureIsObservable:
    def test_t7_writes_exactly_one_working_state_row_with_all_fields(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        trace = tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        assert trace is not None, "閉環應產出 trace"

        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert len(rows) == 1, f"working state 應恰好 1 筆，實得 {len(rows)}"
        row = rows[0]
        assert set(row.keys()) == set(tcl.WORKING_STATE_FIELDS), row.keys()
        assert row["agent_id"] == "agent_akane"
        assert row["resolved_tension_id"] == trace.tension_id
        assert row["drive_code"] == trace.drive_code
        # 🔴 裁定 A（2026-10-22）後 drives_touched 只含 D1（D3 已自驅動目標移除，
        # 即使未決矛盾數 > 0 也不列入）。
        assert row["drives_touched"] == ["D1"]
        assert row["open_gap_count_after"] == 1
        assert row["open_contradiction_count_after"] == 1
        assert row["ts"] == FAKE_NOW

    def test_t7_writes_exactly_one_tension_event_with_production_field_shape(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        rows = _read_jsonl(sandbox_store / tcl.TENSION_STORE_FILENAME)
        assert len(rows) == 1, f"沙盒張力承載應恰好 1 筆，實得 {len(rows)}"
        event = rows[0]
        assert set(event.keys()) == set(TENSION_EVENT_FIELDS), event.keys()
        assert event["kind"] == "tension_resolved"
        # ⚠️ Phase 2 語意（工單 §3.3 允許「只調整期望值」）：本槽有 **2 個**資訊缺口，
        #    而 Phase 2 的決定性思考器**每次思考只消解 1 個** ⇒ 尚未想完 ⇒ 事件狀態為
        #    ``partially_resolved``（``resolved`` 保留給「缺口全消」的情形，見 P2-T9）。
        assert event["status"] == "partially_resolved"
        assert event["agent_id"] == "agent_akane"
        assert event["subject"] == "D1"
        assert event["payload"]["drive_snapshot"]["info_gap_count"] == 2
        assert event["payload"]["drive_snapshot"]["unresolved_count"] == 1

    def test_t7_top1_tension_is_the_lexicographic_first(self, sandbox_store, enabled):
        """字典序：牵挂 > 释然 > 无感；同狀態且同計數 ⇒ **drive_code 決定**。

        ⚠️ 裁定 A（2026-10-22）後**只有 D1** 會成為張力驅動目標 ⇒ 本槽的 top-1 恆為 D1；
        未決矛盾仍作為**觀測因子**被誠實回報（``unresolved_count == 1``），但**不驅動**
        任何張力。
        """
        _seed_sandbox_data()
        trace = tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        assert trace is not None
        assert trace.drive_code == "D1", "同計數時 drive_code（D1<D2<D3）決定先後"
        assert trace.think_request.unresolved_count == 1
        assert trace.think_request.info_gap_count == 2
        assert trace.think_request.temporal_state == "牵挂"
        assert trace.think_request.decay_bucket == "24h-7d"
        assert trace.think_request.source_rows, "ThinkRequest 必須帶可稽核的資料面列"

    def test_t7_no_tension_means_no_file_is_created(self, tmp_path, enabled):
        """無張力 ⇒ 0 寫入（不建立任何檔案）。"""
        store = tmp_path / "empty_sandbox"
        store.mkdir(parents=True, exist_ok=True)
        _seed_sandbox_data()
        # 清掉沙盒資料 ⇒ 三因子皆 0 ⇒ 無張力
        (Path(data_root()) / "soul" / "agent_akane" / "life_threads.jsonl").write_text(
            "", encoding="utf-8"
        )
        assert tcl.drive_loop("agent_akane", FAKE_NOW, store) is None
        assert list(store.iterdir()) == [], "無張力時不得建立任何檔案"


# ── T8：沙盒隔離（生產 bytes 恆等）──────────────────────────────────────
class TestT8SandboxIsolation:
    def test_t8_production_tension_store_bytes_are_identical(self, sandbox_store, enabled):
        _seed_sandbox_data()
        before = _production_snapshot()
        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        after = _production_snapshot()
        assert before == after, "生產張力承載的 bytes 不得改變"
        if before is not None:
            assert hashlib.sha256(before).hexdigest() == hashlib.sha256(after).hexdigest()

    def test_t8_loop_writes_only_under_the_given_store_dir(self, sandbox_store, enabled):
        _seed_sandbox_data()
        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        assert (sandbox_store / tcl.TENSION_STORE_FILENAME).is_file()
        assert (sandbox_store / tcl.WORKING_STATE_FILENAME).is_file()
        # 輸出目錄底下**只有**這兩個檔案（不多寫任何東西）
        assert sorted(p.name for p in sandbox_store.iterdir()) == [
            tcl.TENSION_STORE_FILENAME,
            tcl.WORKING_STATE_FILENAME,
        ]


# ── T9：開關預設 OFF ─────────────────────────────────────────────────────
class TestT9FeatureFlagDefaultsOff:
    def test_t9_feature_is_off_when_env_is_absent(self, monkeypatch):
        monkeypatch.delenv(tcl.FEATURE_ENV, raising=False)
        assert tcl.feature_enabled() is False

    def test_t9_off_loop_returns_none_and_creates_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv(tcl.FEATURE_ENV, raising=False)
        _seed_sandbox_data()
        store = tmp_path / "off_sandbox"
        store.mkdir(parents=True, exist_ok=True)
        assert tcl.drive_loop("agent_akane", FAKE_NOW, store) is None
        assert list(store.iterdir()) == [], "OFF 時不得建立任何檔案"

    def test_t9_off_loop_does_not_touch_production_store_dir(self, monkeypatch):
        monkeypatch.delenv(tcl.FEATURE_ENV, raising=False)
        _seed_sandbox_data()
        prod_store = Path(data_root()) / "inner_life"
        prod_store.mkdir(parents=True, exist_ok=True)
        before = sorted(p.name for p in prod_store.iterdir())
        tcl.drive_loop("agent_akane", FAKE_NOW, prod_store)
        assert sorted(p.name for p in prod_store.iterdir()) == before


# ── T10：拒絕生產 store_dir ─────────────────────────────────────────────
class TestT10RejectsProductionStoreDir:
    def test_t10_store_dir_under_data_root_is_rejected(self, enabled):
        """生產資料根（``data_root()``）之下的子路徑 ⇒ 拒絕執行、回 ``None``。"""
        _seed_sandbox_data()
        prod_store = Path(data_root()) / "inner_life"
        prod_store.mkdir(parents=True, exist_ok=True)
        before = sorted(p.name for p in prod_store.iterdir())
        before_bytes = {
            p.name: p.read_bytes() for p in prod_store.iterdir() if p.is_file()
        }

        assert tcl.drive_loop("agent_akane", FAKE_NOW, prod_store) is None

        after = sorted(p.name for p in prod_store.iterdir())
        assert after == before, f"不得建立任何檔案：{after}"
        for name, blob in before_bytes.items():
            assert (prod_store / name).read_bytes() == blob, f"{name} bytes 不得改變"

    def test_t10_production_tension_store_bytes_are_identical(self, enabled):
        _seed_sandbox_data()
        before = _production_snapshot()
        tcl.drive_loop("agent_akane", FAKE_NOW, Path(data_root()) / "inner_life")
        assert _production_snapshot() == before

    def test_t10_check_store_dir_reports_reason(self):
        allowed, reason = tcl.check_store_dir(Path(data_root()) / "inner_life")
        assert allowed is False
        assert reason, "拒絕必須有理由（可稽核）"

    def test_t10_store_dir_outside_temp_is_rejected(self, enabled):
        """不在 temp 之下的目錄 ⇒ 拒絕（雙條件的第二條）。"""
        _seed_sandbox_data()
        outside = REPO_ROOT / "not_a_sandbox"
        allowed, reason = tcl.check_store_dir(outside)
        assert allowed is False
        assert "temp" in reason or "生產" in reason or "repo" in reason
        assert not outside.exists(), "檢查本身不得建立目錄"


# ═══════════════════════════════════════════════════════════════════════
# CAMPAIGN-B-P2 §3.3 — 守衛測試 P2-T1～P2-T16
# （工單：logs/CAMPAIGN-B-P2-D1-THINKING-LOOP-SANDBOX-WORK-ORDER.md）
#
# 🔴 坑 #1：合成 fixture 必須**與生產事件同形**（欄位齊備）——否則守衛會
#    「意外全綠」⇒ 變異失守而無人察覺。
# 🔴 坑 #3：本檔的「載入點覆寫」只接受 %TEMP% 之下（見 P2_MODULE_OVERRIDE_ENV）。
# ╴══════════════════════════════════════════════════════════════════════


# ── P2-T1：提示詞決定性 ─────────────────────────────────────────────────
class TestP2T1PromptIsDeterministic:
    """P2-T1 — 同一 ``ThinkRequest`` 兩次 ``build_thinking_prompt`` 逐位元相同。"""

    def test_p2_t1_same_request_yields_byte_identical_prompt(self):
        req = _make_request()
        first = tt.build_thinking_prompt(req)
        second = tt.build_thinking_prompt(req)
        assert first == second
        assert first.encode("utf-8") == second.encode("utf-8")
        assert first, "提示詞不得為空"


# ── P2-T2：提示詞必含因子 ───────────────────────────────────────────────
class TestP2T2PromptCarriesFactors:
    """P2-T2 — 提示詞必含 agent_id／drive_code／temporal_state／decay_bucket／
    info_gap_count／unresolved_count。"""

    def test_p2_t2_prompt_contains_every_required_factor(self):
        req = _make_request(info_gap_count=3, unresolved_count=1)
        prompt = tt.build_thinking_prompt(req)
        for token in (
            "agent_id",
            "drive_code",
            "temporal_state",
            "decay_bucket",
            "info_gap_count",
            "unresolved_count",
        ):
            assert token in prompt, f"提示詞缺因子：{token}"
        assert "agent_akane" in prompt
        assert "D1" in prompt
        assert "牵挂" in prompt
        assert "24h-7d" in prompt
        assert "info_gap_count: 3" in prompt
        assert "unresolved_count: 1" in prompt


# ── P2-T3：提示詞不膨脹 ─────────────────────────────────────────────────
class TestP2T3PromptDoesNotBloat:
    """P2-T3 — 提示詞**不得**含 source_rows 原文全文（只含識別子欄位摘要）。"""

    def test_p2_t3_prompt_keeps_only_identifying_fields_and_bounds_the_row_list(self):
        sentinel = "RAW_ROW_PAYLOAD_SENTINEL_XYZ"
        req = _make_request(
            info_gap_count=1,
            source_rows=[
                {
                    "factor": "info_gap",
                    "thread_id": "gap-1",
                    "status": "active",
                    "created_at": "2023-11-13T14:00:00+00:00",
                    "updated_at": "2023-11-13T14:00:00+00:00",
                    "gap_kind": "unknown_fact",
                    "question": "缺口一的問題",
                    "title": sentinel,
                    "contradiction": sentinel,
                    "content": sentinel,
                    "sage_fact_id": None,
                }
            ],
        )
        prompt = tt.build_thinking_prompt(req)
        # 🔴 原文（title／contradiction／content）**不得**進提示詞。
        assert sentinel not in prompt
        # 識別子欄位摘要必須在（可稽核）。
        assert "thread_id=gap-1" in prompt
        assert "gap_kind=unknown_fact" in prompt
        assert "question=缺口一的問題" in prompt
        # 列數上限：20 列 ⇒ 超出截斷並標註 (+N more)。
        many = _make_request(info_gap_count=0, source_rows=[
            {
                "factor": "info_gap",
                "thread_id": f"gap-{idx + 1}",
                "updated_at": "2023-11-13T14:00:00+00:00",
            }
            for idx in range(25)
        ])
        long_prompt = tt.build_thinking_prompt(many)
        assert "(+5 more)" in long_prompt
        assert "thread_id=gap-21" not in long_prompt
        assert "thread_id=gap-20" in long_prompt
        assert len(long_prompt) < 4096, "提示詞不得無界膨脹"


# ── P2-T4：提示詞無副作用 ───────────────────────────────────────────────
class TestP2T4PromptHasNoSideEffects:
    """P2-T4 — 建構提示詞**不讀不寫**（沙盒與隔離資料根的檔案數／位元組不變）。"""

    def test_p2_t4_building_the_prompt_touches_no_file(self, sandbox_store):
        _seed_sandbox_data()
        before_store = _dir_snapshot(sandbox_store)
        before_data = _dir_snapshot(Path(data_root()))
        prompt = tt.build_thinking_prompt(_make_request())
        assert prompt
        assert _dir_snapshot(sandbox_store) == before_store, "沙盒目錄不得被動過"
        assert _dir_snapshot(Path(data_root())) == before_data, "資料根不得被動過"


# ── P2-T5：Mock — 無缺口 ────────────────────────────────────────────────
class TestP2T5MockWithoutOpenGap:
    """P2-T5 — ``info_gap_count=0`` ⇒ decided／partial=1.0／remaining=0。"""

    def test_p2_t5_mock_with_no_open_gap_is_fully_decided(self):
        req = _make_request(info_gap_count=0, source_rows=[])
        outcome = tt.DeterministicMockThinker().think(req, tt.build_thinking_prompt(req))
        assert outcome.decided is True
        assert outcome.partial_resolution == 1.0
        assert outcome.remaining_gap_count == 0
        assert outcome.reason == "no_open_info_gap"


# ── P2-T6：Mock — 部分消解 ──────────────────────────────────────────────
class TestP2T6MockPartiallyResolves:
    """P2-T6 — ``info_gap_count=3`` ⇒ partial=1/3／remaining=2／decided=False。"""

    def test_p2_t6_mock_resolves_exactly_one_gap_per_think(self):
        req = _make_request(info_gap_count=3)
        outcome = tt.DeterministicMockThinker().think(req, tt.build_thinking_prompt(req))
        assert outcome.partial_resolution == pytest.approx(1.0 / 3)
        assert outcome.remaining_gap_count == 2
        assert outcome.decided is False
        assert outcome.reason == "resolved_one_info_gap"


# ── P2-T7：Mock 決定性 ──────────────────────────────────────────────────
class TestP2T7MockIsDeterministic:
    """P2-T7 — 同輸入兩次 ``think()`` 結果**完全相同**（禁 id()／time()／hash()）。"""

    def test_p2_t7_two_thinks_on_the_same_request_are_identical(self):
        req = _make_request()
        prompt = tt.build_thinking_prompt(req)
        thinker = tt.DeterministicMockThinker()
        first = thinker.think(req, prompt)
        second = thinker.think(req, prompt)
        assert first == second
        assert first.to_dict() == second.to_dict()


# ── P2-T8：Null 對照組 ──────────────────────────────────────────────────
class TestP2T8NullThinker:
    """P2-T8 — ``NullThinker`` ⇒ partial=0.0／decided=False／remaining=缺口數。"""

    def test_p2_t8_null_thinker_resolves_nothing(self):
        req = _make_request(info_gap_count=3)
        outcome = tt.NullThinker().think(req, tt.build_thinking_prompt(req))
        assert outcome.partial_resolution == 0.0
        assert outcome.decided is False
        assert outcome.remaining_gap_count == 3
        assert outcome.reason == "null_thinker"
        # 兩個思考器都必須滿足 Thinker 契約（Protocol：method 存在即可）。
        assert isinstance(tt.DeterministicMockThinker(), tt.Thinker)
        assert isinstance(tt.NullThinker(), tt.Thinker)


# ── P2-T9：閉環事件分流 ─────────────────────────────────────────────────
class TestP2T9EventStatusFollowsOutcome:
    """P2-T9 — 注入 mock ⇒ 沙盒張力承載恰好 +1 筆；缺口 ≥2 ⇒ partially_resolved，
    缺口 =1 ⇒ resolved。"""

    def test_p2_t9_event_status_follows_the_thinking_outcome(self, sandbox_store, enabled):
        _seed_sandbox_data()  # 2 個資訊缺口
        trace = tcl.drive_loop(
            "agent_akane", FAKE_NOW, sandbox_store, thinker=tt.DeterministicMockThinker()
        )
        assert trace is not None
        rows = _read_jsonl(sandbox_store / tcl.TENSION_STORE_FILENAME)
        assert len(rows) == 1, f"沙盒張力承載應恰好 1 筆，實得 {len(rows)}"
        assert rows[0]["status"] == "partially_resolved"
        assert rows[0]["payload"]["partial_resolution"] == pytest.approx(0.5)
        assert rows[0]["payload"]["remaining_gap_count"] == 1
        assert rows[0]["payload"]["decided"] is False
        assert rows[0]["payload"]["reason"] == "resolved_one_info_gap"

        # 只有 1 個缺口 ⇒ 一次即消解完 ⇒ resolved。
        single_store = sandbox_store.parent / "sandbox_single_gap"
        single_store.mkdir(parents=True, exist_ok=True)
        _seed_sandbox_data(rows=[SANDBOX_ROWS[0]])
        trace_single = tcl.drive_loop(
            "agent_akane", FAKE_NOW, single_store, thinker=tt.DeterministicMockThinker()
        )
        assert trace_single is not None
        single_rows = _read_jsonl(single_store / tcl.TENSION_STORE_FILENAME)
        assert len(single_rows) == 1
        assert single_rows[0]["status"] == "resolved"
        assert single_rows[0]["payload"]["decided"] is True
        assert single_rows[0]["payload"]["partial_resolution"] == 1.0


# ── P2-T10：Null 注入 ───────────────────────────────────────────────────
class TestP2T10NullThinkerInjection:
    """P2-T10 — 注入 ``NullThinker`` ⇒ 事件為 partially_resolved 且 partial=0.0。"""

    def test_p2_t10_null_thinker_injection_yields_partially_resolved_event(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        trace = tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store, thinker=tt.NullThinker())
        assert trace is not None
        rows = _read_jsonl(sandbox_store / tcl.TENSION_STORE_FILENAME)
        assert len(rows) == 1
        event = rows[0]
        assert event["status"] == "partially_resolved"
        assert event["payload"]["partial_resolution"] == 0.0
        assert event["payload"]["remaining_gap_count"] == 2
        assert event["payload"]["decided"] is False
        assert event["payload"]["reason"] == "null_thinker"


# ── P2-T11：working state 新欄位 ────────────────────────────────────────
class TestP2T11WorkingStateNewFields:
    """P2-T11 — 沙盒 working_state 恰好 +1 筆，且 Phase 2 新增的 6 欄齊全且型別正確。"""

    def test_p2_t11_working_state_carries_the_six_new_phase2_fields(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        trace = tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        assert trace is not None
        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert len(rows) == 1, f"working state 應恰好 1 筆，實得 {len(rows)}"
        row = rows[0]
        # Phase 1 的 7 欄一個都沒少。
        for key in (
            "agent_id",
            "ts",
            "resolved_tension_id",
            "drive_code",
            "drives_touched",
            "open_gap_count_after",
            "open_contradiction_count_after",
        ):
            assert key in row, f"Phase 1 欄位消失：{key}"
        # Phase 2 新增 6 欄（含型別）。
        assert isinstance(row["open_gap_count_before"], int)
        assert not isinstance(row["open_gap_count_before"], bool)
        assert row["open_gap_count_before"] == 2
        assert isinstance(row["partial_resolution"], float)
        assert row["partial_resolution"] == pytest.approx(0.5)
        assert isinstance(row["remaining_gap_count"], int)
        assert not isinstance(row["remaining_gap_count"], bool)
        assert row["remaining_gap_count"] == 1
        assert row["decided"] is False
        assert isinstance(row["thinker"], str)
        assert row["thinker"] == "DeterministicMockThinker"
        assert isinstance(row["think_prompt_sha256"], str)
        assert len(row["think_prompt_sha256"]) == 64
        int(row["think_prompt_sha256"], 16)  # 必須是合法 hex
        # 欄位集合與常數一致（既有 7 欄 ＋ 新增 6 欄）。
        assert set(row.keys()) == set(tcl.WORKING_STATE_FIELDS)


# ── P2-T12：沙盒隔離 ─────────────────────────────────────────────────────
class TestP2T12SandboxIsolation:
    """P2-T12 — 執行前後**生產**張力承載與 ``data/soul/*/life_threads.jsonl``
    位元恆等。"""

    def test_p2_t12_production_files_are_byte_identical_after_the_loop(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        before = _production_files_snapshot()
        assert before, "生產對帳基準不應為空（否則本測試是假紅／假綠）"
        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        after = _production_files_snapshot()
        assert before == after, "生產檔案位元不得改變"


# ── P2-T13：舊行不可改寫 ────────────────────────────────────────────────
class TestP2T13AppendOnly:
    """P2-T13 — working state / 張力承載的既有行位元在第二次執行後**不變**。"""

    def test_p2_t13_existing_rows_are_never_rewritten(self, sandbox_store, enabled):
        _seed_sandbox_data()
        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)
        state_path = sandbox_store / tcl.WORKING_STATE_FILENAME
        tension_path = sandbox_store / tcl.TENSION_STORE_FILENAME
        before_state = state_path.read_bytes()
        before_tension = tension_path.read_bytes()

        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store)

        after_state = state_path.read_bytes()
        after_tension = tension_path.read_bytes()
        assert after_state.startswith(before_state), "working state 舊行被改寫"
        assert after_tension.startswith(before_tension), "張力承載舊行被改寫"
        assert len(after_state) > len(before_state), "第二次執行必須是 append"
        assert len(after_tension) > len(before_tension), "第二次執行必須是 append"


# ── P2-T14：旗標預設 OFF ────────────────────────────────────────────────
class TestP2T14FeatureFlagDefaultsOff:
    """P2-T14 — 旗標預設 ``False``；OFF 呼叫 ``drive_loop`` 不建立任何檔案、回 ``None``。"""

    def test_p2_t14_flag_is_off_by_default_and_creates_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv(tcl.FEATURE_ENV, raising=False)
        assert tcl.feature_enabled() is False
        _seed_sandbox_data()
        store = tmp_path / "p2_t14_off_sandbox"
        store.mkdir(parents=True, exist_ok=True)
        assert tcl.drive_loop("agent_akane", FAKE_NOW, store) is None
        assert list(store.iterdir()) == [], "OFF 時不得建立任何檔案"


# ── P2-T15：無 LLM 路徑（AST）───────────────────────────────────────────
class TestP2T15ThinkingModuleHasNoLlmPath:
    """P2-T15 — ``tension_thinking.py`` 不得 import LLM／網路／行程相關模組。"""

    def test_p2_t15_thinking_module_imports_no_llm_or_network_module(self):
        path = _effective_module_path(tt)
        names = _imported_module_names(path)
        for banned in (
            "src.llm",
            "httpx",
            "requests",
            "socket",
            "subprocess",
            "urllib",
            "asyncio",
        ):
            assert not _imports_any(names, banned), f"{path.name} 不得 import {banned}"


# ── P2-T16：閉環不得接 LLM（AST）────────────────────────────────────────
class TestP2T16ClosureLoopHasNoLlmProxy:
    """P2-T16 — ``tension_closure_loop.py`` 不得 import ``src.llm.proxy``。"""

    def test_p2_t16_closure_loop_does_not_import_llm_proxy(self):
        path = _effective_module_path(tcl)
        names = _imported_module_names(path)
        assert not _imports_any(names, "src.llm.proxy"), (
            f"{path.name} 不得 import src.llm.proxy"
        )


# ═══════════════════════════════════════════════════════════════════════
# CAMPAIGN-B-P3 §3 — 守衛測試 P4-T1～P4-T12
# （工單：logs/CAMPAIGN-B-P3-LLM-THINKING-WIRING-WORK-ORDER.md）
#
# 🔴 零真實呼叫：P4 守衛全部以 **script backend**（假 LLM 通道）驅動
#    ⇒ 零網路、零成本、可重現（AST 斷言見 P4-T12）。
# 🔴 幻覺護欄：LLM 說了不算 —— resolved／remaining／decided 全部受**實際缺口數**約束。
# ═══════════════════════════════════════════════════════════════════════

#: Phase 2 提示詞的**逐位元基準**（sha256；2026-10-23 釘住 ``build_thinking_prompt`` 未變動）。
PHASE2_PROMPT_SHA256 = "d37bf79274ef5bd636a1ffae31b117f011bc970ea1c001f1747266518268f5c0"

#: 2 個缺口、LLM 消解 1 個的**合法**回應（script backend 回放用）。
VALID_OUTCOME_2_GAPS = (
    '{"resolved_gap_count": 1, "remaining_gap_count": 1, '
    '"decided": false, "reason": "llm_one_gap_left"}'
)


class _ScriptBackend:
    """**零成本**的假 LLM 通道（script backend）：記錄呼叫、可回放預設回應。

    🔴 **不 import** ``src.llm``（避免載入 proxy 的排程接線副作用）⇒ 只以
    **介面契約**模擬：``generate_text(messages, agent_id=, max_tokens=, **kwargs)``
    —— 與 :class:`~src.llm.proxy.LLMProxy` 的呼叫面一致。
    """

    def __init__(
        self,
        responses: Optional[List[Any]] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._responses = list(responses or [])
        self._raises = raises

    async def generate_text(
        self,
        messages: List[Dict[str, str]],
        agent_id: str = "system",
        max_tokens: int = 200,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "messages": messages,
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        if self._raises is not None:
            raise self._raises
        if self._responses:
            return self._responses.pop(0)
        return None


class _SyncLLMThinkerBridge(tt.LLMThinker):
    """把 **async** 的 ``LLMThinker.think()`` 橋接成**同步** Thinker（沙盒接線用）。

    🔴 為什麼需要：``LLMProxy.generate_text`` 是 async ⇒ ``LLMThinker.think()``
    必須是 async（工單 §2.3）⇒ 既有**同步** ``drive_loop()`` 無法直接 await。
    本橋接器**只存在於測試檔**：生產的 async 閉環變體（``drive_loop_async``）
    需要動 ``tension_closure_loop.py``（第 4 檔）⇒ 不在本票範圍
    （工單 §7.3：``git diff --name-only HEAD`` 恰好 3 檔）。
    """

    def think(self, req: Any, prompt: str) -> Any:  # type: ignore[override]
        return asyncio.run(tt.LLMThinker.think(self, req, prompt))


def _gt_check_lefts(func: Any) -> List[str]:
    """AST 層：函式內所有 ``<左> > gap_count`` 比較的左側原始碼。

    用途：把**每一道幻覺護欄的存在性**釘死。
    🔴 為什麼需要「存在性」斷言：護欄 ③（``resolved + remaining > gap``）在數學上
    **蘊含**護欄 ①② ⇒ 單純移除 ① 或 ② 時**行為不變** ⇒ 只靠行為斷言無法證明
    「那條變異本身」被轉紅 ⇒ 必須另外以結構性斷言證明檢查真的在程式碼裡。
    """
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    lefts: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Gt):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Name) and comparator.id == "gap_count":
                lefts.append(ast.unparse(node.left))
    return lefts


# ── P4-T1：LLM 提示詞含 OUTPUT 段 ─────────────────────────────────────
class TestP4T1LlmPromptHasOutputSection:
    """P4-T1 — LLM 提示詞必含**六段**標題 ＋ **四個** JSON 欄位名。"""

    def test_p4_t1_llm_prompt_carries_six_sections_and_four_json_fields(self):
        req = _make_request()
        prompt = tt.build_llm_thinking_prompt(req)
        assert len(tt.LLM_PROMPT_SECTIONS) == 6
        positions: List[int] = []
        for section in tt.LLM_PROMPT_SECTIONS:
            assert section in prompt, f"LLM 提示詞缺段落：{section}"
            positions.append(prompt.index(section))
        assert positions == sorted(positions), "六段標題必須依序出現"
        for field in ("resolved_gap_count", "remaining_gap_count", "decided", "reason"):
            assert field in prompt, f"OUTPUT 段缺欄位：{field}"
        assert "JSON 物件" in prompt, "OUTPUT 段必須要求單一 JSON 物件"
        assert "不得臆造" in prompt, "OUTPUT 段必須禁止臆造外部事實"


# ── P4-T2：既有提示詞零變動 ───────────────────────────────────────────
class TestP4T2Phase2PromptUnchanged:
    """P4-T2 — ``build_thinking_prompt()`` 與 Phase 2 基準**逐位元相同**。"""

    def test_p4_t2_phase2_prompt_is_byte_identical_to_the_pinned_baseline(self):
        req = _make_request()
        prompt = tt.build_thinking_prompt(req)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        assert digest == PHASE2_PROMPT_SHA256, "Phase 2 提示詞逐位元變動了"

    def test_p4_t2_llm_prompt_keeps_the_five_existing_sections_verbatim(self):
        req = _make_request()
        base = tt.build_thinking_prompt(req)
        llm_prompt = tt.build_llm_thinking_prompt(req)
        assert llm_prompt.startswith(base), "既有五段必須逐位元保留"
        assert llm_prompt[len(base) :].startswith("\n\nOUTPUT"), "第六段必須接在五段之後"


# ── P4-T3：解析成功 ───────────────────────────────────────────────────
class TestP4T3ParseSuccess:
    """P4-T3 — 合法 JSON ⇒ outcome 正確且型別正確。"""

    def test_p4_t3_valid_json_is_parsed_into_the_expected_outcome(self):
        req = _make_request(info_gap_count=2)
        outcome = tt.parse_llm_outcome(VALID_OUTCOME_2_GAPS, req)
        assert outcome.partial_resolution == pytest.approx(0.5)
        assert outcome.remaining_gap_count == 1
        assert outcome.decided is False
        assert outcome.reason == "llm_one_gap_left"
        assert isinstance(outcome.partial_resolution, float)
        assert isinstance(outcome.remaining_gap_count, int)
        assert not isinstance(outcome.remaining_gap_count, bool)
        assert isinstance(outcome.decided, bool)

    def test_p4_t3_fenced_json_and_zero_gap_case(self):
        req = _make_request(info_gap_count=2)
        fenced = "```json\n" + VALID_OUTCOME_2_GAPS + "\n```"
        assert tt.parse_llm_outcome(fenced, req) == tt.parse_llm_outcome(
            VALID_OUTCOME_2_GAPS, req
        )
        empty = _make_request(info_gap_count=0, source_rows=[])
        outcome = tt.parse_llm_outcome(
            '{"resolved_gap_count": 0, "remaining_gap_count": 0, '
            '"decided": true, "reason": "none"}',
            empty,
        )
        assert outcome.partial_resolution == 1.0
        assert outcome.decided is True
        assert outcome.remaining_gap_count == 0


# ── P4-T4：解析失敗 fail-closed ───────────────────────────────────────
class TestP4T4ParseFailureFailsClosed:
    """P4-T4 — 非 JSON／空字串 ⇒ fail-closed（``llm_parse_failed``）。"""

    def test_p4_t4_unparsable_response_fails_closed(self):
        req = _make_request(info_gap_count=3)
        for text in ("", "   ", "not json at all", '{"resolved_gap_count": ', "[1, 2, 3]"):
            outcome = tt.parse_llm_outcome(text, req)
            assert outcome.partial_resolution == 0.0, text
            assert outcome.decided is False, text
            assert outcome.reason == "llm_parse_failed", text
            assert outcome.remaining_gap_count == req.info_gap_count, text


# ── P4-T5：幻覺護欄 ①（resolved 不得大於實際缺口數）────────────────────
class TestP4T5HallucinatedResolvedCountFailsClosed:
    """P4-T5 — ``resolved_gap_count > info_gap_count`` ⇒ fail-closed。"""

    def test_p4_t5_resolved_above_the_actual_gap_count_fails_closed(self):
        req = _make_request(info_gap_count=2)
        text = (
            '{"resolved_gap_count": 3, "remaining_gap_count": 0, '
            '"decided": true, "reason": "all_done"}'
        )
        outcome = tt.parse_llm_outcome(text, req)
        assert outcome.reason == "llm_outcome_invalid"
        assert outcome.partial_resolution == 0.0
        assert outcome.decided is False
        assert outcome.remaining_gap_count == req.info_gap_count

    def test_p4_t5_the_resolved_guard_must_exist_as_an_explicit_check(self):
        """🔴 護欄 ① 的**存在性**（行為斷言無法與護欄 ③ 區分）。"""
        lefts = _gt_check_lefts(tt.parse_llm_outcome)
        assert "resolved" in lefts, f"幻覺護欄 ① 不在程式碼裡：{lefts}"


# ── P4-T6：幻覺護欄 ②／③（remaining 與合計不得超額）──────────────────
class TestP4T6HallucinatedRemainingCountFailsClosed:
    """P4-T6 — ``remaining > info_gap_count`` 或 ``resolved+remaining > info_gap_count``
    ⇒ fail-closed。"""

    def test_p4_t6_remaining_or_total_above_the_actual_gap_count_fails_closed(self):
        req = _make_request(info_gap_count=2)
        too_many_remaining = (
            '{"resolved_gap_count": 0, "remaining_gap_count": 3, '
            '"decided": false, "reason": "x"}'
        )
        too_many_total = (
            '{"resolved_gap_count": 2, "remaining_gap_count": 1, '
            '"decided": false, "reason": "x"}'
        )
        for text in (too_many_remaining, too_many_total):
            outcome = tt.parse_llm_outcome(text, req)
            assert outcome.reason == "llm_outcome_invalid", text
            assert outcome.partial_resolution == 0.0, text
            assert outcome.decided is False, text
            assert outcome.remaining_gap_count == req.info_gap_count, text

    def test_p4_t6_the_remaining_and_total_guards_must_exist(self):
        """🔴 護欄 ②／③ 的**存在性**。"""
        lefts = _gt_check_lefts(tt.parse_llm_outcome)
        assert "remaining" in lefts, f"幻覺護欄 ② 不在程式碼裡：{lefts}"
        assert "resolved + remaining" in lefts, f"幻覺護欄 ③ 不在程式碼裡：{lefts}"


# ── P4-T7：decided=True 但仍有缺口 ⇒ fail-closed ──────────────────────
class TestP4T7DecidedWithRemainingFailsClosed:
    """P4-T7 — ``decided is True`` 但 ``remaining_gap_count != 0`` ⇒ fail-closed。"""

    def test_p4_t7_decided_true_with_open_gaps_fails_closed(self):
        req = _make_request(info_gap_count=2)
        text = (
            '{"resolved_gap_count": 1, "remaining_gap_count": 1, '
            '"decided": true, "reason": "done"}'
        )
        outcome = tt.parse_llm_outcome(text, req)
        assert outcome.reason == "llm_outcome_invalid"
        assert outcome.partial_resolution == 0.0
        assert outcome.decided is False
        assert outcome.remaining_gap_count == req.info_gap_count


# ── P4-T8：型別錯誤 ⇒ fail-closed ─────────────────────────────────────
class TestP4T8TypeErrorsFailClosed:
    """P4-T8 — bool 冒充 int／float 冒充 int／負數／decided 非 bool／reason 非 str／
    欄位缺漏 ⇒ 一律 fail-closed。"""

    def test_p4_t8_wrong_types_or_missing_fields_fail_closed(self):
        req = _make_request(info_gap_count=2)
        cases = [
            # bool 冒充 int
            '{"resolved_gap_count": true, "remaining_gap_count": 1, "decided": false, "reason": "x"}',
            # float 冒充 int
            '{"resolved_gap_count": 1.0, "remaining_gap_count": 1, "decided": false, "reason": "x"}',
            # 負數
            '{"resolved_gap_count": -1, "remaining_gap_count": 1, "decided": false, "reason": "x"}',
            # decided 非 bool
            '{"resolved_gap_count": 1, "remaining_gap_count": 1, "decided": "false", "reason": "x"}',
            # reason 非 str
            '{"resolved_gap_count": 1, "remaining_gap_count": 1, "decided": false, "reason": 7}',
            # 欄位缺漏（reason）
            '{"resolved_gap_count": 1, "remaining_gap_count": 1, "decided": false}',
            # 欄位缺漏（remaining）
            '{"resolved_gap_count": 1, "decided": false, "reason": "x"}',
        ]
        for text in cases:
            outcome = tt.parse_llm_outcome(text, req)
            assert outcome.reason == "llm_outcome_invalid", text
            assert outcome.partial_resolution == 0.0, text
            assert outcome.decided is False, text
            assert outcome.remaining_gap_count == req.info_gap_count, text


# ── P4-T9：呼叫預算 ───────────────────────────────────────────────────
class TestP4T9CallBudgetIsEnforced:
    """P4-T9 — ``max_calls`` 用盡後**不再呼叫**（script backend 計數不增加）
    且回 fail-closed（``llm_call_budget_exhausted``）。"""

    async def test_p4_t9_budget_exhaustion_stops_further_calls(self):
        backend = _ScriptBackend(responses=[VALID_OUTCOME_2_GAPS, VALID_OUTCOME_2_GAPS])
        thinker = tt.LLMThinker(backend, model="script-model", max_calls=1, max_tokens=400)
        req = _make_request(info_gap_count=2)
        first = await thinker.think(req, tt.build_llm_thinking_prompt(req))
        assert first.reason == "llm_one_gap_left"
        assert len(backend.calls) == 1

        second = await thinker.think(req, tt.build_llm_thinking_prompt(req))
        assert second.reason == "llm_call_budget_exhausted"
        assert second.partial_resolution == 0.0
        assert second.decided is False
        assert second.remaining_gap_count == req.info_gap_count
        assert len(backend.calls) == 1, "預算用盡後不得再呼叫"
        assert thinker.calls["budget_exhausted"] == 1
        assert thinker.calls["attempted"] == 1


# ── P4-T10：失敗不污染 ────────────────────────────────────────────────
class TestP4T10FailureDoesNotEscape:
    """P4-T10 — proxy 拋例外 ⇒ ``think()`` 回 fail-closed，**不拋出**。"""

    async def test_p4_t10_proxy_exception_is_contained(self):
        backend = _ScriptBackend(raises=RuntimeError("boom"))
        thinker = tt.LLMThinker(backend, model="script-model", max_calls=1)
        req = _make_request(info_gap_count=2)
        outcome = await thinker.think(req, tt.build_llm_thinking_prompt(req))
        assert outcome.partial_resolution == 0.0
        assert outcome.decided is False
        assert outcome.reason == "llm_call_failed"
        assert outcome.remaining_gap_count == req.info_gap_count
        assert len(backend.calls) == 1

    async def test_p4_t10_none_and_empty_responses_fail_closed(self):
        cases = ((None, "llm_call_failed"), ("", "llm_parse_failed"), ("   ", "llm_parse_failed"))
        for response, expected in cases:
            backend = _ScriptBackend(responses=[response])
            thinker = tt.LLMThinker(backend, model="script-model", max_calls=1)
            req = _make_request(info_gap_count=2)
            outcome = await thinker.think(req, tt.build_llm_thinking_prompt(req))
            assert outcome.reason == expected, repr(response)
            assert outcome.partial_resolution == 0.0, repr(response)
            assert outcome.decided is False, repr(response)
            assert outcome.remaining_gap_count == req.info_gap_count, repr(response)


# ── P4-T11：接線面（script backend → 閉環）────────────────────────────
class TestP4T11LlmThinkerDrivesTheClosureLoop:
    """P4-T11 — 以假 backend 回傳合法 JSON ⇒ 閉環產出 ``partially_resolved`` 事件
    ＋ working state 的 partial_resolution／remaining_gap_count／decided 正確
    （``reason`` 欄位屬於**事件 payload**，working state 的固定欄位集不含它）。"""

    def test_p4_t11_llm_thinker_drives_the_closure_loop_end_to_end(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()  # 2 個資訊缺口
        backend = _ScriptBackend(responses=[VALID_OUTCOME_2_GAPS])
        thinker = _SyncLLMThinkerBridge(
            backend, model="script-model", max_calls=1, max_tokens=400
        )
        assert isinstance(thinker, tt.LLMThinker)
        trace = tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store, thinker=thinker)
        assert trace is not None

        events = _read_jsonl(sandbox_store / tcl.TENSION_STORE_FILENAME)
        assert len(events) == 1, f"沙盒張力承載應恰好 1 筆，實得 {len(events)}"
        event = events[0]
        payload = event["payload"]
        assert event["status"] == "partially_resolved"
        assert payload["partial_resolution"] == pytest.approx(0.5)
        assert payload["remaining_gap_count"] == 1
        assert payload["decided"] is False
        assert payload["reason"] == "llm_one_gap_left"

        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert len(rows) == 1
        row = rows[0]
        assert row["partial_resolution"] == pytest.approx(0.5)
        assert row["remaining_gap_count"] == 1
        assert row["decided"] is False
        assert row["open_gap_count_before"] == 2
        assert row["open_gap_count_after"] == 1
        assert row["thinker"] == "_SyncLLMThinkerBridge"

        # 呼叫面：只有 1 次、system+user 兩段、user 必含 OUTPUT 段、禁重試。
        assert len(backend.calls) == 1
        sent = backend.calls[0]
        assert [m["role"] for m in sent["messages"]] == ["system", "user"]
        assert "OUTPUT" in sent["messages"][1]["content"]
        assert sent["max_tokens"] == 400
        assert sent["kwargs"].get("max_retries") == 0


# ── P4-T13：decided ⇔ remaining 必須一致（自相矛盾不可信）──────────────
class TestP4T13DecidedMustMatchRemaining:
    """P4-T13 — ``decided is False`` 卻宣稱「一個缺口都不剩」⇒ fail-closed
    （Owner 裁示 2026-10-23 21:2x：自相矛盾的回應不可信）。

    🔴 反向測試：``decided=True`` ＋ ``remaining=0`` 仍**合法通過**
    （「想完了」與「零剩餘」是一致組合，不被本護欄誤殺）。
    """

    def test_p4_t13_not_decided_but_zero_remaining_is_rejected(self):
        req = _make_request(info_gap_count=2)
        # 🔴 本輸入只被護欄 ④-b 擋下（① 1≤2、② 0≤2、③ 1+0=1≤2 全通過）。
        text = (
            '{"resolved_gap_count": 1, "remaining_gap_count": 0, '
            '"decided": false, "reason": "contradiction"}'
        )
        outcome = tt.parse_llm_outcome(text, req)
        assert outcome.reason == "llm_outcome_invalid"
        assert outcome.partial_resolution == 0.0
        assert outcome.decided is False
        assert outcome.remaining_gap_count == req.info_gap_count

    def test_p4_t13_decided_true_with_zero_remaining_is_accepted(self):
        """反向：``decided=True`` ＋ ``remaining=0`` ⇒ 合法（不得被誤殺）。"""
        req = _make_request(info_gap_count=2)
        text = (
            '{"resolved_gap_count": 2, "remaining_gap_count": 0, '
            '"decided": true, "reason": "all_done"}'
        )
        outcome = tt.parse_llm_outcome(text, req)
        assert outcome.reason == "all_done"
        assert outcome.partial_resolution == pytest.approx(1.0)
        assert outcome.decided is True
        assert outcome.remaining_gap_count == 0


# ── P4-T12：零真實呼叫 ────────────────────────────────────────────────
class TestP4T12NoRealNetworkPath:
    """P4-T12 — 全部 P4 守衛執行期間**不得**有任何真實網路呼叫
    ⇒ script backend 計數 ＋ AST 斷言（無 httpx／requests／urllib／socket／
    subprocess／src.llm import）。"""

    def test_p4_t12_thinking_module_has_no_network_import_path(self):
        path = _effective_module_path(tt)
        names = _imported_module_names(path)
        for banned in ("src.llm", "httpx", "requests", "urllib", "socket", "subprocess"):
            assert not _imports_any(names, banned), f"{path.name} 不得 import {banned}"

    async def test_p4_t12_script_backend_is_the_only_call_path(self):
        backend = _ScriptBackend(responses=[VALID_OUTCOME_2_GAPS])
        thinker = tt.LLMThinker(backend, model="script-model", max_calls=1)
        req = _make_request(info_gap_count=2)
        outcome = await thinker.think(req, tt.build_llm_thinking_prompt(req))
        assert outcome.reason == "llm_one_gap_left"
        assert len(backend.calls) == 1, "P4 守衛期間只允許 script backend 呼叫"
        assert thinker.calls["attempted"] == 1
        assert thinker.calls["succeeded"] == 1


# ═══════════════════════════════════════════════════════════════════════
# CAMPAIGN-B-P4 §3 — 守衛測試 P5-T1～P5-T12
# （工單：logs/CAMPAIGN-B-P4-ASYNC-THINKING-LOOP-WORK-ORDER.md）
#
# 🔴 坑 #1：合成 fixture 必須**與生產事件同形**（欄位齊備）——否則守衛會
#    「意外全綠」⇒ 變異失守而無人察覺。
# 🔴 坑 #3：本檔的「載入點覆寫」只接受 %TEMP% 之下（見 P2_MODULE_OVERRIDE_ENV）。
# ═══════════════════════════════════════════════════════════════════════


def _p5_fake_sent_sha(prompt: str) -> str:
    """假思考器「**實際送出**」的提示詞 sha（**刻意不同於** Phase 2 提示詞的 sha）。

    🔴 為什麼要刻意不同：守衛 P5-T5 要能分辨「錨點來自**送出者**」與「接收者
    **自行重算**」⇒ 兩者必須產生**不同**的值，否則守衛會「意外全綠」。
    """
    return hashlib.sha256(("P5-SENT::" + prompt).encode("utf-8")).hexdigest()


class _P5SyncFakeThinker:
    """**同步**假思考器（零成本、零網路）；回報「實際送出」的 sha。"""

    def __init__(
        self,
        *,
        decided: bool = False,
        remaining_gap_count: int = 1,
        partial_resolution: float = 0.5,
    ) -> None:
        self.decided = bool(decided)
        self.remaining_gap_count = int(remaining_gap_count)
        self.partial_resolution = float(partial_resolution)
        #: 收到的提示詞（可稽核「閉環組了什麼」）。
        self.seen_prompts: List[str] = []

    def think(self, req: Any, prompt: str) -> Any:
        self.seen_prompts.append(prompt)
        return tt.ThinkOutcome(
            partial_resolution=self.partial_resolution,
            remaining_gap_count=self.remaining_gap_count,
            decided=self.decided,
            reason="p5_fake_outcome",
            think_prompt_sha256=_p5_fake_sent_sha(prompt),
        )


class _P5AsyncFakeThinker:
    """**async** 假思考器（零成本、零網路）；回報「實際送出」的 sha。

    🔴 與同步版**刻意同名**（見檔尾的 ``__name__`` 指派）：working state 的
    ``thinker`` 欄位記的是**類別名** ⇒ 兩條路徑必須用**同名**的思考器，
    守衛 P5-T4 的「逐位元相同」才比的是**資料面**而不是標籤。
    """

    def __init__(
        self,
        *,
        decided: bool = False,
        remaining_gap_count: int = 1,
        partial_resolution: float = 0.5,
    ) -> None:
        self.decided = bool(decided)
        self.remaining_gap_count = int(remaining_gap_count)
        self.partial_resolution = float(partial_resolution)
        self.seen_prompts: List[str] = []

    async def think(self, req: Any, prompt: str) -> Any:
        self.seen_prompts.append(prompt)
        return tt.ThinkOutcome(
            partial_resolution=self.partial_resolution,
            remaining_gap_count=self.remaining_gap_count,
            decided=self.decided,
            reason="p5_fake_outcome",
            think_prompt_sha256=_p5_fake_sent_sha(prompt),
        )


# 🔴 同名（見 class docstring）：兩條路徑的 ``thinker`` 欄位才會相同。
_P5AsyncFakeThinker.__name__ = _P5SyncFakeThinker.__name__


def _p5_working_state_row(
    *,
    agent_id: str = "agent_akane",
    ts: float = FAKE_NOW,
    decided: bool = False,
    remaining_gap_count: int = 1,
    partial_resolution: float = 0.5,
    **overrides: Any,
) -> Dict[str, Any]:
    """**與生產同形**的 working state 記錄（🔴 坑 #1：欄位齊備，13 欄）。"""
    row: Dict[str, Any] = {
        "agent_id": agent_id,
        "ts": float(ts),
        "resolved_tension_id": "drive_tension:0123456789abcdef",
        "drive_code": "D1",
        "drives_touched": ["D1"],
        "open_gap_count_after": int(remaining_gap_count),
        "open_contradiction_count_after": 1,
        "open_gap_count_before": 2,
        "partial_resolution": float(partial_resolution),
        "remaining_gap_count": int(remaining_gap_count),
        "decided": bool(decided),
        "thinker": "DeterministicMockThinker",
        "think_prompt_sha256": "0" * 64,
    }
    row.update(overrides)
    assert set(row.keys()) == set(tcl.WORKING_STATE_FIELDS), "fixture 必須欄位齊備（同形）"
    return row


def _p5_write_working_state(
    store: Path,
    rows: List[Dict[str, Any]],
    *,
    raw_lines: Optional[List[str]] = None,
) -> Path:
    """把 working state 記錄寫進沙盒（**測試端**寫入；生產同形：append-only JSONL、LF）。"""
    path = store / tcl.WORKING_STATE_FILENAME
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for raw in raw_lines or []:
            handle.write(raw + "\n")
    return path


# ── P5-T1：同步路徑拒絕 async thinker ───────────────────────────────────
class TestP5T1SyncLoopRejectsAsyncThinker:
    """P5-T1 — ``drive_loop()`` 收到 async thinker ⇒ **raise** ``TypeError``
    且**不得**建立任何檔案。"""

    def test_p5_t1_sync_loop_rejects_async_thinker_and_writes_nothing(
        self, tmp_path, enabled
    ):
        _seed_sandbox_data()
        store = tmp_path / "p5_t1_store"
        store.mkdir(parents=True, exist_ok=True)
        with pytest.raises(TypeError) as excinfo:
            tcl.drive_loop("agent_akane", FAKE_NOW, store, thinker=_P5AsyncFakeThinker())
        message = str(excinfo.value)
        assert "drive_loop_async" in message, message
        assert list(store.iterdir()) == [], "拒絕時不得建立任何檔案"


# ── P5-T2：async 路徑拒絕同步 thinker ───────────────────────────────────
class TestP5T2AsyncLoopRejectsSyncThinker:
    """P5-T2 — ``drive_loop_async()`` 收到同步 thinker ⇒ **raise** ``TypeError``
    且**不得**建立任何檔案。"""

    async def test_p5_t2_async_loop_rejects_sync_thinker_and_writes_nothing(
        self, tmp_path, enabled
    ):
        _seed_sandbox_data()
        store = tmp_path / "p5_t2_store"
        store.mkdir(parents=True, exist_ok=True)
        for thinker in (tt.DeterministicMockThinker(), tt.NullThinker(), _P5SyncFakeThinker()):
            with pytest.raises(TypeError) as excinfo:
                await tcl.drive_loop_async("agent_akane", FAKE_NOW, store, thinker=thinker)
            message = str(excinfo.value)
            assert "drive_loop" in message, message
            assert list(store.iterdir()) == [], "拒絕時不得建立任何檔案"


# ── P5-T3：async 端到端 ────────────────────────────────────────────────
class TestP5T3AsyncEndToEnd:
    """P5-T3 — 注入假 async thinker ⇒ 沙盒 ``working_state.jsonl`` **+1**、
    ``epistemic_tensions.jsonl`` **+1**、事件狀態分流正確。"""

    async def test_p5_t3_async_loop_writes_one_row_and_one_event(self, sandbox_store, enabled):
        _seed_sandbox_data()  # 2 個資訊缺口
        thinker = _P5AsyncFakeThinker(decided=False, remaining_gap_count=1)
        trace = await tcl.drive_loop_async(
            "agent_akane", FAKE_NOW, sandbox_store, thinker=thinker
        )
        assert trace is not None, "async 閉環應產出 trace"

        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert len(rows) == 1, f"working state 應恰好 1 筆，實得 {len(rows)}"
        events = _read_jsonl(sandbox_store / tcl.TENSION_STORE_FILENAME)
        assert len(events) == 1, f"沙盒張力承載應恰好 1 筆，實得 {len(events)}"

        row = rows[0]
        assert set(row.keys()) == set(tcl.WORKING_STATE_FIELDS), row.keys()
        assert row["partial_resolution"] == pytest.approx(0.5)
        assert row["remaining_gap_count"] == 1
        assert row["decided"] is False

        event = events[0]
        assert set(event.keys()) == set(TENSION_EVENT_FIELDS), event.keys()
        assert event["status"] == "partially_resolved"
        assert event["payload"]["decided"] is False
        assert event["payload"]["remaining_gap_count"] == 1
        assert event["payload"]["partial_resolution"] == pytest.approx(0.5)
        assert event["payload"]["reason"] == "p5_fake_outcome"

    async def test_p5_t3_async_loop_routes_a_decided_outcome_to_resolved(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        thinker = _P5AsyncFakeThinker(decided=True, remaining_gap_count=0, partial_resolution=1.0)
        trace = await tcl.drive_loop_async(
            "agent_akane", FAKE_NOW, sandbox_store, thinker=thinker
        )
        assert trace is not None
        events = _read_jsonl(sandbox_store / tcl.TENSION_STORE_FILENAME)
        assert len(events) == 1
        assert events[0]["status"] == "resolved"
        assert events[0]["payload"]["decided"] is True
        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert rows[0]["decided"] is True
        assert rows[0]["remaining_gap_count"] == 0


# ── P5-T4：兩路徑產出逐位元相同 ─────────────────────────────────────────
class TestP5T4BothPathsAreByteIdentical:
    """P5-T4 — 同一輸入、同一 outcome ⇒ 同步路徑與 async 路徑寫出的
    working_state ／張力事件**逐位元相同**。"""

    async def test_p5_t4_sync_and_async_paths_write_byte_identical_files(
        self, tmp_path, enabled
    ):
        _seed_sandbox_data()
        sync_store = tmp_path / "p5_t4_sync"
        sync_store.mkdir(parents=True, exist_ok=True)
        async_store = tmp_path / "p5_t4_async"
        async_store.mkdir(parents=True, exist_ok=True)

        sync_trace = tcl.drive_loop(
            "agent_akane", FAKE_NOW, sync_store, thinker=_P5SyncFakeThinker()
        )
        async_trace = await tcl.drive_loop_async(
            "agent_akane", FAKE_NOW, async_store, thinker=_P5AsyncFakeThinker()
        )
        assert sync_trace is not None and async_trace is not None

        for name in (tcl.WORKING_STATE_FILENAME, tcl.TENSION_STORE_FILENAME):
            sync_bytes = (sync_store / name).read_bytes()
            async_bytes = (async_store / name).read_bytes()
            assert sync_bytes == async_bytes, (
                f"{name} 兩條路徑必須逐位元相同\n"
                f"sync={sync_bytes!r}\nasync={async_bytes!r}"
            )
        # trace 也必須逐位元相同（**除路徑欄位**——兩條路徑用的是不同的沙盒目錄，
        # 路徑不同是**設計使然**，不是資料面差異）。
        sync_dict = dict(sync_trace.to_dict())
        async_dict = dict(async_trace.to_dict())
        for key in ("tension_store_path", "working_state_path"):
            sync_dict.pop(key, None)
            async_dict.pop(key, None)
        assert sync_dict == async_dict, "trace（除路徑欄位外）也必須逐位元相同"


# ── P5-T5：稽核錨點（由送出者回報）────────────────────────────────────
class TestP5T5AuditAnchorComesFromTheSender:
    """P5-T5 — ``working_state.think_prompt_sha256`` ＝ **thinker 回報的** sha256
    ⇒ **不得**是 Phase 2 提示詞的 sha256（接收者不得自行重算）。"""

    async def test_p5_t5_working_state_records_the_sender_reported_sha(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        thinker = _P5AsyncFakeThinker()
        await tcl.drive_loop_async("agent_akane", FAKE_NOW, sandbox_store, thinker=thinker)

        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert len(rows) == 1
        recorded = rows[0]["think_prompt_sha256"]

        sent_prompt = thinker.seen_prompts[0]
        assert sent_prompt, "閉環必須把提示詞交給思考器"
        expected = _p5_fake_sent_sha(sent_prompt)
        phase2_sha = hashlib.sha256(sent_prompt.encode("utf-8")).hexdigest()

        assert recorded == expected, "錨點必須是**送出者回報**的 sha256"
        assert recorded != phase2_sha, "不得是 Phase 2 提示詞的 sha256（指錯對象）"
        assert len(recorded) == 64
        int(recorded, 16)  # 必須是合法 hex

    async def test_p5_t5_sync_path_takes_the_same_sender_reported_value(
        self, sandbox_store, enabled
    ):
        _seed_sandbox_data()
        thinker = _P5SyncFakeThinker()
        tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store, thinker=thinker)
        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert rows[0]["think_prompt_sha256"] == _p5_fake_sent_sha(thinker.seen_prompts[0])


# ── P5-T6：ThinkOutcome 相容性 ─────────────────────────────────────────
class TestP5T6ThinkOutcomeCompatibility:
    """P5-T6 — 未填 ``think_prompt_sha256`` 時（預設 ``""``）**不得** raise
    既有呼叫點。"""

    def test_p5_t6_think_outcome_without_the_sha_field_defaults_to_empty(self):
        outcome = tt.ThinkOutcome(
            partial_resolution=0.5,
            remaining_gap_count=1,
            decided=False,
            reason="legacy_call_site",
        )
        assert outcome.think_prompt_sha256 == ""
        assert outcome.reason == "legacy_call_site"

    def test_p5_t6_existing_call_sites_still_work_end_to_end(self, sandbox_store, enabled):
        _seed_sandbox_data()

        class _LegacyThinker:
            """**不填**新欄位的舊式思考器（模擬既有呼叫點）。"""

            def think(self, req: Any, prompt: str) -> Any:
                return tt.ThinkOutcome(
                    partial_resolution=0.5,
                    remaining_gap_count=1,
                    decided=False,
                    reason="legacy_thinker",
                )

        trace = tcl.drive_loop("agent_akane", FAKE_NOW, sandbox_store, thinker=_LegacyThinker())
        assert trace is not None, "未填新欄位不得 raise"
        rows = _read_jsonl(sandbox_store / tcl.WORKING_STATE_FILENAME)
        assert len(rows) == 1
        # 送出者未回報 ⇒ 誠實留白（不猜、不重算）。
        assert rows[0]["think_prompt_sha256"] == ""

    def test_p5_t6_working_state_field_count_is_unchanged(self):
        assert len(tcl.WORKING_STATE_FIELDS) == 13, "欄位數維持 13（語意變更，不是新增欄位）"
        assert "think_prompt_sha256" in tcl.WORKING_STATE_FIELDS


# ── P5-T7：介面：無檔案 ────────────────────────────────────────────────
class TestP5T7InterfaceWithoutFile:
    """P5-T7 — 檔案不存在 ⇒ 回**空 list**（不得 raise、不得建立檔案）。"""

    def test_p5_t7_interface_returns_empty_list_when_no_file_exists(self, tmp_path):
        store = tmp_path / "p5_t7_store"
        store.mkdir(parents=True, exist_ok=True)
        assert tcl.format_working_state_lines("agent_akane", store) == []
        assert list(store.iterdir()) == [], "唯讀介面不得建立任何檔案"

    def test_p5_t7_interface_returns_empty_list_when_the_file_is_empty(self, tmp_path):
        store = tmp_path / "p5_t7_empty_store"
        store.mkdir(parents=True, exist_ok=True)
        (store / tcl.WORKING_STATE_FILENAME).write_text("", encoding="utf-8")
        assert tcl.format_working_state_lines("agent_akane", store) == []


# ── P5-T8：介面：已想完 ────────────────────────────────────────────────
class TestP5T8InterfaceWithDecidedRecord:
    """P5-T8 — 該筆 ``decided=True`` ⇒ 回**空 list**。"""

    def test_p5_t8_decided_record_yields_empty_list(self, tmp_path):
        store = tmp_path / "p5_t8_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(
            store,
            [
                _p5_working_state_row(decided=False, remaining_gap_count=2),
                _p5_working_state_row(decided=True, remaining_gap_count=0, partial_resolution=1.0),
            ],
        )
        assert tcl.format_working_state_lines("agent_akane", store) == []


# ── P5-T9：介面：未解張力 ──────────────────────────────────────────────
class TestP5T9InterfaceWithUnresolvedTension:
    """P5-T9 — ``decided=False`` ⇒ 回**一行**、格式 ``- [YYYY-MM-DD 張力未解] …``、
    長度 ≤ 60。"""

    def test_p5_t9_unresolved_record_yields_one_formatted_line(self, tmp_path):
        store = tmp_path / "p5_t9_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(
            store,
            [
                _p5_working_state_row(
                    decided=False, remaining_gap_count=3, partial_resolution=0.25
                )
            ],
        )
        lines = tcl.format_working_state_lines("agent_akane", store)
        assert len(lines) == 1, f"應恰好 1 行，實得 {lines}"
        line = lines[0]
        expected_date = datetime.fromtimestamp(FAKE_NOW, tz=timezone.utc).strftime("%Y-%m-%d")
        assert line == f"- [{expected_date} 張力未解] 還有 3 個未解的問題（已想過 25%）", line
        assert len(line) <= 60, f"長度 {len(line)} 不得大於 60"

    def test_p5_t9_long_lines_are_truncated_to_max_chars(self, tmp_path):
        store = tmp_path / "p5_t9_long_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(
            store,
            [
                _p5_working_state_row(
                    decided=False,
                    remaining_gap_count=123456789012345678901234567890,
                    partial_resolution=0.123456,
                )
            ],
        )
        line = tcl.format_working_state_lines("agent_akane", store)[0]
        assert len(line) == 60, f"截斷後長度應為 60，實得 {len(line)}"
        assert line.endswith("..."), line

    def test_p5_t9_max_entries_caps_the_output(self, tmp_path):
        store = tmp_path / "p5_t9_cap_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(
            store, [_p5_working_state_row(decided=False, remaining_gap_count=1)]
        )
        assert tcl.format_working_state_lines("agent_akane", store, max_entries=0) == []
        assert len(tcl.format_working_state_lines("agent_akane", store, max_entries=5)) == 1


# ── P5-T10：介面：壞行 ─────────────────────────────────────────────────
class TestP5T10InterfaceSkipsBadLines:
    """P5-T10 — 檔案含壞行 ⇒ 跳過該行、不 raise。"""

    def test_p5_t10_bad_lines_are_skipped_without_raising(self, tmp_path):
        store = tmp_path / "p5_t10_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(
            store,
            [_p5_working_state_row(decided=False, remaining_gap_count=4)],
            raw_lines=["{not json at all", "[1, 2, 3]", '"a string"', "null"],
        )
        lines = tcl.format_working_state_lines("agent_akane", store)
        assert len(lines) == 1, f"壞行應被跳過，實得 {lines}"
        assert "還有 4 個未解的問題" in lines[0]

    def test_p5_t10_a_file_with_only_bad_lines_yields_empty_list(self, tmp_path):
        store = tmp_path / "p5_t10_only_bad_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(store, [], raw_lines=["{not json", "}}}", "[]"])
        assert tcl.format_working_state_lines("agent_akane", store) == []


# ── P5-T11：介面只讀 ───────────────────────────────────────────────────
class TestP5T11InterfaceIsReadOnly:
    """P5-T11 — 呼叫前後 ``working_state.jsonl`` 與 ``epistemic_tensions.jsonl``
    **位元不變**。"""

    def test_p5_t11_interface_does_not_modify_any_file(self, tmp_path):
        store = tmp_path / "p5_t11_store"
        store.mkdir(parents=True, exist_ok=True)
        _p5_write_working_state(
            store, [_p5_working_state_row(decided=False, remaining_gap_count=1)]
        )
        tension_path = store / tcl.TENSION_STORE_FILENAME
        tension_path.write_text('{"kind": "tension_resolved"}\n', encoding="utf-8")

        before = {p.name: p.read_bytes() for p in store.iterdir() if p.is_file()}
        assert set(before) == {tcl.WORKING_STATE_FILENAME, tcl.TENSION_STORE_FILENAME}

        lines = tcl.format_working_state_lines("agent_akane", store)
        assert len(lines) == 1

        after = {p.name: p.read_bytes() for p in store.iterdir() if p.is_file()}
        assert after == before, "唯讀介面不得改動任何位元組"
        assert set(after) == set(before), "唯讀介面不得建立新檔案"


# ── P5-T12：介面不觸碰生產 ─────────────────────────────────────────────
class TestP5T12InterfaceRefusesProductionStoreDir:
    """P5-T12 — 傳入不合格的 ``store_dir``（生產路徑）⇒ 回空 list、**不得** raise、
    **不得**寫入生產。"""

    def test_p5_t12_production_data_root_store_dir_yields_empty_list(self):
        prod_store = Path(data_root()) / "inner_life"
        prod_store.mkdir(parents=True, exist_ok=True)
        before = {p.name: p.read_bytes() for p in prod_store.iterdir() if p.is_file()}
        names_before = sorted(p.name for p in prod_store.iterdir())

        assert tcl.format_working_state_lines("agent_akane", prod_store) == []

        after = {p.name: p.read_bytes() for p in prod_store.iterdir() if p.is_file()}
        assert after == before, "生產檔案位元不得改變"
        assert sorted(p.name for p in prod_store.iterdir()) == names_before

    def test_p5_t12_repo_data_store_dir_yields_empty_list(self):
        repo_data = REPO_ROOT / "data"
        before = {
            path.relative_to(repo_data).as_posix(): path.read_bytes()
            for path in repo_data.rglob("*")
            if path.is_file()
        }
        assert tcl.format_working_state_lines("agent_akane", repo_data) == []
        after = {
            path.relative_to(repo_data).as_posix(): path.read_bytes()
            for path in repo_data.rglob("*")
            if path.is_file()
        }
        assert after == before, "repo data/ 不得被改動"
