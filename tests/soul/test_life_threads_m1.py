# tests/soul/test_life_threads_m1.py
# LIFE-THREAD-M1-1 — 模組 1 契約可測斷言（規格唯一來源：
# `docs/LIFE-THREAD-ENGINE-CONTRACT.md` §2 / §9 / §10.2 / §12）。
#
# 隔離：`tests/conftest.py` 的 autouse fixture 已把 SOUL_OS_DATA_DIR 指向 per-test
# tmp 無菌室；本檔另以 monkeypatch.setenv + reset_data_root() 顯式隔離。
# **0 生產 `data/**` 寫入**。
from __future__ import annotations

import ast
import inspect
import json
import os
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_threads.py"
AGENT_A = "agent_ruka"
AGENT_B = "agent_yua"

_NARRATIVE = "早上開窗發現風變涼了，想起陽台那盆枯掉的薄荷，決定今天把它救回來。"


# ──────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def soul_env(tmp_path, monkeypatch):
    """顯式隔離：資料根 → tmp（0 生產寫入）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()
    try:
        yield tmp_path / "data"
    finally:
        monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=False)
        reset_data_root()


def _path(agent_id: str) -> Path:
    return lt.life_threads_path(agent_id)


def _lines(agent_id: str) -> list[str]:
    p = _path(agent_id)
    if not p.exists():
        return []
    raw = p.read_bytes().decode("utf-8")
    assert not raw.startswith("\ufeff"), "life_threads.jsonl 不得有 BOM"
    assert "\r" not in raw, "life_threads.jsonl 必須 LF 行尾"
    return [ln for ln in raw.split("\n") if ln != ""]


def _raw_rows(agent_id: str) -> list[dict]:
    return [json.loads(ln) for ln in _lines(agent_id)]


def _write_rows(agent_id: str, rows: list) -> Path:
    """測試夾具用：直接寫入原始列（含壞行時以 str 給）。"""
    p = _path(agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(row if isinstance(row, str) else json.dumps(row, ensure_ascii=False))
            f.write("\n")
    return p


def _mk(agent_id: str = AGENT_A, **kw) -> str:
    defaults = dict(
        title="想把陽台那盆枯掉的薄荷救回來",
        narrative_content=_NARRATIVE,
        origin_type="world_collision",
    )
    defaults.update(kw)
    tid = lt.create_thread(agent_id, **defaults)
    assert tid is not None, "create_thread 應成功"
    return tid


# ══════════════════════════════════════════════════════════════
# §2.1 落盤 / 隔離 / 編碼
# ══════════════════════════════════════════════════════════════

def test_s2_1_path_uses_data_root(soul_env):
    """路徑 = data_root()/soul/<agent_id>/life_threads.jsonl，且經 data_root() 組出。"""
    tid = _mk()
    p = _path(AGENT_A)
    assert p == data_root() / "soul" / AGENT_A / "life_threads.jsonl"
    assert p.exists()
    # 原始碼不得硬編碼 data/ 字面路徑
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert '"data/' not in src and "'data/" not in src
    assert "data_root()" in src
    assert lt.fold(AGENT_A)[tid]["status"] == "active"


def test_s2_1_encoding_utf8_no_bom_lf_ensure_ascii_false(soul_env):
    """UTF-8 無 BOM、LF 行尾、非 ASCII 不被轉義（ensure_ascii=False）。"""
    _mk(title="薄荷與涼風", narrative_content="風變涼了，薄荷枯了，我想救它。")
    raw = _path(AGENT_A).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert "薄荷" in text  # ensure_ascii=False
    assert "\\u" not in text


def test_s2_1_module_not_imported_by_production_paths():
    """M1 是獨立落地（§10.2 / 紅線 4）：0 既有**生產模組**介接。

    以 AST 掃描 `src/**` + `scripts/**`，除「生活線頭引擎自身、尚未接線的模組」外，
    任何 import 本模組者必須是 0。

    ⚠️ 例外更正（LIFE-THREAD-M4-1，2026-09-14）：本測試原先把不變量寫成「**任何**
    importer 皆為 0」，那比契約更嚴且與契約**直接互斥**——
    `docs/LIFE-THREAD-ENGINE-CONTRACT.md` §10.1 `:701-720` 的依賴圖逐字指定
    「**M4 起源注入 (寫 M1)**」，`:722-726` 再明列 **M5 依賴 M1+M4**
    ⇒「M4 → 寫 M1」是**契約欽定的方向**；契約要防的是**生產路徑拉線**（M5 接線），
    不是 M4 import M1。故掃描排除「生活線頭引擎自身、尚未接線的模組」
    （M1 自身 ＋ M4 `src/soul/life_thread_origins.py`）。

    🔒 不變量**未被放寬**：排除後，其餘 `src/**` ＋ `scripts/**` 對本模組的 importer
    仍必須 ＝ **0**；且 M4 自身另有測試釘死
    `git grep life_thread_origins -- src scripts configs` ＝ **0 命中**
    （即「**M4 沒有任何生產路徑 import**」照樣成立）。

    ⚠️ 例外追加（LIFE-THREAD-M5，2026-09-14）：M5 的職責**就是**把生活線頭引擎
    接進生產路徑，其介接層 `src/soul/life_thread_orchestrator.py` 必然 import M1
    （依賴圖 §10.1 `:722-726`「M5 依賴 M1+M4」）。故白名單加入 orchestrator，
    **其餘排除集不變** —— 排除後仍有任何其他 importer ⇒ 紅。
    """
    engine_own_unwired = {
        MODULE_PATH,
        _REPO_ROOT / "src" / "soul" / "life_thread_origins.py",  # M4：§10.1 允許寫 M1
        # M5：§10.1 的介接層，唯一合法的生產接線點
        _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py",
    }
    offenders = []
    for root in (_REPO_ROOT / "src", _REPO_ROOT / "scripts"):
        for py in root.rglob("*.py"):
            if py in engine_own_unwired:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.endswith("life_threads"):
                            offenders.append(str(py))
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod.endswith("life_threads") or any(
                        a.name == "life_threads" for a in node.names
                    ):
                        offenders.append(str(py))
    assert offenders == [], (
        "life_threads 被「生活線頭引擎自身」以外的模組 import"
        f"（§10.1 只允許 M4 寫 M1）：{offenders}"
    )


def test_s2_1_configs_default_yaml_has_no_capacity_key():
    """本票不新增 `life_thread_capacity` 鍵（缺鍵 ⇒ fail-closed 回 2）。"""
    txt = (_REPO_ROOT / "configs" / "default.yaml").read_text(encoding="utf-8")
    assert lt.LIFE_THREAD_CAPACITY_KEY not in txt


# ══════════════════════════════════════════════════════════════
# §2.2 欄位逐一規格
# ══════════════════════════════════════════════════════════════

def test_s2_2_created_entry_fields(soul_env):
    """`created` 事件的共同欄位逐一存在，型別正確。"""
    tid = _mk()
    row = _raw_rows(AGENT_A)[0]
    assert set(row) == {
        "thread_id", "event_seq", "event_type", "origin_type", "status", "title",
        "narrative_content", "created_at", "updated_at", "check_after_ts",
        "share_target", "dissolved_at", "sage_fact_id",
    }
    assert row["thread_id"] == tid and len(tid) == 36
    assert row["event_seq"] == 1 and isinstance(row["event_seq"], int)
    assert row["status"] == "active"
    assert row["origin_type"] == "world_collision"
    assert row["created_at"] == row["updated_at"]
    assert row["check_after_ts"] is None
    assert row["share_target"] == "none"
    assert row["dissolved_at"] is None and row["sage_fact_id"] is None


def test_s2_2_forbidden_fields_absent_in_written_rows(soul_env):
    """§2.2 禁用欄位硬斷言：寫入 dict 的 key 集合 ∩ FORBIDDEN == ∅。"""
    tid = _mk()
    lt.append_updated(AGENT_A, tid, narrative_content="隔天我又想起那盆薄荷，決定先查資料。")
    lt.append_transition(AGENT_A, tid, "completed")
    lt.append_dissolved(AGENT_A, tid, sage_fact_id="fact-1")
    rows = _raw_rows(AGENT_A)
    assert len(rows) == 4
    for row in rows:
        assert not (set(row) & lt.FORBIDDEN_FIELDS), row
    assert lt.FORBIDDEN_FIELDS == {
        "score", "weight", "intensity", "urgency", "priority", "longing", "confidence"
    }
    # 模組原始碼層面：0 個 float 評分欄位寫入
    src = MODULE_PATH.read_text(encoding="utf-8")
    for bad in lt.FORBIDDEN_FIELDS:
        # 模組只允許在 `FORBIDDEN_FIELDS` 常數本身出現這些鍵名（＝斷言用，非寫入用）
        occurrences = src.count(f'"{bad}"')
        assert occurrences <= 1, f"禁用鍵 {bad} 出現 {occurrences} 次（應只作為斷言常數）"


@pytest.mark.parametrize("bad_title", ["", "x" * 41, "   "])
def test_s2_2_title_bounds(soul_env, bad_title):
    """`title` 1–40 字元且去空白後非空。"""
    with pytest.raises(ValueError):
        lt.create_thread(
            AGENT_A, title=bad_title, narrative_content=_NARRATIVE,
            origin_type="whim_driven",
        )
    assert _lines(AGENT_A) == []


def test_s2_2_title_max_len_40_ok(soul_env):
    tid = _mk(title="薄" * 40)
    assert lt.fold(AGENT_A)[tid]["title"] == "薄" * 40


@pytest.mark.parametrize("bad", ["", "   ", "TBD", "N/A", "...", "占位符"])
def test_s2_2_narrative_empty_or_placeholder_rejected(soul_env, bad):
    """`narrative_content` 不得為空字串、不得為佔位符。"""
    with pytest.raises(ValueError):
        lt.create_thread(
            AGENT_A, title="薄荷", narrative_content=bad, origin_type="whim_driven"
        )
    assert _lines(AGENT_A) == []


def test_s2_2_narrative_must_not_equal_title(soul_env):
    with pytest.raises(ValueError):
        lt.create_thread(
            AGENT_A, title="薄荷枯了", narrative_content="薄荷枯了",
            origin_type="whim_driven",
        )
    with pytest.raises(ValueError):
        lt.create_thread(
            AGENT_A, title="薄荷枯了", narrative_content="  薄荷枯了  ",
            origin_type="whim_driven",
        )
    assert _lines(AGENT_A) == []


def test_s2_2_narrative_max_len_600(soul_env):
    ok = "薄荷" + "水" * 598
    assert len(ok) == 600
    _mk(narrative_content=ok)
    with pytest.raises(ValueError):
        lt.create_thread(
            AGENT_A + "_x", title="薄荷", narrative_content="水" * 601,
            origin_type="whim_driven",
        )


@pytest.mark.parametrize("origin", ["goal_driven", "necessity_driven", "whim_driven", "world_collision"])
def test_s2_3_origin_type_enum(soul_env, origin):
    tid = _mk(origin_type=origin)
    assert lt.fold(AGENT_A)[tid]["origin_type"] == origin


def test_s2_3_origin_type_rejects_5th_value(soul_env):
    """§2.3：只有 4 值，不得發明第五個（含「零線頭」不落盤）。"""
    assert lt.ORIGIN_TYPES == (
        "goal_driven", "necessity_driven", "whim_driven", "world_collision"
    )
    for bad in ["", "zero_thread", "relational_driven", "memory_driven", "None"]:
        with pytest.raises(ValueError):
            lt.create_thread(
                AGENT_A, title="薄荷", narrative_content=_NARRATIVE,
                origin_type=bad,
            )
    assert _lines(AGENT_A) == []


@pytest.mark.parametrize("status", ["active", "dormant", "completed", "abandoned"])
def test_s2_2_status_enum(status):
    assert status in lt.STATUS_VALUES
    assert lt.STATUS_VALUES == ("active", "dormant", "completed", "abandoned")


def test_s2_2_status_value_domain_enforced(soul_env):
    """`status` 只接受 4 值：非 4 值的目標狀態被拒絕（且不落盤）。

    註：本票的 `create_thread()` 固定寫 `status="active"`（§2.4：`created` ⇒ active），
    故寫入路徑上唯一可由外部指定的 `status` 是 `append_transition()` 的目標狀態；
    「`created` 帶非 4 值 status」在契約中不可達（YAGNI，不造不可達狀態）。

    註（2026-09-14 協調者修正）：值域驗證走 `_validate_status`（嚴格、raise `ValueError`）；
    而 `append_transition()` 對**非 4 值目標狀態**的行為依 §2.5 —— 視為非法轉移：
    **不寫入 ＋ 一行 WARNING ＋ 回 `False`**，契約逐字規定「**不得 raise、不得中斷呼叫端**」。
    故本測試不得要求 `append_transition` 拋錯。
    """
    assert lt.STATUS_VALUES == ("active", "dormant", "completed", "abandoned")
    for bad in ("dissolved", "", "ACTIVE", None, "pending"):
        with pytest.raises(ValueError):
            lt._validate_status(bad)

    tid = _mk()
    before = _lines(AGENT_A)
    for bad in ("dissolved", "", "ACTIVE", None, "pending"):
        # §2.5：非法轉移 ⇒ 回 False 且**不得 raise**、**不寫入**
        assert lt.append_transition(AGENT_A, tid, bad) is False
    assert _lines(AGENT_A) == before


@pytest.mark.parametrize("target", ["none", "lounge", "user_bryan", "agent_ruka", "agent_x-1"])
def test_s2_2_share_target_value_domain_ok(soul_env, target):
    tid = _mk(share_target=target)
    assert lt.fold(AGENT_A)[tid]["share_target"] == target


@pytest.mark.parametrize("bad", ["", "public", "agent_", "agent_ruka/x", "bryan", 3, None])
def test_s2_2_share_target_rejects_out_of_domain(soul_env, bad):
    with pytest.raises(ValueError):
        lt.create_thread(
            AGENT_A, title="薄荷", narrative_content=_NARRATIVE,
            origin_type="whim_driven", share_target=bad,
        )
    assert _lines(AGENT_A) == []


# ══════════════════════════════════════════════════════════════
# §2.4 append-only ＋ fold 三條規則
# ══════════════════════════════════════════════════════════════

def test_s2_4_fold_rule1_skips_blank_and_broken_lines(soul_env):
    """規則 1：空行與 json.loads 失敗的列跳過，不 crash。

    註（2026-09-14 協調者修正）：`_write_rows` 是**追加**（`open("a")`），而 `_mk()`
    先寫下的 `created` 列其 `updated_at` ＝ 當下時刻。依 §2.4 規則 2，fold 取
    `(updated_at, event_seq)` 字典序最大者，故本測試插入的合法列必須**比它更新**，
    否則勝者會是 `_mk()` 那列（原測試用 2026-09-14T16:00 反而較舊，設置有誤）。
    """
    tid = _mk()
    _write_rows(AGENT_A, [
        "",
        "   ",
        "{not json at all",
        json.dumps({"thread_id": tid, "updated_at": "2099-01-01T00:00:00+00:00",
                    "event_seq": 99, "status": "abandoned"}),
        '["not", "a", "dict"]',
        json.dumps({"thread_id": tid, "updated_at": "2099-01-01T00:00:01+00:00",
                    "event_seq": 100, "status": "dormant"})[:-1],  # 截斷壞行 ⇒ 跳過
    ])
    states = lt.fold(AGENT_A)  # 不得 raise
    assert states[tid]["status"] == "abandoned"  # 壞行被跳過，勝者不受影響


def test_s2_4_fold_rule2_max_updated_at_wins(soul_env):
    """規則 2：`(updated_at, event_seq)` 字典序最大者為當前狀態。"""
    tid = _mk(created_at="2026-09-14T08:00:00+00:00", updated_at="2026-09-14T08:00:00+00:00")
    _write_rows(AGENT_A, [
        {**_raw_rows(AGENT_A)[0], "status": "completed",
         "updated_at": "2026-09-14T20:00:00+00:00", "event_seq": 2},
        {**_raw_rows(AGENT_A)[0], "status": "dormant",
         "updated_at": "2026-09-14T09:00:00+00:00", "event_seq": 3},
    ])
    assert lt.fold(AGENT_A)[tid]["status"] == "completed"


def test_s2_4_fold_rule2_same_second_tiebreak_by_event_seq(soul_env):
    """同秒 `updated_at` 衝突 ⇒ `event_seq` 破除。"""
    tid = _mk(updated_at="2026-09-14T10:00:00+00:00")
    base = _raw_rows(AGENT_A)[0]
    ts = "2026-09-14T10:00:00+00:00"
    _write_rows(AGENT_A, [
        {**base, "status": "completed", "updated_at": ts, "event_seq": 7},
        {**base, "status": "dormant", "updated_at": ts, "event_seq": 2},
        {**base, "status": "active", "updated_at": ts, "event_seq": 5},
    ])
    state = lt.fold(AGENT_A)[tid]
    assert state["status"] == "completed" and state["event_seq"] == 7
    # 生成側：next_event_seq 必須嚴格遞增（同秒也不會撞號）
    assert lt.next_event_seq(AGENT_A, tid) == 8


def test_s2_4_fold_rule3_last_non_null_credential_wins(soul_env):
    """規則 3：`dissolved_at` / `sage_fact_id` 取最後一個非 null 值勝出。"""
    tid = _mk()
    lt.append_transition(AGENT_A, tid, "completed")
    lt.append_dissolved(AGENT_A, tid, dissolved_at="2026-09-14T21:00:00+00:00",
                        sage_fact_id="fact-A")
    lt.append_updated(AGENT_A, tid, narrative_content="溶解後補記：我還是每天去看那盆薄荷。")
    state = lt.fold(AGENT_A)[tid]
    assert state["dissolved_at"] == "2026-09-14T21:00:00+00:00"
    assert state["sage_fact_id"] == "fact-A"


def test_s2_4_fold_rule3_second_dissolve_credentials_win(soul_env):
    tid = _mk()
    lt.append_transition(AGENT_A, tid, "abandoned")
    lt.append_dissolved(AGENT_A, tid, dissolved_at="2026-09-14T21:00:00+00:00",
                        sage_fact_id="fact-A")
    lt.append_dissolved(AGENT_A, tid, dissolved_at="2026-09-14T22:00:00+00:00",
                        sage_fact_id="fact-B")
    state = lt.fold(AGENT_A)[tid]
    assert state["dissolved_at"] == "2026-09-14T22:00:00+00:00"
    assert state["sage_fact_id"] == "fact-B"


def test_s2_4_fold_groups_by_thread_id(soul_env):
    a = _mk(title="薄荷")
    b = _mk(title="修腳踏車")
    states = lt.fold(AGENT_A)
    assert set(states) == {a, b}
    assert lt.active_count(AGENT_A) == 2


def test_s2_4_append_only_existing_lines_never_mutated(soul_env):
    """append-only：先前列的位元組內容逐一不變；單檔永不重寫。"""
    t1 = _mk(title="薄荷")
    snap1 = _path(AGENT_A).read_bytes()
    t2_created = _mk(title="修腳踏車")
    snap2 = _path(AGENT_A).read_bytes()
    assert snap2.startswith(snap1), "既有列位元組內容不得改變"
    lt.append_updated(AGENT_A, t1, narrative_content="我買了新的土，重新種下去。")
    snap3 = _path(AGENT_A).read_bytes()
    assert snap3.startswith(snap2)
    lt.append_transition(AGENT_A, t1, "dormant")
    lt.append_transition(AGENT_A, t1, "active")
    lt.append_transition(AGENT_A, t2_created, "completed")
    lt.append_dissolved(AGENT_A, t2_created, sage_fact_id="fact-B")
    snap4 = _path(AGENT_A).read_bytes()
    assert snap4.startswith(snap2)
    assert snap3 == snap4[: len(snap3)]
    # 每一列都是獨立完整 JSON（單列寫入，非整檔重寫）
    for ln in _lines(AGENT_A):
        assert json.loads(ln)


def test_s2_4_event_seq_monotonic_per_thread(soul_env):
    tid = _mk()
    for _ in range(4):
        lt.append_updated(AGENT_A, tid, narrative_content="又過了一天，薄荷長出新芽了。")
    seqs = [r["event_seq"] for r in _raw_rows(AGENT_A) if r["thread_id"] == tid]
    assert seqs == [1, 2, 3, 4, 5]


# ══════════════════════════════════════════════════════════════
# §2.5 狀態機
# ══════════════════════════════════════════════════════════════

ALL_STATUSES = ["active", "dormant", "completed", "abandoned"]


def test_s2_5_allowed_transition_full_truth_table():
    expected = {
        ("active", "dormant"): True,
        ("active", "completed"): True,
        ("active", "abandoned"): True,
        ("active", "active"): False,
        ("dormant", "active"): True,
        ("dormant", "completed"): True,
        ("dormant", "abandoned"): True,
        ("dormant", "dormant"): False,
        ("completed", "active"): False,
        ("completed", "dormant"): False,
        ("completed", "completed"): False,
        ("completed", "abandoned"): False,
        ("abandoned", "active"): False,
        ("abandoned", "dormant"): False,
        ("abandoned", "completed"): False,
        ("abandoned", "abandoned"): False,
    }
    for (src, dst), want in expected.items():
        assert lt.allowed_transition(src, dst) is want, (src, dst)


def test_s2_5_terminal_states_reject_all_targets():
    """INV-6：`allowed_transition("completed", X) is False` 與
    `("abandoned", X) is False` **對所有 X** 成立。"""
    targets = [*ALL_STATUSES, "dissolved", "", "anything", "ACTIVE", None, 0, 1, True]
    for terminal in ("completed", "abandoned"):
        for x in targets:
            assert lt.allowed_transition(terminal, x) is False, (terminal, x)


def test_s2_5_illegal_transition_no_write_warning_verbatim_false_no_raise(
    soul_env, caplog
):
    """非法轉移＝不寫入 ＋ WARNING 逐字格式 ＋ 回 False ＋ 不 raise。"""
    tid = _mk()
    lt.append_transition(AGENT_A, tid, "completed")
    before = _path(AGENT_A).read_bytes()
    lines_before = len(_lines(AGENT_A))

    with caplog.at_level("WARNING", logger="soul_os.soul.life_threads"):
        caplog.clear()
        result = lt.append_transition(AGENT_A, tid, "active")  # 終態不可復活

    assert result is False
    assert len(_lines(AGENT_A)) == lines_before
    assert _path(AGENT_A).read_bytes() == before
    messages = [r.getMessage() for r in caplog.records]
    expected = f"[LifeThread] 拒絕非法轉移 {tid}: completed → active (ignored)"
    assert expected in messages, messages
    assert lt.WARNING_ILLEGAL_TRANSITION == (
        "[LifeThread] 拒絕非法轉移 {thread_id}: {from_status} → {to_status} (ignored)"
    )


def test_s2_5_illegal_transition_does_not_break_caller(soul_env):
    """不得 raise、不得中斷呼叫端：連續非法轉移後流程照常。"""
    tid = _mk()
    lt.append_transition(AGENT_A, tid, "abandoned")
    for target in ("active", "dormant", "completed", "abandoned"):
        assert lt.append_transition(AGENT_A, tid, target) is False
    lt.append_dissolved(AGENT_A, tid, sage_fact_id="fact-C")
    assert lt.fold(AGENT_A)[tid]["sage_fact_id"] == "fact-C"


def test_s2_5_unknown_thread_transition_rejected(soul_env):
    """不存在的線頭：拒絕、不建檔、不 raise。"""
    assert lt.append_transition(AGENT_A, "9f1c4c1e-3b0a-4a6f-9d2e-7c5b1a0e8d33", "dormant") is False
    assert _lines(AGENT_A) == []


def test_s2_5_dormant_roundtrip_and_revival(soul_env):
    """`active ⇄ dormant` 反覆來回合法；dormant 可被喚回 active。"""
    tid = _mk()
    assert lt.append_transition(AGENT_A, tid, "dormant") is True
    assert lt.active_count(AGENT_A) == 0
    assert lt.append_transition(AGENT_A, tid, "active") is True
    assert lt.active_count(AGENT_A) == 1
    assert lt.append_transition(AGENT_A, tid, "dormant") is True
    assert lt.append_transition(AGENT_A, tid, "active") is True
    assert lt.fold(AGENT_A)[tid]["status"] == "active"


def test_s2_5_transition_events_record_new_status(soul_env):
    tid = _mk()
    lt.append_transition(AGENT_A, tid, "dormant")
    lt.append_transition(AGENT_A, tid, "completed")
    rows = [r for r in _raw_rows(AGENT_A) if r["thread_id"] == tid]
    assert [r["status"] for r in rows] == ["active", "dormant", "completed"]
    assert [r["event_type"] for r in rows] == [
        "created", "transitioned", "transitioned"
    ]


def test_s2_5_terminal_thread_fold_max_row_is_terminal(soul_env):
    """終態不可復活的可審計形式：最大列 `status ∈ {completed, abandoned}`。"""
    for target in ("completed", "abandoned"):
        agent = f"agent_{target}"
        tid = _mk(agent, title=f"薄荷-{target}")
        assert lt.append_transition(agent, tid, target) is True
        assert lt.append_transition(agent, tid, "active") is False
        state = lt.fold(agent)[tid]
        assert state["status"] in lt.TERMINAL_STATUSES
        assert state["check_after_ts"] is None


def test_s2_5_transition_to_terminal_nulls_check_after_ts(soul_env):
    """§2.2：`check_after_ts` 為 null 僅允許終態 ⇒ 轉終態時歸 null。"""
    tid = _mk(check_after_ts="2026-09-15T08:00:00+00:00")
    assert lt.fold(AGENT_A)[tid]["check_after_ts"] == "2026-09-15T08:00:00+00:00"
    lt.append_transition(AGENT_A, tid, "abandoned")
    assert lt.fold(AGENT_A)[tid]["check_after_ts"] is None


# ══════════════════════════════════════════════════════════════
# §2.6 容量防線
# ══════════════════════════════════════════════════════════════

def test_s2_6_1_cap_constants():
    assert lt.LIFE_THREAD_ACTIVE_CAP_MIN == 1
    assert lt.LIFE_THREAD_ACTIVE_CAP_DEFAULT == 2
    assert lt.LIFE_THREAD_ACTIVE_CAP_HARD_MAX == 3


def test_s2_6_2_capacity_defaults_and_bounds():
    """四步規則：缺鍵／壞型別／越界 ⇒ 2；值恆 ∈ [1,3]。"""
    assert lt.capacity("agent_ruka") == 2      # 未宣告
    assert lt.capacity("不存在的 agent") == 2   # 不存在
    assert lt.capacity("") == 2
    for agent in ["agent_ruka", "agent_yua", "agent_aoi", "nobody"]:
        assert 1 <= lt.capacity(agent) <= lt.LIFE_THREAD_ACTIVE_CAP_HARD_MAX


@pytest.mark.parametrize("declared,expected", [
    (1, 1), (2, 2), (3, 3),
    (0, 2), (-1, 2), (4, 2), (99, 2),
    ("2", 2), (2.0, 2), (None, 2), (True, 2), ([2], 2), ({"v": 2}, 2),
])
def test_s2_6_2_capacity_declared_value_rules(tmp_path, declared, expected):
    """per-agent 宣告面的四步判定（以 tmp 覆寫 config，0 生產改動）。"""
    import yaml

    cfg = yaml.safe_load(
        (_REPO_ROOT / "configs" / "default.yaml").read_text(encoding="utf-8")
    )
    for entry in cfg.get("agents", []):
        if entry.get("id") == AGENT_A:
            entry[lt.LIFE_THREAD_CAPACITY_KEY] = declared
    cfg_path = tmp_path / "default.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert lt.capacity(AGENT_A, config_path=str(cfg_path)) == expected


def test_s2_6_2_capacity_ast_does_not_touch_soul_md():
    """AST：`capacity` 函式體**不引用任何 SOUL.md 檔內容**。

    註（2026-09-14 協調者修正）：契約 §2.6.2 的可測斷言針對的是**函式體的行為**
    （不得讀取人格檔），而非「函式內不得出現 SOUL.md 這串字」。原測試掃描全函式
    字面值，會把 `capacity()` 那段**合法引用契約條文**的 docstring 一併算進去而誤判
    （docstring 明寫「本函式不引用任何人格檔」）。故改為**排除 docstring**，並保留
    真正的不變量斷言（不得出現人格檔路徑字面值、不得引用人格載入符號）。
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "capacity"
    )
    docstring = ast.get_docstring(fn)
    # 以「節點身分」排除 docstring（get_docstring 會 dedent，字串比對不可靠）
    _body0 = fn.body[0] if fn.body else None
    _doc_node = (
        _body0.value
        if isinstance(_body0, ast.Expr) and isinstance(_body0.value, ast.Constant)
        and isinstance(_body0.value.value, str)
        else None
    )
    literals = [
        n.value for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not _doc_node
    ]
    assert docstring is not None  # 反向確認：本函式確實有 docstring 被排除
    assert not any(re.search(r"soul\.md|persona|\.md\b", s, re.I) for s in literals), literals
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert names.isdisjoint({"load_persona", "personas", "soul", "SOUL"}), names
    # 來源只允許 configs/ 的 YAML：函式體不得出現任何 .md 路徑字面值
    assert not any(".md" in s.lower() for s in literals), literals


