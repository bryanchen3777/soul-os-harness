# tests/harness/test_tension_open_idempotent_fix.py
# FEAT-TENSION-OPEN-IDEMPOTENT-FIX（二階段實作）的新守門：G1–G3。
#
# 背景（一階段排查）：生產 E7（tension_thought_enqueued，status 硬寫 "resolved"）
# 的回聲被 kind-blind 的 fold／守門當成終態 ⇒ E8（同 id 重開）在窗口外被誤放行
# （狀態漂白）。修復 F1–F3（src/inner_life/epistemic_action.py）：
#   F1 ＝ _fold_tension_events 按 kind 跳過 thought 註記；
#   F2 ＝ _guard_opened_events 經 _prior_state_event 取末筆「狀態型」事件，
#        batch_state 亦不記錄 thought；
#   F3 ＝ record_thought_enqueued 的 status 改為自帶非終態標籤 "thought"
#       （TENSION_STATUS_THOUGHT，刻意不進 TENSION_STATUSES 三元組）。
#
# 本檔三組守門（G0 沿用既有 tests/inner_life/test_epistemic_action.py:1028，
# 不在此重建）：
#   G1 ＝ 原語級（fold／guard／API 狀態形／不多擋）；
#   G2 ＝ tmp 端到端（含舊式 thought 回聲序列與 E8 形序列）；
#   G3 ＝ 讀端（有／無 resolve 序列的末筆狀態語意）。
#
# 變異錨點（M-a–c，走本檔的載入點覆寫跑，repo 檔零寫入）：
#   M-a ＝ 拿掉 fold 的 kind 過濾 ⇒ 同時致盲 F1／F2 ⇒ 必紅 G1／G2（／G3b）；
#   M-b ＝ thought status 改回 "resolved" ⇒ 必紅 G1c／G2b（狀態形）；
#   M-c ＝ 拿掉 batch_state 去重 ⇒ 必紅既有 old-G3
#          （test_g3_same_id_inside_one_batch_is_written_once）。
#
# 全部離線；唯一的文件系統寫入發生在 tmp_path 內，0 生產寫入、
# 不綁任何服務埠、不發真實請求。
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 變異 harness 的**載入點覆寫**環境變數（🔴 只接受 ``%TEMP%`` 底下的路徑）。
#: 慣例仿 tests/inner_life/test_epistemic_action.py 的 MUTATION_MODULE_ENV。
MUTATION_MODULE_ENV = "SOUL_OS_EPISTEMIC_ACTION_MODULE_OVERRIDE"


def _resolve_override_path(raw: str) -> Path:
    """把載入點覆寫路徑解析成絕對路徑；**不在 ``%TEMP%`` 底下 ⇒ 一律拒絕**。"""
    target = Path(raw).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not target.is_relative_to(temp_root):
        raise RuntimeError(
            f"載入點覆寫只接受 %TEMP% 底下的路徑（temp_root={temp_root}），收到：{target}"
        )
    return target


