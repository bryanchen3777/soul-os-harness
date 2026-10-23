# tests/inner_life/test_tension_closure_loop.py
"""CAMPAIGN-B-P1 §4.1 T7/T8/T9/T10 — 短期思考閉環（沙盒原型 ＋ 隔離 ＋ 開關）。

工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md

覆蓋：
  - **T7** 閉環三段可觀測（沙盒 ``working_state.jsonl`` 恰好 +1 筆且欄位齊全；
    沙盒 ``epistemic_tensions.jsonl`` 恰好 +1 筆，且**頂層欄位形狀與生產一致**）。
  - **T8** 沙盒隔離（閉環執行前後**生產** ``data/inner_life/epistemic_tensions.jsonl``
    的 bytes 恆等）。
  - **T9** 開關預設 OFF（``feature_enabled()`` 預設 ``False``；OFF 呼叫
    ``drive_loop`` ⇒ 不建立任何檔案、回 ``None``）。
  - **T10** 拒絕生產 ``store_dir``（⇒ 拒絕執行、回 ``None``、且生產檔案 bytes 恆等）。

沙盒：線頭檔與關係檔寫進 ``SOUL_OS_DATA_DIR`` 指向的**隔離 tmp 資料根**
（``tests/conftest.py`` 的 autouse fixture）；輸出只寫 ``tmp_path`` 下的沙盒。

🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.inner_life import tension_closure_loop as tcl
from src.inner_life.epistemic_action import TENSION_EVENT_FIELDS
from src.paths import data_root

REPO_ROOT = Path(__file__).resolve().parents[2]

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


def _seed_sandbox_data(agent_id: str = "agent_akane") -> None:
    """把線頭檔與關係檔寫進**隔離**資料根（沙盒；不碰生產）。"""
    agent_dir = Path(data_root()) / "soul" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for row in SANDBOX_ROWS:
        item = dict(row)
        item.setdefault("created_at", item["updated_at"])
        item.setdefault("dissolved_at", None)
        rows.append(item)
    (agent_dir / "life_threads.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
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
        assert event["status"] == "resolved"
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