def test_s2_6_3_create_rejected_at_capacity_line_count_unchanged(soul_env):
    """`active_count >= capacity` ⇒ `create_thread()` 回 None 且列數不變。"""
    assert lt.capacity(AGENT_A) == 2
    t1 = _mk(title="薄荷")
    t2 = _mk(title="腳踏車")
    before = _path(AGENT_A).read_bytes()
    lines_before = len(_lines(AGENT_A))

    assert lt.create_thread(
        AGENT_A, title="第三條", narrative_content=_NARRATIVE,
        origin_type="whim_driven",
    ) is None
    assert len(_lines(AGENT_A)) == lines_before
    assert _path(AGENT_A).read_bytes() == before
    assert lt.active_count(AGENT_A) == 2
    assert lt.fold(AGENT_A).keys() == {t1, t2}


def test_s2_6_3_capacity_rejection_logs_o6_line(soul_env, caplog):
    """§9 O6：容量防線真的在擋（觀測行逐字）。"""
    _mk(title="薄荷")
    _mk(title="腳踏車")
    with caplog.at_level("INFO", logger="soul_os.soul.life_threads"):
        caplog.clear()
        lt.create_thread(
            AGENT_A, title="第三條", narrative_content=_NARRATIVE,
            origin_type="whim_driven",
        )
    messages = [r.getMessage() for r in caplog.records]
    assert f"[LifeThread] cap agent={AGENT_A} active=2 cap=2 (create rejected)" in messages
    assert lt.LOG_CAPACITY_REJECTED == (
        "[LifeThread] cap agent={agent_id} active={active} cap={cap} (create rejected)"
    )