def _load_epistemic_action():
    """載入受測模組（預設＝repo 正式檔；變異時＝``%TEMP%`` 下的改壞副本）。"""
    raw = os.environ.get(MUTATION_MODULE_ENV)
    if not raw:
        from src.inner_life import epistemic_action

        return epistemic_action
    target = _resolve_override_path(raw)
    spec = importlib.util.spec_from_file_location("src.inner_life.epistemic_action", target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法由 {target} 建立 module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["src.inner_life.epistemic_action"] = module
    spec.loader.exec_module(module)
    return module


ea = _load_epistemic_action()


# ── 測試用的小工具 ────────────────────────────────────────────────────
def _tension(**overrides) -> ea.Tension:
    base = dict(
        tension_id="epistemic_gap:fix0001",
        kind=ea.TENSION_KIND_EPISTEMIC_GAP,
        agent_id="agent_rem",
        subject="發熱的東西",
        summary="還不認得「發熱的東西」這件事",
        tags=("發熱", "東西"),
        world_keys=("發熱的東西",),
        source=ea.SOURCE_EPISTEMIC_APPRAISAL,
        created_at=1000.0,
        utterance="主人買了一個會發熱的東西",
    )
    base.update(overrides)
    return ea.Tension(**base)


def _legacy_thought_event(tension: ea.Tension, ts: float) -> dict:
    """舊式 thought 回聲（生產 E7 形：kind＝thought，status 冒充 "resolved"）。"""
    payload = tension.to_dict()
    payload["thought_payload"] = "回聲小念頭"
    payload["thought_tags"] = []
    return {
        "ts": ts,
        "agent_id": tension.agent_id,
        "tension_id": tension.tension_id,
        "kind": ea.TENSION_EVENT_THOUGHT_ENQUEUED,
        "subject": tension.subject,
        "status": "resolved",
        "payload": payload,
    }


def _write_events(store_dir: Path, events) -> Path:
    """把事件寫成 append-only 承載檔（**與生產同一個檔名**、同一種寫法）。"""
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / ea.TENSION_STORE_EVENTS_FILENAME
    blob = "".join(
        json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n" for event in events
    )
    path.write_text(blob, encoding="utf-8")
    return path


def _read_events(store_dir: Path) -> list:
    path = store_dir / ea.TENSION_STORE_EVENTS_FILENAME
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _opened_events(store_dir: Path) -> list:
    """承載檔內的 ``tension_opened`` 事件（開立事件清單）。"""
    return [e for e in _read_events(store_dir) if e.get("kind") == ea.TENSION_EVENT_OPENED]


# ── G1：原語級（fold／guard／API 狀態形／不多擋）───────────────────────
def test_g1a_fold_ignores_thought_events():
    """G1-A — fold 按 kind 分流：thought 註記永不更新每 id 的狀態。

    序列 [opened, 舊式 thought(status="resolved")] ⇒ fold 結果仍是 opened
    那一筆（kind＝tension_opened）。M-a（拿掉 kind 過濾）⇒ 這裡轉紅。
    """
    tension = _tension()
    opened = ea._make_tension_event(
        ea.TENSION_EVENT_OPENED, tension, ea.TENSION_STATUS_OPEN, now=1000.0
    )
    legacy_thought = _legacy_thought_event(tension, ts=1001.0)

    folded = ea._fold_tension_events([opened, legacy_thought])
    assert folded[tension.tension_id]["kind"] == ea.TENSION_EVENT_OPENED, (
        f"thought 註記不得覆蓋狀態：{folded[tension.tension_id]!r}"
    )
    assert folded[tension.tension_id]["status"] == ea.TENSION_STATUS_OPEN


def test_g1b_guard_blocks_reopen_after_legacy_thought(tmp_path):
    """G1-B（守門面）— [opened, 舊式 thought] 後再 open ⇒ 被擋（冪等成功）。

    舊式 thought 的 status 冒充 "resolved"，但 kind 感知守門根本不讀它
    ⇒ prior 仍是 opened ⇒ 不得再寫第二筆 opened。M-a ⇒ 這裡轉紅
    （prior 被漂白成 resolved ⇒ 誤放行 ⇒ 第二筆 opened 出現）。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    opened = ea._make_tension_event(
        ea.TENSION_EVENT_OPENED, tension, ea.TENSION_STATUS_OPEN, now=1000.0
    )
    _write_events(store, [opened, _legacy_thought_event(tension, ts=1001.0)])

    assert ea.upsert_open_tension(tension, store) is True
    assert len(_opened_events(store)) == 1, (
        f"thought 回聲後重開必須被擋：{_opened_events(store)!r}"
    )


def test_g1c_thought_status_never_masquerades_as_terminal(tmp_path):
    """G1-C（API 狀態形）— record_thought_enqueued 自帶非終態標籤。

    寫出的 thought 事件 status ＝ "thought"（TENSION_STATUS_THOUGHT），
    **不是** "resolved"，且該值刻意不在 TENSION_STATUSES 三元組內
    （三元組由 test_p3_t5_tension_statuses_is_a_closed_set 釘死）。
    M-b（改回 "resolved"）⇒ 這裡轉紅。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    assert ea.upsert_open_tension(tension, store) is True
    assert ea.record_thought_enqueued(tension, store, thought_payload="回聲") is True

    events = _read_events(store)
    assert len(events) == 2
    thought = events[-1]
    assert thought["kind"] == ea.TENSION_EVENT_THOUGHT_ENQUEUED
    assert thought["status"] == ea.TENSION_STATUS_THOUGHT == "thought", (
        f"thought 註記的 status 永不冒充終態：{thought['status']!r}"
    )
    assert thought["status"] not in ea.TENSION_STATUSES, (
        "thought 標籤不得進入封閉三元組 TENSION_STATUSES"
    )


def test_g1d_guard_does_not_overblock(tmp_path):
    """G1-D（不多擋）— 首開／真 resolve 後重開／不同 id 照寫。

    E8 形（真 resolve → 重入 → 同 id 重開）仍判允許是既有語意
    （舊 G2 釘死；是否非法屬 F4 政策裁決，本階段不斷言）。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    assert ea.upsert_open_tension(tension, store) is True
    assert len(_opened_events(store)) == 1

    # 真 resolve 後重開 ⇒ 允許（恰好多一筆 opened）。
    assert ea.mark_tension_resolved(tension, store, now=2000.0) is True
    assert ea.upsert_open_tension(tension, store) is True
    assert len(_opened_events(store)) == 2, "真 resolve 後的重開不得被守門擋掉"

    # 不同 id ⇒ 照寫。
    other = _tension(tension_id="epistemic_gap:fix0002")
    assert ea.upsert_open_tension(other, store) is True
    assert len(_opened_events(store)) == 3, "不同 id 的開立不得被守門擋掉"


# ── G2：tmp 端到端（含舊式回聲序列與 E8 形序列）────────────────────────
def test_g2a_legacy_thought_echo_does_not_reopen(tmp_path):
    """G2-A — 生產 E6 前段形：[opened, 舊式 thought] 後再 open ⇒ 被擋。

    這是生產 E7 → E8 誤放行的最小重現（舊式 thought status＝"resolved"）。
    M-a ⇒ 這裡轉紅（與 G1-B 同時點紅：F1／F2 共用同一份 kind 感知原語）。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    opened = ea._make_tension_event(
        ea.TENSION_EVENT_OPENED, tension, ea.TENSION_STATUS_OPEN, now=1_700_000_000.0
    )
    _write_events(store, [opened, _legacy_thought_event(tension, ts=1_700_000_001.0)])

    assert ea.upsert_open_tension(tension, store) is True
    assert len(_opened_events(store)) == 1, (
        f"舊式 thought 回聲後重開必須被擋：{_opened_events(store)!r}"
    )
    assert len(_read_events(store)) == 2, "被擋時不得多寫任何事件"


def test_g2b_new_thought_roundtrip_stays_blocked(tmp_path):
    """G2-B — 全 API 走一輪：開 → thought(API) → 重開 ⇒ 被擋，且狀態形正確。

    M-b（thought status 改回 "resolved"）⇒ 狀態形斷言轉紅。
    M-a（拿掉 kind 過濾）⇒ 即使狀態形正確也會誤放行 ⇒ 被擋斷言轉紅。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    assert ea.upsert_open_tension(tension, store) is True
    assert ea.record_thought_enqueued(tension, store, thought_payload="回聲") is True

    thought = _read_events(store)[-1]
    assert thought["status"] == "thought", f"API 寫出的 thought 狀態形：{thought['status']!r}"

    assert ea.upsert_open_tension(tension, store) is True
    assert len(_opened_events(store)) == 1, "thought 之後重開必須被擋"
    assert len(_read_events(store)) == 2, "被擋時不得多寫任何事件"


def test_g2c_e8_shape_still_allowed_but_single_write(tmp_path):
    """G2-C — E8 形 [opened, resolved, thought] 後重開 ⇒ 允許，但只多寫一筆。

    真 resolve 在先 ⇒ 重開合法；thought 註記夾在中間不得改變這個判定，
    也不得多寫（恰好 opened＋1，總事件＋1）。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    opened = ea._make_tension_event(
        ea.TENSION_EVENT_OPENED, tension, ea.TENSION_STATUS_OPEN, now=1_700_000_000.0
    )
    resolved = ea._make_tension_event(
        ea.TENSION_EVENT_RESOLVED, tension, ea.TENSION_STATUS_RESOLVED, now=1_700_000_001.0
    )
    _write_events(store, [opened, resolved, _legacy_thought_event(tension, ts=1_700_000_002.0)])

    assert ea.upsert_open_tension(tension, store) is True
    assert len(_opened_events(store)) == 2, (
        f"E8 形（真 resolve 在先）必須允許重開：{_opened_events(store)!r}"
    )
    assert len(_read_events(store)) == 4, "允許重開時只准多寫恰好一筆 opened"


# ── G3：讀端（有／無 resolve 序列的末筆狀態語意）───────────────────────
def test_g3a_open_with_thought_echo_stays_open(tmp_path):
    """G3-A — [opened, thought] 的讀端仍報 open（thought 不閉合張力）。"""
    store = tmp_path / "tensions"
    tension = _tension()
    opened = ea._make_tension_event(
        ea.TENSION_EVENT_OPENED, tension, ea.TENSION_STATUS_OPEN, now=1000.0
    )
    _write_events(store, [opened, _legacy_thought_event(tension, ts=1001.0)])

    assert [t.tension_id for t in ea.load_open_tensions("agent_rem", store)] == [
        tension.tension_id
    ], "thought 回聲不得把未閉合張力擠出未閉合清單"
    records = ea.load_tension_records("agent_rem", store)
    assert [
        r["status"] for r in records if r["tension"].get("tension_id") == tension.tension_id
    ] == ["open"]


def test_g3b_resolved_with_thought_echo_stays_resolved(tmp_path):
    """G3-B — [opened, resolved, thought] 的讀端仍報已消解（thought 不重開張力）。

    且 fold 的末筆「狀態型」事件 kind 必須是真 resolve（thought 永不參與折疊）。
    M-a ⇒ kind 斷言轉紅（fold 被 thought 覆蓋）。
    """
    store = tmp_path / "tensions"
    tension = _tension()
    opened = ea._make_tension_event(
        ea.TENSION_EVENT_OPENED, tension, ea.TENSION_STATUS_OPEN, now=1000.0
    )
    resolved = ea._make_tension_event(
        ea.TENSION_EVENT_RESOLVED, tension, ea.TENSION_STATUS_RESOLVED, now=1001.0
    )
    events = [opened, resolved, _legacy_thought_event(tension, ts=1002.0)]
    _write_events(store, events)

    folded = ea._fold_tension_events(events)
    assert folded[tension.tension_id]["kind"] == ea.TENSION_EVENT_RESOLVED, (
        f"末筆狀態型事件必須是真 resolve：{folded[tension.tension_id]!r}"
    )
    assert [
        t.tension_id for t in ea.load_open_tensions("agent_rem", store)
    ] == [], "thought 回聲不得把已消解張力搬回未閉合清單"
