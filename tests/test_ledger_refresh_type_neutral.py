# -*- coding: utf-8 -*-
"""tests/test_ledger_refresh_type_neutral.py — 型別中立化守衛（G1～G6）。

工單
----
`logs/LEDGER-REFRESH-TYPE-NEUTRAL-1-WORK-ORDER.md`：把
`scripts/ledger_refresh.py::structural_members()` 與
`scripts/eval_lifecycle_cli.py::expected_retirement_sets_by_cohort()` 的
「四條件推導」從**型別硬寫 value** 解耦為**以台帳記載型別為準**，
消除「missing 0」量測盲區。

設計前提（**由實測決定**，不是推測）
----------------------------------
1. **型別來源＝台帳記載型別**（`"types"` 欄位；未記載 ⇒ 向後相容預設
   `"value"`），與 CLI 的 `approved_node_types()` / `_recorded_node_type()`
   **完全同源**（刷新器直接複用 CLI 模組，不另寫一份推導）。
2. **fail-closed 方向**：未入帳的非 value 節點推導不出來 ⇒ 推導只會更嚴、
   不會更鬆 ⇒ 逐 cohort 的自我否決（`derived ⊆ approved`）不受影響。
   （實測佐證：全庫每個 cohort 的 extra 皆為 0。）
3. **入帳紀律**（Owner 2026-10-27 裁定）：**value 由每日刷新 job 自動入帳；
   belief／trait／essence 嚴禁自動入帳** ⇒ 守衛 G5 釘死這條。

為什麼「全部靈魂型」是**錯誤**的規則（實測，不是推測）
------------------------------------------------------
本 repo 的生產圖譜裡，2026-10-23～10-28 這 6 個 cohort 的建立日上存在
**未被核准**的 belief／trait 節點（合計 8+1+3 顆）。若把條件①改成
「屬靈魂型即算」，這些節點會被推導出來卻不在核准清單內 ⇒
`derived ⊋ approved` ⇒ 自我否決破裂、CLI 在**執行期**報 FAIL。
⇒ 唯一安全且符合工單的規則就是「台帳記載型別」。

本檔的守衛
----------
* **G1** 刷新器型別中立：`structural_members()` 必須把 belief／trait／essence
  型算進缺口（改前只認 value ⇒ 會漏）。
* **G2** CLI 推導型別中立：`expected_retirement_sets_by_cohort()` 必須把
  台帳記載為 belief 的節點算進 derived。
* **G3** 自我否決不變量：**每個** cohort 斷言 `derived ⊆ approved`（extra == 0）。
* **G4** 未放寬防呆：型別對但**其餘三條件**不成立者**不得**被算進缺口
  （證明沒有為了修盲區而放寬別的條件）。
* **G5** 台帳不得被工具自行改寫：跑一次刷新器（`--dry-run` 與非 dry-run）
  ⇒ `EXPECTED_RETIREMENT_COHORTS` **不得**被自動加入非 value 型節點
  （入帳必須是人工／主大腦的動作）。
* **G6**（工單 §6）**approved ⊋ derived 的差額可被解釋**：差額只能是
  「被背書過才老化」（supported-then-aged）的成員 ⇒ 台帳刻意只增不減是
  **可解釋**的，不是台帳與圖矛盾。

🔴 安全邊界（與本 repo 的既有做法一致）
----------------------------------------
* **不啟動子行程**（`tests/**` 禁 `subprocess`，見
  `tests/infra/test_no_production_spawn_guard.py` 的 R5）⇒ 刷新器以
  **行程內**執行（launcher 被 stub 掉），且跑在 **影子樹**（%TEMP% 的副本）
  ⇒ 寫入只落在影子樹，repo 檔與生產 `data/**` 零寫入。
* **不得殺任何行程**、不得啟動伺服器、不得綁任何埠。
* 變異實驗的載入點覆寫**只允許**指向 %TEMP% 下的副本（沿用
  `tests/test_eval_lifecycle_cli.py` 的 `SOUL_OS_EVAL_CLI_PATH` 慣例）⇒
  repo 檔全程唯讀，沒有東西需要還原、也沒有東西會還原失敗。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 🔴 變異實驗的載入點覆寫（**僅允許**指向 %TEMP% 下的副本）——與
#    `tests/test_eval_lifecycle_cli.py` 的同名慣例一致：副本在 %TEMP%，
#    repo 檔全程不必被寫入 ⇒ 沒有東西需要還原，也沒有東西會還原失敗。
_TEMP_ROOT = pathlib.Path(tempfile.gettempdir()).resolve()


def _resolve_override(env_name: str, default: pathlib.Path) -> pathlib.Path:
    raw = os.environ.get(env_name)
    if not raw:
        return default
    candidate = pathlib.Path(raw).resolve()
    if _TEMP_ROOT not in candidate.parents:
        raise RuntimeError(
            "%s 只能指向 TEMP 根目錄下的副本（收到：%s）" % (env_name, candidate))
    return candidate


CLI_PATH = _resolve_override(
    "SOUL_OS_EVAL_CLI_PATH", REPO_ROOT / "scripts" / "eval_lifecycle_cli.py")
REFRESH_PATH = _resolve_override(
    "SOUL_OS_LEDGER_REFRESH_PATH", REPO_ROOT / "scripts" / "ledger_refresh.py")
PROD_DIR = REPO_ROOT / "data" / "elevation"

#: 合成 fixture 的時間錨點（與台帳無關；純粹是一個已過的建立日）。
_FIX_DATE = "2026-10-20"
_FIX_CREATED = "2026-10-20T03:15:00+00:00"


def _load(name: str, path: pathlib.Path):
    """以檔案路徑載入模組（`scripts/` 不是 package；絕不當成 `__main__`）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, (
        "cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True        # never drop a __pycache__ into scripts/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


CLI = _load("eval_lifecycle_cli_type_neutral", CLI_PATH)
REFRESH = _load("ledger_refresh_type_neutral", REFRESH_PATH)


# ── 合成 fixture ──────────────────────────────────────────────────


def _node(nid: str, node_type: str = "value", created: str = _FIX_CREATED,
          last_support: str | None = None, agent: str = "agent_probe") -> dict:
    """一個節點記錄（預設滿足四條件）。"""
    return {
        "node_id": nid,
        "node_type": node_type,
        "created_ts": created,
        "last_support_ts": created if last_support is None else last_support,
        "agent_id": agent,
    }


def _support_edge(nid: str) -> dict:
    """一條指向該節點的 support_propagation 祖先邊（⇒ 條件④不成立）。"""
    return {
        "node_id": nid,
        "trigger_type": "support_propagation",
        "valid_from_ts": "2026-10-21T01:00:00+00:00",
        "valid_until_ts": None,
    }


def _cohort(date: str = _FIX_DATE, nodes=(), types=None, bounds=None) -> dict:
    """一個 cohort 條目（形狀與台帳的 `normalize_cohorts()` 一致）。"""
    return {
        "date": date,
        "not_before": "2026-10-28T02:00:00+00:00",
        "nodes": frozenset(nodes),
        "types": dict(types or {}),
        "bounds": dict(bounds or {}),
    }


def _prod_jsonl(name: str) -> list:
    """生產圖譜（**唯讀**）。"""
    path = PROD_DIR / name
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# ═════════════════════════════════════════════════════════════════════
# G1 — 刷新器型別中立
# ═════════════════════════════════════════════════════════════════════


def test_g1_refresher_counts_belief_trait_and_essence_nodes():
    """G1：belief／trait／essence 型**也必須**被算進結構性缺口。

    改前（硬寫 `node_type != "value"`）⇒ 這三型全部被漏掉 ⇒ 本測試轉紅。
    """
    for node_type in ("belief", "trait", "essence"):
        nid = ("%02x" % len(node_type)) * 16
        nodes = [_node(nid, node_type=node_type)]
        types = {nid: node_type}                 # 台帳記載的型別
        members = REFRESH.structural_members(nodes, [], _FIX_DATE, types)
        assert nid in members, (
            "型別 %s 的節點必須被算進四條件缺口（型別中立）；"
            "只認 value 的舊實作會把它漏掉" % node_type)
        assert members[nid] == "agent_probe", "成員的 agent 對應必須保留"


def test_g1_refresher_default_type_source_is_the_ledger():
    """G1（預設來源）：型別來源必須是**台帳記載型別**，不是節點自己的型別。

    ⇒ 用台帳**真實記載**為 belief／trait 的節點驗證（生產圖譜唯讀）。
    這條同時證明：未記載者沿用向後相容預設 `"value"`（fail-closed）。
    """
    soul_ids = {nid: t for nid, t in CLI.approved_node_types().items()
                if t != "value"}
    assert soul_ids, (
        "台帳已無任何非 value 的記載型別 ⇒ G1 的預設來源無從驗證"
        "（工單的前提消失，請回報主大腦）")
    prod_nodes = _prod_jsonl("elevation_nodes.jsonl")
    prod_edges = _prod_jsonl("elevation_edges.jsonl")
    assert prod_nodes, "生產圖譜不可讀（data/elevation/elevation_nodes.jsonl）"

    checked = 0
    for nid, recorded_type in sorted(soul_ids.items()):
        node = next((n for n in prod_nodes
                     if isinstance(n, dict) and n.get("node_id") == nid), None)
        if node is None:
            continue
        date = (node.get("created_ts") or "")[:10]
        if not date:
            continue
        members = REFRESH.structural_members(prod_nodes, prod_edges, date)
        assert nid in members, (
            "台帳記載為 %s 的節點 %s 必須被型別中立的四條件量測算進缺口"
            % (recorded_type, nid))
        checked += 1
    assert checked, "台帳記載了非 value 型別，但沒有一顆落在生產圖譜裡"


def test_g1_refresher_unrecorded_node_keeps_the_value_default():
    """未記載於台帳者沿用預設 `"value"` ⇒ 非 value 節點**不會**被自動量進來。

    🔴 這是 fail-closed 方向的具體化：漏寫型別只會更嚴、不會更鬆
    （⇒ 未入帳的 belief 不會被這支工具自動入帳）。
    """
    nid = "ff" * 16
    nodes = [_node(nid, node_type="belief")]
    members = REFRESH.structural_members(nodes, [], _FIX_DATE)
    assert nid not in members, (
        "未記載型別者視為 value ⇒ belief 節點不得被算進缺口"
        "（否則刷新器會自動核准非 value 型 ⇒ 違反入帳紀律）")


# ═════════════════════════════════════════════════════════════════════
# G2 — CLI 推導型別中立
# ═════════════════════════════════════════════════════════════════════


def test_g2_cli_derivation_counts_the_ledger_recorded_type():
    """G2：台帳記載為 belief 的節點必須被推導出來。

    改前（硬寫 `node_type != "value"`）⇒ belief 被漏掉 ⇒ 本測試轉紅。
    """
    nid = "ab" * 16
    cohort = _cohort(nodes=[nid], types={nid: "belief"})
    derived = CLI.expected_retirement_sets_by_cohort(
        [_node(nid, node_type="belief")], [], cohorts=[cohort])
    assert nid in derived[_FIX_DATE], (
        "台帳記載為 belief 的節點必須被推導出來（型別中立）")


def test_g2_cli_derivation_is_fail_closed_on_type_mismatch():
    """型別與**台帳記載**不符者不得被推導（fail-closed：只會更嚴、不會更鬆）。"""
    nid = "ac" * 16
    cohort = _cohort(nodes=[nid], types={nid: "belief"})
    derived = CLI.expected_retirement_sets_by_cohort(
        [_node(nid, node_type="value")], [], cohorts=[cohort])
    assert nid not in derived[_FIX_DATE], (
        "台帳記載為 belief、實際型別是 value ⇒ 不得被推導")


# ═════════════════════════════════════════════════════════════════════
# G3 — 自我否決不變量（逐 cohort：derived ⊆ approved）
# ═════════════════════════════════════════════════════════════════════


def test_g3_derived_is_a_subset_of_approved_for_every_cohort():
    """G3：**每個** cohort 的推導集合必須 ⊆ 該 cohort 的核准清單（extra == 0）。

    🔴 這是「規則不得被悄悄放寬」的**執行期**不變量。型別中立化讓推導集合
       變大（多了台帳記載的 belief／trait）⇒ 這條守衛證明它**沒有**變大到
       越界。
    """
    prod_nodes = _prod_jsonl("elevation_nodes.jsonl")
    prod_edges = _prod_jsonl("elevation_edges.jsonl")
    assert prod_nodes, "生產圖譜不可讀（data/elevation/elevation_nodes.jsonl）"

    derived_by_cohort = CLI.expected_retirement_sets_by_cohort(
        prod_nodes, prod_edges)
    violations = []
    for cohort in CLI.normalize_cohorts():
        date = cohort["date"]
        derived = derived_by_cohort.get(date, set())
        approved = set(cohort["nodes"])
        extra = sorted(derived - approved)
        if extra:
            violations.append((date, extra))
    assert not violations, (
        "推導集合超出核准清單（規則被放寬，或台帳待刷新）：%s" % violations)
    assert CLI.baseline_violation(derived_by_cohort) == [], (
        "執行期自我否決被觸發：%s" % CLI.baseline_violation(derived_by_cohort))


# ═════════════════════════════════════════════════════════════════════
# G4 — 未放寬防呆（其餘三條件原封不動）
# ═════════════════════════════════════════════════════════════════════


def test_g4_refresher_does_not_relax_the_other_three_conditions():
    """G4（刷新器）：型別對、其餘條件不成立者**不得**被算進缺口。"""
    cases = {
        "last_support_ts != created_ts（被背書過）": (
            _node("b1" * 16, node_type="belief", last_support="2026-10-25T00:00:00+00:00"),
            [], "b1" * 16),
        "有 support_propagation 祖先邊": (
            _node("b2" * 16, node_type="belief"),
            [_support_edge("b2" * 16)], "b2" * 16),
        "建立日不是該 cohort 日期": (
            _node("b3" * 16, node_type="belief", created="2026-10-19T03:15:00+00:00"),
            [], "b3" * 16),
    }
    for label, (node, edges, nid) in cases.items():
        types = {nid: node["node_type"]}
        members = REFRESH.structural_members([node], edges, _FIX_DATE, types)
        assert nid not in members, (
            "「%s」不得被算進缺口（為了修盲區而放寬其他條件）" % label)


def test_g4_cli_does_not_relax_the_other_three_conditions():
    """G4（CLI）：型別對、其餘條件不成立者**不得**被推導。"""
    nid = "cd" * 16
    cohort = _cohort(nodes=[nid], types={nid: "belief"})
    cases = {
        "last_support_ts != created_ts": (
            _node(nid, node_type="belief", last_support="2026-10-25T00:00:00+00:00"), []),
        "有 support_propagation 祖先邊": (
            _node(nid, node_type="belief"), [_support_edge(nid)]),
        "建立日不是該 cohort 日期": (
            _node(nid, node_type="belief", created="2026-10-19T03:15:00+00:00"), []),
    }
    for label, (node, edges) in cases.items():
        derived = CLI.expected_retirement_sets_by_cohort(
            [node], edges, cohorts=[cohort])
        assert nid not in derived[_FIX_DATE], (
            "「%s」不得被推導（為了修盲區而放寬其他條件）" % label)


# ═════════════════════════════════════════════════════════════════════
# G5 — 台帳不得被工具自行改寫
# ═════════════════════════════════════════════════════════════════════


def test_g5_auto_entry_accepts_value_only():
    """G5（單元）：自動入帳集合**只收 value 型**。

    🔴 入帳紀律（Owner 2026-10-27 裁定）：value 由每日刷新 job 自動入帳；
       belief／trait／essence **嚴禁自動入帳**（必須人工審核核准後補丁登錄）。
    """
    value_id, belief_id, trait_id = "e1" * 16, "e2" * 16, "e3" * 16
    types = {value_id: "value", belief_id: "belief", trait_id: "trait"}
    auto = REFRESH.auto_entry_ids({value_id, belief_id, trait_id}, types)
    assert value_id in auto, "value 型必須可自動入帳"
    assert belief_id not in auto, "belief 型**嚴禁**自動入帳"
    assert trait_id not in auto, "trait 型**嚴禁**自動入帳"


def _sha256(path: pathlib.Path) -> str:
    if not path.exists():
        return "<missing>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_shadow_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """把刷新器會寫的檔案複製成影子樹（**寫入只落在影子樹**）。"""
    shadow = tmp_path / "shadow"
    (shadow / "scripts").mkdir(parents=True)
    (shadow / "logs").mkdir(parents=True)
    (shadow / "data" / "elevation").mkdir(parents=True)
    shutil.copy2(REFRESH_PATH, shadow / "scripts" / "ledger_refresh.py")
    shutil.copy2(CLI_PATH, shadow / "scripts" / "eval_lifecycle_cli.py")
    canon = REPO_ROOT / "logs" / "ENGINEERING_STATE.md"
    if canon.exists():
        shutil.copy2(canon, shadow / "logs" / "ENGINEERING_STATE.md")
    for name in ("elevation_nodes.jsonl", "elevation_edges.jsonl",
                 "elevation_lifecycle_events.jsonl"):
        src = REPO_ROOT / "data" / "elevation" / name
        if src.exists():
            shutil.copy2(src, shadow / "data" / "elevation" / name)
    return shadow


def _fake_sh(cmd, cwd=None, env=None):
    """刷新器的行程啟動器替身（R5：tests/** 不得 spawn 子行程）。

    刷新器只會啟動兩件事：沙盒模擬腳本與沙盒 CLI。兩者的輸出被替身成
    解析器需要的最小形狀。
    """
    joined = " ".join(str(part) for part in cmd)
    closed = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    if "ledger_refresh_sim.py" in joined:
        return 0, json.dumps({closed: {"e1" * 16: "agent_probe"}}) + "\n", ""
    if "eval_lifecycle_cli.py" in joined:
        return 0, "VERDICT: PASS\n", ""
    return 0, "ok\n", ""


def _run_shadow_refresh(shadow: pathlib.Path, dry_run: bool, tmp_path):
    """在影子樹裡跑一次刷新器（行程內；launcher 被 stub）。"""
    module = _load("ledger_refresh_shadow_%s" % ("dry" if dry_run else "real"),
                    shadow / "scripts" / "ledger_refresh.py")
    module.sh = _fake_sh
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    argv = ["ledger_refresh.py"] + (["--dry-run"] if dry_run else [])
    previous_argv = sys.argv
    sys.argv = argv
    try:
        return module.main()
    finally:
        sys.argv = previous_argv
        tempfile.tempdir = previous_tempdir


def test_g5_refresh_never_auto_enrolls_non_value_nodes(tmp_path):
    """G5（整合）：跑一次刷新器（`--dry-run` 與非 dry-run）⇒ 台帳不得被
    **自動加入非 value 型節點**（入帳必須是人工／主大腦的動作）。

    🔴 全程寫在**影子樹**（%TEMP% 的副本）⇒ repo 檔與生產 `data/**` 零寫入。
    """
    # 影子樹的 CLI 模組會 import `src.*` ⇒ repo 根必須在 sys.path 上。
    if str(REPO_ROOT) not in sys.path:
        sys.path.append(str(REPO_ROOT))

    watched = [
        REPO_ROOT / "scripts" / "eval_lifecycle_cli.py",
        REPO_ROOT / "scripts" / "ledger_refresh.py",
        REPO_ROOT / "logs" / "ENGINEERING_STATE.md",
    ]
    before = {p: _sha256(p) for p in watched}
    elev_before = _sha256(PROD_DIR / "elevation_nodes.jsonl")

    original_soul = {nid for nid, t in CLI.approved_node_types().items()
                     if t != "value"}
    original_ledger_nodes = {
        c["date"]: set(c["nodes"]) for c in CLI.normalize_cohorts()}

    # ── dry-run ──────────────────────────────────────────────────
    shadow = _build_shadow_tree(tmp_path)
    ledger_path = shadow / "scripts" / "eval_lifecycle_cli.py"
    ledger_before = _sha256(ledger_path)
    assert _run_shadow_refresh(shadow, dry_run=True, tmp_path=tmp_path) == 0
    assert _sha256(ledger_path) == ledger_before, (
        "--dry-run 不得改動影子台帳")

    # ── 非 dry-run ───────────────────────────────────────────────
    shadow = _build_shadow_tree(tmp_path / "real")
    ledger_path = shadow / "scripts" / "eval_lifecycle_cli.py"
    ledger_before_run = _sha256(ledger_path)
    assert _run_shadow_refresh(shadow, dry_run=False, tmp_path=tmp_path) == 0
    # 🔴 反空轉：影子台帳必須**真的被改寫** ⇒ 寫入路徑確實被走到 ⇒ 下面
    #    「沒有被自動加入非 value 型」的斷言才有意義（否則本守衛會是空轉的）。
    assert _sha256(ledger_path) != ledger_before_run, (
        "影子台帳完全沒被改動 ⇒ 寫入路徑沒被走到 ⇒ 本守衛空轉"
        "（必須先證明刷新器真的會改寫台帳）")

    shadow_module = _load("shadow_cli_check", ledger_path)
    shadow_cohorts = shadow_module.normalize_cohorts()

    # (a) 台帳**原本記載**的非 value 型節點必須一個都還在（不得被抹掉）
    shadow_soul_ids = {nid for c in shadow_cohorts
                       for nid, t in (c.get("types") or {}).items()
                       if t != "value"}
    assert original_soul <= shadow_soul_ids, (
        "刷新器抹掉了台帳記載的非 value 型節點：%s"
        % sorted(original_soul - shadow_soul_ids))

    # (b) 台帳**不得被自動加入**非 value 型節點
    newly_added_soul = shadow_soul_ids - original_soul
    assert not newly_added_soul, (
        "刷新器自行把非 value 型節點入帳了（嚴禁自動入帳）：%s"
        % sorted(newly_added_soul))

    # (c) 逐 cohort：**新加入**的節點不得是圖譜上的非 value 型。
    #    🔴 這裡必須對照**圖譜的實際型別**，不能只看台帳的 `types`：最危險的
    #    故障模式正是「把 belief 節點當成普通 id 寫進 `nodes`、卻沒寫 `types`」
    #    ⇒ 台帳會把它記成 value ⇒ 判定層型別不符 ⇒ 假紅（預期代謝被誤判成破線）。
    graph_types = {n.get("node_id"): n.get("node_type")
                   for n in _prod_jsonl("elevation_nodes.jsonl")
                   if isinstance(n, dict)}
    for cohort in shadow_cohorts:
        date = cohort["date"]
        original = original_ledger_nodes.get(date, set())
        added = set(cohort["nodes"]) - original
        added_non_value = sorted(
            nid for nid in added
            if (graph_types.get(nid) or "value") != "value"
            or (cohort.get("types") or {}).get(nid, "value") != "value")
        assert not added_non_value, (
            "cohort %s 被自動加入非 value 型節點：%s" % (date, added_non_value))

    # (d) repo 檔與生產圖譜**逐位元未變**
    after = {p: _sha256(p) for p in watched}
    assert before == after, "刷新器（影子樹）改動了 repo 檔：%s" % [
        str(p) for p in watched if before[p] != after[p]]
    assert _sha256(PROD_DIR / "elevation_nodes.jsonl") == elev_before, (
        "刷新器改動了生產圖譜")


# ═════════════════════════════════════════════════════════════════════
# G6 — 工單 §6：approved ⊋ derived 的差額**可被解釋**
# ═════════════════════════════════════════════════════════════════════


def test_g6_approved_minus_derived_is_always_explainable():
    """G6：台帳刻意「只增不減」留下來的差額，必須**逐筆可解釋**。

    背景（工單 §6）：台帳對封閉 cohort **只增不減**（保留 supported-then-aged
    成員），而既有守衛 `test_closed_cohort_is_fully_reconciled_with_the_graph`
    斷言的是**等號** ⇒ 只要有成員「被背書過才老化」，approved 就會**永久大於**
    derived ⇒ 假紅。

    🔴 本守衛**不修改**那條等號斷言（放寬判準是主大腦／Owner 的裁示範圍）。
       它只做一件事：把「approved ⊋ derived 的差額」**明確量測出來**，並要求
       差額**可被解釋**（每個差額成員都必須是「被背書過才老化」）。
    """
    prod_nodes = _prod_jsonl("elevation_nodes.jsonl")
    prod_edges = _prod_jsonl("elevation_edges.jsonl")
    assert prod_nodes, "生產圖譜不可讀（data/elevation/elevation_nodes.jsonl）"

    by_id = {n.get("node_id"): n for n in prod_nodes if isinstance(n, dict)}
    supported = {e.get("node_id") for e in prod_edges
                 if isinstance(e, dict)
                 and e.get("trigger_type") == "support_propagation"}
    derived_by_cohort = CLI.expected_retirement_sets_by_cohort(
        prod_nodes, prod_edges)

    measured = []
    unexplained = []
    for cohort in CLI.normalize_cohorts():
        date = cohort["date"]
        difference = sorted(set(cohort["nodes"]) - derived_by_cohort.get(date, set()))
        measured.append((date, len(difference)))
        for nid in difference:
            node = by_id.get(nid)
            if node is None:
                unexplained.append((date, nid, "節點不在圖譜中"))
                continue
            created = node.get("created_ts") or ""
            supported_after_creation = (node.get("last_support_ts") or "") != created
            has_ancestor_support_edge = nid in supported
            if not (supported_after_creation or has_ancestor_support_edge):
                unexplained.append(
                    (date, nid, "既非被背書過、也無 support_propagation 祖先邊"))

    assert not unexplained, (
        "approved ⊋ derived 的差額有**無法解釋**的成員：%s" % unexplained)
    # 反空轉：**每個** cohort 都必須被量測到（差額表不可缺漏）⇒ 這條守衛
    # 確實在逐 cohort 量，而不是只跑第一個就回報。
    assert len(measured) == len(list(CLI.normalize_cohorts())), (
        "每個 cohort 都必須被量測到")
    # 差額是**量測值**，不是門檻：把總數講清楚，讓「台帳與圖矛盾」無所遁形。
    print("[G6] approved \\ derived per cohort: %s (total %d)"
          % (measured, sum(n for _, n in measured)))