def test_s2_6_3_dormant_frees_slot_but_is_revivable(soul_env):
    """dormant 不佔額度，但可被喚回 active。"""
    t1 = _mk(title="薄荷")
    _mk(title="腳踏車")
    assert lt.create_thread(
        AGENT_A, title="第三條", narrative_content=_NARRATIVE, origin_type="whim_driven"
    ) is None
    assert lt.append_transition(AGENT_A, t1, "dormant") is True
    t3 = lt.create_thread(
        AGENT_A, title="第三條", narrative_content=_NARRATIVE, origin_type="whim_driven"
    )
    assert t3 is not None
    assert lt.active_count(AGENT_A) == 2
    # dormant 喚回 ⇒ 額度已滿，第三條仍在
    assert lt.append_transition(AGENT_A, t1, "active") is True
    assert lt.active_count(AGENT_A) == 3
    assert lt.active_count(AGENT_A) <= lt.LIFE_THREAD_ACTIVE_CAP_HARD_MAX


def test_s2_6_3_terminal_frees_slot(soul_env):
    t1 = _mk(title="薄荷")
    _mk(title="腳踏車")
    lt.append_transition(AGENT_A, t1, "completed")
    assert lt.active_count(AGENT_A) == 1
    assert lt.create_thread(
        AGENT_A, title="第三條", narrative_content=_NARRATIVE, origin_type="whim_driven"
    ) is not None


def test_s2_6_3_active_count_never_exceeds_hard_max(soul_env):
    """任何時刻 `active_count(agent_id) <= HARD_MAX`（經公開寫入路徑）。"""
    for i in range(8):
        lt.create_thread(
            AGENT_A, title=f"線頭{i}", narrative_content=_NARRATIVE,
            origin_type="necessity_driven",
        )
        assert lt.active_count(AGENT_A) <= lt.LIFE_THREAD_ACTIVE_CAP_HARD_MAX
    assert lt.active_count(AGENT_A) == lt.capacity(AGENT_A) == 2


def test_s2_6_3_capacity_enforced_before_created_event(soul_env):
    """強制點在寫入 `created` 事件**之前**：拒絕時完全沒有新列，也無部分寫入。"""
    _mk(title="薄荷")
    _mk(title="腳踏車")
    seeded = _path(AGENT_A).read_bytes()
    for i in range(5):
        assert lt.create_thread(
            AGENT_A, title=f"x{i}", narrative_content=_NARRATIVE,
            origin_type="whim_driven",
        ) is None
    assert _path(AGENT_A).read_bytes() == seeded
    assert len(_lines(AGENT_A)) == 2


def test_s2_6_2_hard_max_is_the_ceiling_of_capacity(soul_env):
    """即使 config 宣告越界值，capacity 仍被夾在 [MIN, HARD_MAX]（fail-closed 回 2）。"""
    import yaml

    cfg = yaml.safe_load(
        (_REPO_ROOT / "configs" / "default.yaml").read_text(encoding="utf-8")
    )
    for entry in cfg.get("agents", []):
        if entry.get("id") == AGENT_A:
            entry[lt.LIFE_THREAD_CAPACITY_KEY] = 999
    p = soul_env / "_cfg.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert lt.capacity(AGENT_A, config_path=str(p)) == 2


# ══════════════════════════════════════════════════════════════
# 溶解事件的寫入路徑與讀取語意（M2 觸發；M1 只提供語意）
# ══════════════════════════════════════════════════════════════

def test_dissolved_write_path_and_fold_semantics(soul_env):
    tid = _mk()
    # 非終態不得溶解（§3.1：不在終態前溶解）
    assert lt.append_dissolved(AGENT_A, tid, sage_fact_id="fact-X") is False
    assert len(_lines(AGENT_A)) == 1
    lt.append_transition(AGENT_A, tid, "completed")
    assert lt.append_dissolved(
        AGENT_A, tid, dissolved_at="2026-09-14T21:00:00+00:00", sage_fact_id="fact-X"
    ) is True
    row = _raw_rows(AGENT_A)[-1]
    assert row["event_type"] == "dissolved"
    assert row["status"] == "completed"
    assert row["dissolved_at"] == "2026-09-14T21:00:00+00:00"
    assert row["sage_fact_id"] == "fact-X"
    assert not (set(row) & lt.FORBIDDEN_FIELDS)


def test_dissolved_no_llm_no_sage_import():
    """§10.2：M1 是 0 LLM 成本模組 —— 不 import LLM／SAGE／排程面。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    banned = ("llm", "sage", "memory", "scheduler", "agency", "asyncio", "threading")
    for mod in imported:
        top = mod.split(".")[-1] if mod.startswith("src.") else mod
        assert not any(b in top.lower() for b in banned), f"不得 import {mod}"
    assert "Proxy" not in src and "add_fact" not in src


# ══════════════════════════════════════════════════════════════
# per-agent 隔離（INV-1 / INV-4）
# ══════════════════════════════════════════════════════════════

def test_inv1_inv4_per_agent_isolation(soul_env):
    """兩個 agent 的檔互不影響。"""
    ta = _mk(AGENT_A, title="薄荷")
    tb = _mk(AGENT_B, title="咖啡機")
    assert _path(AGENT_A) != _path(AGENT_B)
    assert set(lt.fold(AGENT_A)) == {ta}
    assert set(lt.fold(AGENT_B)) == {tb}
    lt.append_transition(AGENT_A, ta, "completed")
    assert lt.fold(AGENT_B)[tb]["status"] == "active"
    assert lt.active_count(AGENT_B) == 1 and lt.active_count(AGENT_A) == 0
    assert ta not in _lines(AGENT_B)


def test_inv4_api_requires_single_agent_id_and_blocks_traversal(soul_env):
    """API 不接受跨 agent 讀寫：agent_id 必須是單一安全路徑段。"""
    for bad in ["../agent_yua", "agent_yua/../../etc", "a/b", "a\\b", "", None, 5, ".."]:
        with pytest.raises(ValueError):
            lt.life_threads_path(bad)
        with pytest.raises(ValueError):
            lt.create_thread(
                bad, title="薄荷", narrative_content=_NARRATIVE, origin_type="whim_driven"
            )
    assert lt.read_entries("../agent_yua") == []
    assert lt.fold("../agent_yua") == {}
    assert lt.active_count("../agent_yua") == 0


def test_inv4_isolation_not_delegated_to_identity_firewall():
    """§2.1：per-agent 隔離**不得依賴** `IdentityFirewall`。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "IdentityFirewall" not in src
    assert "identity_firewall" not in src


def test_inv4_no_cross_agent_file_touch(soul_env):
    """寫 agent A 不得產生／修改 agent B 的檔。"""
    _mk(AGENT_A)
    assert not _path(AGENT_B).exists()
    bdir = _path(AGENT_B).parent
    assert not bdir.exists()


# ══════════════════════════════════════════════════════════════
# fail-silent（INV-5 精神）
# ══════════════════════════════════════════════════════════════

def test_fail_silent_missing_file(soul_env):
    assert lt.read_entries(AGENT_A) == []
    assert lt.fold(AGENT_A) == {}
    assert lt.active_count(AGENT_A) == 0
    assert lt.get_state(AGENT_A, "nope") is None
    assert lt.list_active(AGENT_A) == []


def test_fail_silent_bad_json_only(soul_env):
    _write_rows(AGENT_A, ["{{{", "", "null", "3", "[]"])
    assert lt.read_entries(AGENT_A) == []
    assert lt.fold(AGENT_A) == {}
    assert lt.active_count(AGENT_A) == 0


def test_fail_silent_readonly_directory(soul_env, monkeypatch):
    """唯讀目錄 ⇒ create_thread 回 None、不 crash 主流程。"""
    target = _path(AGENT_A)
    target.parent.mkdir(parents=True, exist_ok=True)

    real_open = Path.open

    def _boom(self, *a, **kw):
        if self == target:
            raise PermissionError("read-only")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", _boom)
    assert lt.create_thread(
        AGENT_A, title="薄荷", narrative_content=_NARRATIVE, origin_type="whim_driven"
    ) is None
    assert lt.read_entries(AGENT_A) == []


def test_fail_silent_unreadable_file(soul_env, monkeypatch):
    target = _path(AGENT_A)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")

    real_open = Path.open

    def _boom(self, *a, **kw):
        if self == target:
            raise OSError("unreadable")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", _boom)
    assert lt.read_entries(AGENT_A) == []
    assert lt.active_count(AGENT_A) == 0


def test_fail_silent_appends_to_unknown_thread(soul_env):
    """對不存在的線頭 append：回 False、不建檔、不 raise。"""
    assert lt.append_updated(AGENT_A, "missing-id") is False
    assert lt.append_dissolved(AGENT_A, "missing-id") is False
    assert _lines(AGENT_A) == []


# ══════════════════════════════════════════════════════════════
# 紅線：0 生產 data/** 寫入
# ══════════════════════════════════════════════════════════════

def test_no_production_data_write(soul_env):
    """資料根必須落在 tmp（非生產 repo `data/`）。"""
    root = data_root()
    assert str(root) != str((_REPO_ROOT / "data").resolve())
    _mk()
    assert str(_path(AGENT_A)).startswith(str(root))
    assert os.environ.get("SOUL_OS_DATA_DIR")
