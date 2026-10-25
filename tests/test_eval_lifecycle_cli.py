# -*- coding: utf-8 -*-
"""tests/test_eval_lifecycle_cli.py — `scripts/eval_lifecycle_cli.py` 的守衛測試。

背景（Owner 2026-10-19 裁示「方案一」）：生命週期評估原本只能等 scheduler 的
night slot（22:00）才會跑，功能驗收被迫等 5～6 小時牆鐘。CLI 驅動器把
「功能邏輯驗收」從牆鐘上解耦（398 節點 / 903 邊全量評估實測 0.027～0.083 秒）。

🔴 本檔守的是**兩條紅線**：

1. **CLI 結構上不可能寫入生產資料。**
   前一版曾提供 `--data-dir … --in-place`（就地寫入），其「拒絕寫生產」的判斷
   `_same_dir(target, production_dir)` **被實測證偽**：生產目錄的解析值取自
   `src.paths.data_root()`，而它是**行程期快取**的單例；`tests/conftest.py` 的
   autouse fixture 會把 `SOUL_OS_DATA_DIR` 指向每個測試自己的 tmp 目錄，於是
   「測試行程快取到的生產目錄」與「CLI 子行程即時重讀到的生產目錄」不是同一個，
   比較失效 ⇒ CLI 對生產執行了就地寫入（2026-10-19 18:11 實測：167 筆
   lifecycle_transition 被寫進生產 sidecar）。
   ⇒ **修法不是補判斷，是移除寫入能力**：本 CLI 現在**沒有任何就地寫入旗標**，
   唯一寫入目標是它自己 `tempfile.mkdtemp()` 建立的沙盒。

2. **CLI 的四項判定不是空轉**——構造出惡意事件餵進純函式 `assess()`，
   它必須報 FAIL，否則「PASS」沒有任何意義。

ℹ️ **本檔刻意不使用子行程**：CLI 的 `main()` 可在同一直行程內直接呼叫（更快、
無子行程開銷），且避免在 `tests/**` 內產生 `subprocess.*` 呼叫點 ——
`tests/infra/test_no_production_spawn_guard.py` 的 R5 規則要求每個 spawn 呼叫點
都要被 `(file, line)` 白名單明確列出，而本測試**不需要**那個白名單條目。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
import pathlib
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
# 🔴 變異實驗的載入點覆寫（**僅允許**指向 %TEMP% 下的副本）。
#
# 為什麼需要這個鉤子（2026-10-23 主大腦實測）：
#   變異 harness 若「in-place 改 repo 檔 → 測試 → 位元還原」，還原步驟**可能失敗**
#   （實測：`OSError: [Errno 22] Invalid argument`）⇒ repo 檔會**殘留在變異狀態**
#   （一個可被誤 commit 的損壞工作樹）。⇒ harness 改為：
#   「複製到 %TEMP% → 改**副本** → 用本變數載入副本」，
#   repo 檔全程不必被寫入 ⇒ **沒有東西需要還原，也沒有東西會還原失敗**。
#
# 🔴 安全邊界：覆寫路徑必須在 %TEMP% 之下 ⇒ CI／生產永遠載入 repo 的真檔，
#    這個鉤子無法被用來把守衛測試指向 repo 外的任意程式。
_CLI_OVERRIDE = os.environ.get("SOUL_OS_EVAL_CLI_PATH")
if _CLI_OVERRIDE:
    _override_path = pathlib.Path(_CLI_OVERRIDE).resolve()
    _temp_root = pathlib.Path(tempfile.gettempdir()).resolve()
    if _temp_root not in _override_path.parents:
        raise RuntimeError(
            "SOUL_OS_EVAL_CLI_PATH 只能指向 TEMP 根目錄下的副本（收到：" + str(_override_path) + "）")
    CLI_PATH = _override_path
else:
    CLI_PATH = REPO_ROOT / "scripts" / "eval_lifecycle_cli.py"
PROD_DIR = REPO_ROOT / "data" / "elevation"          # repo 內的實體生產目錄
PROD_SIDECAR = PROD_DIR / "elevation_lifecycle_events.jsonl"

# 🔴 生產 sidecar 的基準在**模組匯入時**（= 任何測試執行之前）擷取。
# 若守衛自己寫了生產，這個基準會在寫入「之前」就固定下來 ⇒ 差異會被偵測到。
# （前一版守衛在「測試方法內」才擷取基準，於是同一場測試內的寫入偵測不到。）
PROD_SIDECAR_SHA_BEFORE = (
    hashlib.sha256(PROD_SIDECAR.read_bytes()).hexdigest()
    if PROD_SIDECAR.exists() else "<missing>"
)

_ISO = "%Y-%m-%dT%H:%M:%S+00:00"


def _sha(path: pathlib.Path) -> str:
    if not path.exists():
        return "<missing>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cli():
    """以檔案路徑載入 CLI 模組（scripts/ 不是 package）。"""
    spec = importlib.util.spec_from_file_location("eval_lifecycle_cli_under_test", CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load_cli()


# 🔴 合成 fixture 的時間錨點（2026-10-22 22:3x 實測事故修正）
#
# 真實事故（**時間炸彈**）：測試把 `--simulated-time` 寫死 `2026-10-23T02:00:00Z`，
# 而合成節點的時間戳用「真實 now − 10 天」⇒ 引擎弱化門檻是 **10 整日**
# （`_days_between` 取整日）⇒ 真實時鐘一旦越過 22:00 EDT，elapsed 掉到 9.98 天
# ⇒ 取整 9 < 10 ⇒ 不轉弱 ⇒ 守衛「必紅」。實測：22:00 EDT 前綠、22:00 EDT 後紅
# （全庫回歸 `2 failed / 7152 passed`）。
#
# 修正：合成 fixture 的時間戳**一律由評估時鐘推導**，不得用真實 now。
_SIMULATED_CLOCK = datetime(2026, 10, 23, 2, 0, 0, tzinfo=timezone.utc)
# 合成節點「多久沒被支持」。取 30 天（遠大於引擎 10 天門檻）⇒ 對評估時鐘的
# 微小漂移免疫，不會再卡在門檻邊界（整日取整會讓 9.99 天掉到 9）。
_SYNTHETIC_STALE_DAYS = 30.0


def _iso(days_ago: float, ref=None) -> str:
    base = ref if ref is not None else datetime.now(timezone.utc)
    return (base - timedelta(days=days_ago)).strftime(_ISO)


def _synthetic_graph(root: pathlib.Path, ref=None) -> pathlib.Path:
    """建立一個**合成**來源圖譜：1 顆長期沒人支持的 pattern ⇒ 應轉 weakening。

    合成（而非複製生產）才能讓「來源是否被寫入」成為可判定的觀察量。

    🔴 `ref` = **評估時鐘**（`--simulated-time` 對應的 instant）。時間戳一律由它推導，
    **不得**用真實 now —— 否則守衛會變成時間炸彈（見 `_SIMULATED_CLOCK` 註解）。
    `ref=None` 時退回真實 now（僅供「不帶 `--simulated-time`」的測試使用）。
    """
    store = root / "synthetic_graph"
    store.mkdir(parents=True, exist_ok=True)
    node = {
        "node_id": uuid.uuid4().hex,
        "node_type": "pattern",
        "content": "很久沒被支持過的規律",
        "agent_id": "agent_x",
        "candidate_node_type": "value",
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": _iso(_SYNTHETIC_STALE_DAYS, ref),
        "last_support_ts": _iso(_SYNTHETIC_STALE_DAYS, ref),
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
        "lifecycle_state": "active",
        "superseded_by": None,
    }
    (store / "elevation_nodes.jsonl").write_text(
        json.dumps(node, ensure_ascii=False) + "\n", encoding="utf-8")
    (store / "elevation_edges.jsonl").write_text("", encoding="utf-8")
    return store


def _run_cli(*args):
    """在**同一行程**內呼叫 CLI 的 `main()`，回傳 (returncode, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = CLI.main(list(args))
    return rc, out.getvalue(), err.getvalue()


class TestAssessIsNotVacuous(unittest.TestCase):
    """守衛 2：判定邏輯必須會喊 FAIL。"""

    def test_clean_pattern_only_passes(self):
        """乾淨情境（pattern→weakening、無靈魂誤傷、prompt 無變）⇒ 無失敗項。"""
        events = [
            {"node_id": "n%d" % i, "node_type": "pattern", "lifecycle_state": "weakening"}
            for i in range(167)
        ]
        fails = CLI.assess(new_events=events, prompt_diffs=[], key_node="KEY",
                           weak_before=0, weak_after=0, graph_nodes=399)
        self.assertEqual(fails, [], "乾淨情境不該有失敗項：%s" % fails)

    def test_no_transition_on_empty_graph_is_a_failure(self):
        """0 筆轉變 **且圖為空** ⇒ 評估根本沒圖可算 ⇒ 必須報 FAIL。"""
        fails = CLI.assess(new_events=[], prompt_diffs=[], key_node="KEY",
                           weak_before=0, weak_after=0, graph_nodes=0)
        self.assertTrue(fails, "空圖 + 0 筆轉變必須報 FAIL")
        self.assertTrue(any("沒有任何轉變" in f for f in fails), fails)

    def test_no_transition_on_non_empty_graph_is_a_no_op_not_a_failure(self):
        """0 筆轉變但**圖非空** ⇒ 該轉的都轉過了（冪等 no-op）⇒ **不是失敗**。

        本機實例：167 顆 pattern 已是 weakening，重跑不會再降一級 ⇒
        今晚 22:00 的預期結果就是 0 筆。若把這判成 FAIL，會是**誤判**。
        """
        fails = CLI.assess(new_events=[], prompt_diffs=[], key_node="KEY",
                           weak_before=0, weak_after=0, graph_nodes=399)
        self.assertEqual(fails, [], "非空圖 + 0 筆轉變是冪等 no-op，不該報失敗：%s" % fails)

    def test_soul_type_transition_is_flagged(self):
        """塞一筆 value 類型的轉變 ⇒ 必須報「靈魂結構誤傷」。"""
        fails = CLI.assess(
            new_events=[{"node_id": "v1", "node_type": "value", "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node="KEY", weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("靈魂結構誤傷" in f for f in fails), fails)

    def test_non_pattern_transition_is_flagged(self):
        """任何非 pattern 類型的轉變 ⇒ 必須報。"""
        fails = CLI.assess(
            new_events=[{"node_id": "b1", "node_type": "belief", "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node="KEY", weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("非 pattern" in f for f in fails), fails)

    def test_dormant_target_is_flagged(self):
        """轉變目標含 dormant ⇒ 必須報。"""
        fails = CLI.assess(
            new_events=[{"node_id": "p1", "node_type": "pattern", "lifecycle_state": "dormant"}],
            prompt_diffs=[], key_node="KEY", weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("dormant" in f for f in fails), fails)

    def test_key_node_transition_is_flagged(self):
        """關鍵反例節點被轉變 ⇒ 必須報。"""
        fails = CLI.assess(
            new_events=[{"node_id": "KEY", "node_type": "pattern", "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node="KEY", weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("關鍵反例" in f for f in fails), fails)

    def test_prompt_change_is_flagged(self):
        """Prompt 正文改變 ⇒ 必須報。"""
        fails = CLI.assess(
            new_events=[{"node_id": "p1", "node_type": "pattern", "lifecycle_state": "weakening"}],
            prompt_diffs=[{"agent": "agent_akane"}], key_node="KEY",
            weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("Prompt" in f for f in fails), fails)

    def test_weak_prefix_delta_is_flagged(self):
        """弱化包裝計數變動 ⇒ 必須報。"""
        fails = CLI.assess(
            new_events=[{"node_id": "p1", "node_type": "pattern", "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node="KEY", weak_before=0, weak_after=3, graph_nodes=399)
        self.assertTrue(any("弱化包裝" in f for f in fails), fails)


# ── 預期退役集合的合成圖（Owner 2026-10-23 裁定「改判準」）──────────────
#
# 🔴 核准基線的 10 顆 id **寫死於此**（不引用 CLI 的常數）⇒ 可反向釘死
#    `EXPECTED_RETIREMENT_BASELINE` 常數本身有沒有被改動。
_RETIREMENT_BASELINE_IDS = (
    "0b398afedf30486baa75047028b3319d",   # agent_akane
    "24bad790f8954dc2ae4711c4c1c6ffac",   # agent_aoi
    "56391bd5ce154d9f8ee09ef40280ec71",   # agent_miku
    "60a2d5e453914ac3bbbbc02cb549b076",   # agent_yua
    "6a92c906b245458cb0c4c64ea214f2eb",   # agent_mahiru ★關鍵反例
    "7fcf5557b1994fd1977973f7dcdf5a38",   # agent_ram
    "8dd335036f21453b85c72d17eaf5b303",   # agent_rem
    "9f9b13498c334268aaf2e95429bd2228",   # agent_ruka
    "a8b48b0210e74363b506f5750d61d125",   # agent_mai
    "d0df567934274a9697feff6d1b0d9af5",   # agent_anna
)
# 三顆「近似不合規」節點：各自**只缺一個條件**，且 id **不在**核准基線內
# ⇒ 只要有人把某個條件移除，它們就會被誤抓進退役集合 ⇒ 守衛必須轉紅。
_NEAR_MISS_DATE = "f" * 32        # 條件 2：建立日不同
_NEAR_MISS_SUPPORT = "e" * 32     # 條件 3：last_support 較晚
_NEAR_MISS_EDGE = "d" * 32        # 條件 4：有 support_propagation 邊指向
_NOT_VALUE = "c" * 32             # 條件 1：node_type 不是 value
#: 退役窗**內**的轉變時刻（10/24 22:00 EDT ＝ 2026-10-25T02:00:00Z 起）。
#: 🔴 合成事件**必須**帶 ts：判準層對「時刻不可解析」採 fail-closed（不放行），
#:    沒有 ts 的 fixture 會讓「白名單／型別」類變異走不到被改的那個條件 ⇒ 守衛失守。
_RETIREMENT_TS = "2026-10-25T02:00:22.072571+00:00"


def _value_node(node_id, created_ts, last_support_ts=None, node_type="value"):
    return {
        "node_id": node_id,
        "node_type": node_type,
        "agent_id": "agent_test",
        "created_ts": created_ts,
        "last_support_ts": last_support_ts or created_ts,
        "stability": 0.3,
        "lifecycle_state": "active",
    }


def _retirement_graph(all_compliant=False):
    """合成圖：10 顆四條件全合規 ＋ 3 顆近似不合規（各缺一個條件）。

    `all_compliant=True` ⇒ 把 3 顆近似節點都補成合規 ⇒ 推導結果**必然超出**
    核准基線 ⇒ 必須被 `baseline_violation()`（執行期自我否決）抓到。
    """
    created = "2026-10-11T08:00:00+00:00"
    nodes = [_value_node(nid, created) for nid in _RETIREMENT_BASELINE_IDS]
    edges = []
    nodes.append(_value_node(_NEAR_MISS_DATE, "2026-10-10T08:00:00+00:00"))
    nodes.append(_value_node(_NEAR_MISS_SUPPORT, created,
                              last_support_ts="2026-10-12T08:00:00+00:00"))
    nodes.append(_value_node(_NEAR_MISS_EDGE, created))
    edges.append({"node_id": _NEAR_MISS_EDGE, "trigger_type": "support_propagation"})
    if all_compliant:
        nodes[10]["created_ts"] = created
        nodes[11]["last_support_ts"] = created
        edges[:] = []
    # 條件全對但 node_type 不是 value ⇒ 永遠不得被推導出來
    nodes.append(_value_node(_NOT_VALUE, created, node_type="belief"))
    return nodes, edges


class TestExpectedMetabolism(unittest.TestCase):
    """Owner 2026-10-23 裁定「改判準」：區分「預期生命週期代謝」與「意外破線」。

    放行必須**有界**：只有「核准基線 10 顆 ＋ 四條件全滿足 ＋ 轉入 weakening」
    才算預期代謝；任何超出 ⇒ 破線（一票否決）。
    """

    def test_retirement_set_derivation_is_exact(self):
        """四條件推導：合成圖（10 顆合規 ＋ 3 顆近似不合規）⇒ **恰好**那 10 顆。"""
        nodes, edges = _retirement_graph()
        derived = CLI.expected_retirement_set(nodes, edges)
        self.assertEqual(
            derived, set(_RETIREMENT_BASELINE_IDS),
            "推導結果必須恰好等於核准的 10 顆（近似不合規的 3 顆不得被誤抓）：%s"
            % sorted(derived))
        self.assertEqual(
            set(CLI.EXPECTED_RETIREMENT_BASELINE), set(_RETIREMENT_BASELINE_IDS),
            "CLI 的核准基線常數被改動了（基線是 Owner 指定的集合，不可動）")

    def test_retirement_set_never_exceeds_baseline(self):
        """推導結果不得超出核准基線；一旦超出，執行期自我否決必須抓到。"""
        nodes, edges = _retirement_graph()
        derived = CLI.expected_retirement_set(nodes, edges)
        self.assertTrue(
            derived <= set(CLI.EXPECTED_RETIREMENT_BASELINE),
            "推導結果必須 ⊆ 核准基線：%s" % sorted(derived))
        self.assertEqual(
            CLI.baseline_violation(derived), [],
            "正常推導結果不得觸發基線自我否決")
        # 把 3 顆近似節點都補成合規 ⇒ 推導結果必然超出基線 ⇒ 必須被自我否決抓到
        nodes2, edges2 = _retirement_graph(all_compliant=True)
        derived2 = CLI.expected_retirement_set(nodes2, edges2)
        self.assertFalse(
            derived2 <= set(CLI.EXPECTED_RETIREMENT_BASELINE),
            "這個圖刻意讓規則超出基線，測試資料有問題")
        self.assertTrue(
            CLI.baseline_violation(derived2),
            "超出核准基線（＝規則被放寬）必須被 baseline_violation 擋下：%s"
            % sorted(derived2))

    def test_whitelisted_value_weakening_is_expected_not_failure(self):
        """白名單 value 轉入 weakening ⇒ 預期代謝 ⇒ assess() 不得回任何失敗項。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        fails = CLI.assess(
            new_events=[{"node_id": wid, "node_type": "value",
                         "lifecycle_state": "weakening",
                         "ts": "2026-10-25T02:00:22.072571+00:00"}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=frozenset({wid}))
        self.assertEqual(fails, [], "白名單 value 的弱化是預期代謝，不是破線：%s" % fails)

    def test_non_whitelisted_value_weakening_fails(self):
        """**非**白名單的 value 弱化 ⇒ 必須報「靈魂結構誤傷」。"""
        fails = CLI.assess(
            new_events=[{"node_id": "a" * 32, "node_type": "value",
                         "lifecycle_state": "weakening", "ts": _RETIREMENT_TS}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=frozenset(_RETIREMENT_BASELINE_IDS))
        self.assertTrue(any("靈魂結構誤傷" in f for f in fails),
                        "非白名單的 value 弱化必須報破線：%s" % fails)

    def test_trait_or_essence_change_fails(self):
        """trait / essence 異動 ⇒ 必須報破線（即使傳入 expected_retirement）。"""
        fails = CLI.assess(
            new_events=[{"node_id": "t" * 32, "node_type": "trait",
                         "lifecycle_state": "weakening", "ts": _RETIREMENT_TS},
                        {"node_id": "s" * 32, "node_type": "essence",
                         "lifecycle_state": "weakening", "ts": _RETIREMENT_TS}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=0, graph_nodes=399,
            expected_retirement=frozenset(_RETIREMENT_BASELINE_IDS))
        self.assertTrue(any("靈魂結構誤傷" in f for f in fails),
                        "trait/essence 異動必須報破線：%s" % fails)

    def test_key_node_in_retirement_set_is_not_a_break(self):
        """關鍵反例若在預期退役集合內且轉入 weakening ⇒ 不報「關鍵反例」。"""
        wid = "6a92c906b245458cb0c4c64ea214f2eb"
        fails = CLI.assess(
            new_events=[{"node_id": wid, "node_type": "value",
                         "lifecycle_state": "weakening",
                         "ts": "2026-10-25T02:00:22.072571+00:00"}],
            prompt_diffs=[], key_node="6a92c906b245",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=frozenset({wid}))
        self.assertFalse(any("關鍵反例" in f for f in fails),
                         "預期退役集合內的關鍵反例不得報破線：%s" % fails)
        self.assertEqual(fails, [], "預期代謝不該有任何失敗項：%s" % fails)

    def test_key_node_outside_retirement_set_still_fails(self):
        """同一顆 id，若**未**列入預期退役集合 ⇒ 必須報「關鍵反例」。"""
        wid = "6a92c906b245458cb0c4c64ea214f2eb"
        fails = CLI.assess(
            new_events=[{"node_id": wid, "node_type": "value",
                         "lifecycle_state": "weakening", "ts": _RETIREMENT_TS}],
            prompt_diffs=[], key_node="6a92c906b245",
            weak_before=0, weak_after=1, graph_nodes=399)
        self.assertTrue(any("關鍵反例" in f for f in fails),
                        "未列入預期退役集合的關鍵反例必須報破線：%s" % fails)

    def test_dormant_target_in_retirement_set_still_fails(self):
        """白名單 id 轉入 dormant ⇒ 仍算破線（退役＝弱化，不是休眠/取代）。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        fails = CLI.assess(
            new_events=[{"node_id": wid, "node_type": "value",
                         "lifecycle_state": "dormant", "ts": _RETIREMENT_TS}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=0, graph_nodes=399,
            expected_retirement=frozenset({wid}))
        self.assertTrue(any("轉變目標含" in f for f in fails),
                        "白名單內的 dormant 仍必須報破線：%s" % fails)

    # ── 退役窗（時間下界）：Owner 2026-10-23 裁示核准 ────────────────────
    #   「僅當該節點落在推導出的退役白名單集合內、且轉變狀態嚴格為 weakening
    #    時方得免除報警；**若其在 10/24 槽前提前異動**，或轉入 dormant 與
    #    superseded，CLI 仍必須堅決熔斷報警。」

    def test_retirement_before_approved_slot_is_a_break(self):
        """🔴 提前早衰：白名單 value 在核准退役槽**之前**轉入 weakening ⇒ 必須報破線。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        early = {"node_id": wid, "node_type": "value", "lifecycle_state": "weakening",
                 "ts": "2026-10-24T02:00:22.072571+00:00"}   # 10/23 22:00 槽（早於 10/24 槽）
        self.assertFalse(
            CLI.is_expected_metabolism(early, frozenset({wid})),
            "早於核准退役槽的弱化不得被歸類為預期代謝")
        fails = CLI.assess(
            new_events=[early], prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=frozenset({wid}))
        self.assertTrue(any("靈魂結構誤傷" in f for f in fails),
                        "提前早衰必須報破線：%s" % fails)

    def test_retirement_at_or_after_approved_slot_is_expected(self):
        """退役窗之內（10/24 22:00 EDT ＝ 2026-10-25T02:00:00Z 起）的弱化 ⇒ 預期代謝。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        onslot = {"node_id": wid, "node_type": "value", "lifecycle_state": "weakening",
                  "ts": "2026-10-25T02:00:22.072571+00:00"}   # 10/24 22:00 槽
        self.assertTrue(CLI.is_expected_metabolism(onslot, frozenset({wid})),
                        "核准退役槽的弱化必須被視為預期代謝")
        fails = CLI.assess(
            new_events=[onslot], prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=frozenset({wid}))
        self.assertEqual(fails, [], "退役窗內的弱化是預期代謝，不是破線：%s" % fails)

    def test_dormant_is_never_expected_even_after_approved_slot(self):
        """dormant／superseded 即使在集合內且在退役窗之後 ⇒ 仍不是預期代謝。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        for state in ("dormant", "superseded"):
            ev = {"node_id": wid, "node_type": "value", "lifecycle_state": state,
                  "ts": "2026-10-25T02:00:22.072571+00:00"}
            self.assertFalse(CLI.is_expected_metabolism(ev, frozenset({wid})),
                             "%s 不得被視為預期代謝" % state)
            fails = CLI.assess(
                new_events=[ev], prompt_diffs=[], key_node="KEY",
                weak_before=0, weak_after=0, graph_nodes=399,
                expected_retirement=frozenset({wid}))
            self.assertTrue(any("轉變目標含" in f for f in fails),
                            "%s 必須報破線：%s" % (state, fails))

    def test_select_expected_hits_agrees_with_predicate(self):
        """main() 的「本次命中」與 predicate 必須同源（不得各自實作條件）。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        early = {"node_id": wid, "node_type": "value", "lifecycle_state": "weakening",
                 "ts": "2026-10-24T02:00:22.072571+00:00"}
        onslot = {"node_id": wid, "node_type": "value", "lifecycle_state": "weakening",
                  "ts": "2026-10-25T02:00:22.072571+00:00"}
        pat = {"node_id": "p" * 32, "node_type": "pattern", "lifecycle_state": "weakening",
               "ts": "2026-10-25T02:00:22.072571+00:00"}
        events = [early, onslot, pat]
        ids = frozenset({wid})
        hits = CLI.select_expected_hits(events, ids)
        self.assertEqual([e["ts"] for e in hits], [onslot["ts"]],
                         "只允許退役窗之後的弱化命中：%s" % [e["ts"] for e in hits])
        for e in events:
            self.assertEqual(e in hits, CLI.is_expected_metabolism(e, ids),
                             "select_expected_hits 與 predicate 對 %s 的判定不一致" % e["ts"])

    def test_retirement_window_constant_is_declared(self):
        """核准退役窗是 Owner 宣告值，不得被悄悄改動或移除。"""
        self.assertEqual(
            CLI.EXPECTED_RETIREMENT_NOT_BEFORE, "2026-10-25T02:00:00+00:00",
            "核准退役窗（10/24 22:00 EDT ＝ 2026-10-25T02:00:00Z）不得被改動")

    def test_weak_prefix_delta_must_equal_expected_count(self):
        """預期退役 1 筆但弱化包裝增量是 2 ⇒ 必須報「弱化包裝計數」。"""
        wid = _RETIREMENT_BASELINE_IDS[0]
        fails = CLI.assess(
            new_events=[{"node_id": wid, "node_type": "value",
                         "lifecycle_state": "weakening", "ts": _RETIREMENT_TS}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=2, graph_nodes=399,
            expected_retirement=frozenset({wid}))
        self.assertTrue(any("弱化包裝" in f for f in fails),
                        "增量 2 ≠ 預期退役 1 筆 ⇒ 必須報：%s" % fails)

    def test_weak_prefix_delta_zero_with_no_expected_is_ok(self):
        """0 預期退役、weak 0 → 0 ⇒ 無失敗項。"""
        fails = CLI.assess(
            new_events=[{"node_id": "p" * 32, "node_type": "pattern",
                         "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=0, graph_nodes=399)
        self.assertEqual(fails, [], "無預期退役且無增量 ⇒ 不該有失敗項：%s" % fails)

    def test_baseline_violation_is_self_checking(self):
        """推導集合含一顆不在基線的 id ⇒ `baseline_violation()` 必須報非空。"""
        extra = "9" * 32
        self.assertNotIn(extra, CLI.EXPECTED_RETIREMENT_BASELINE)
        fails = CLI.baseline_violation({extra})
        self.assertTrue(fails, "超出核准基線（＝規則被放寬）必須被自我否決抓到")
        self.assertTrue(any("基線" in f for f in fails), fails)
        self.assertEqual(CLI.baseline_violation(set(CLI.EXPECTED_RETIREMENT_BASELINE)), [],
                         "核准基線本身不得被自我否決")

    def test_cli_does_not_import_decay_thresholds(self):
        """🔴 衰減門檻凍結：判準層不得引入時間門檻算術或另一個 repo 的模組。

        若有人日後「順手」把衰減門檻複製一份進判準層（例如「距今超過 N 天
        就算預期退役」），本守衛必須立刻轉紅。
        """
        src = CLI_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    low = a.name.lower()
                    if "timedelta" in low or "soul_elevation" in low:
                        offenders.append(("import", a.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "soul_elevation" in mod.lower():
                    offenders.append(("from-import", mod, node.lineno))
                for a in node.names:
                    if "timedelta" in a.name.lower():
                        offenders.append(("from-import", "%s.%s" % (mod, a.name),
                                          node.lineno))
            elif isinstance(node, ast.Name):
                if "timedelta" in node.id.lower():
                    offenders.append(("name", node.id, node.lineno))
            elif isinstance(node, ast.Attribute):
                if node.attr.lower() in ("days", "timedelta"):
                    offenders.append(("attribute", node.attr, node.lineno))
        self.assertEqual(offenders, [],
                         "判準層不得含時間門檻算術／另一個 repo 的衰減門檻：%s" % offenders)

    def test_prompt_diff_carveout_is_bounded(self):
        """🔴 ④ 的放行邊界：只有「**完全由預期代謝解釋**」的投影差異才放行。

        2026-10-23 實測：弱化節點的投影會把 `X` 渲染成 `我隐约觉得「X」`
        ⇒ 只要有 value 轉入 weakening，該 agent 的正文就必然逐位元不同。
        ⇒ 放行必須**窄**：命中數、弱化前綴增量、去裝飾後的正文三者都要對上。
        """
        prefix = CLI.WEAKENING_TONE_PREFIX
        before = "「A」\nB"
        after = "「A」\n%s「B」" % prefix
        diff = {"agent": "agent_x", "before_len": len(before),
                "after_len": len(after), "before_weak": 0, "after_weak": 1}
        # 完全由預期代謝解釋 ⇒ 放行
        self.assertTrue(CLI.prompt_diff_is_expected(diff, 1, before, after),
                        "只差弱化裝飾的投影差異必須被視為可解釋")
        # 命中數 0 ⇒ 不放行（沒有預期代謝卻有正文差異 ⇒ 破線）
        self.assertFalse(CLI.prompt_diff_is_expected(diff, 0, before, after),
                         "無預期代謝的正文差異不得放行")
        # 弱化前綴增量對不上命中數 ⇒ 不放行
        self.assertFalse(CLI.prompt_diff_is_expected(diff, 2, before, after),
                         "前綴增量與命中數不符不得放行")
        # 正文被改動（非弱化裝飾）⇒ 不放行
        self.assertFalse(CLI.prompt_diff_is_expected(
            diff, 1, before, "「A」\n%s「C」" % prefix),
            "正文被改動不得放行")
        # assess()：可解釋者不報、不可解釋者報
        events = [{"node_id": "p" * 32, "node_type": "pattern",
                   "lifecycle_state": "weakening"}]
        fails_ok = CLI.assess(
            new_events=events,
            prompt_diffs=[dict(diff, explained_by_expected_metabolism=True)],
            key_node="KEY", weak_before=0, weak_after=0, graph_nodes=399)
        self.assertEqual(fails_ok, [], "可解釋的投影差異不該報破線：%s" % fails_ok)
        fails_bad = CLI.assess(
            new_events=events,
            prompt_diffs=[dict(diff, explained_by_expected_metabolism=False)],
            key_node="KEY", weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("Prompt" in f for f in fails_bad),
                        "不可解釋的投影差異必須報破線：%s" % fails_bad)

    def test_no_src_module_imports_scripts_package(self):
        """變異 in-place 才安全的前提：`src/**` 內不得 import `scripts/`。"""
        hits = []
        src_root = REPO_ROOT / "src"
        for p in sorted(src_root.rglob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (OSError, SyntaxError) as exc:
                hits.append((str(p), "unreadable: %s" % exc))
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == "scripts" or a.name.startswith("scripts."):
                            hits.append((str(p), node.lineno, a.name))
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "scripts" or mod.startswith("scripts."):
                        hits.append((str(p), node.lineno, mod))
        self.assertEqual(hits, [],
                         "src/** 不得 import scripts/（變異 in-place 才安全）：%s" % hits)


class TestCliHasNoInPlaceWritePath(unittest.TestCase):
    """守衛 1a：CLI 不再提供任何就地寫入旗標（能力被移除，不是被條件擋住）。"""

    def test_in_place_flag_is_rejected(self):
        """`--in-place` 必須被 argparse 拒絕（SystemExit 2）。"""
        with self.assertRaises(SystemExit) as ctx:
            _run_cli("--in-place")
        self.assertEqual(ctx.exception.code, 2, "argparse 對未知旗標應以 2 退出")

    def test_data_dir_flag_is_rejected(self):
        """`--data-dir` 必須被 argparse 拒絕（該參數已改名為唯讀的 --source-dir）。"""
        with self.assertRaises(SystemExit) as ctx:
            _run_cli("--data-dir", str(PROD_DIR))
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_source_flag_is_read_only(self):
        """`--source-dir` 只被讀取與複製：來源目錄必須位元組不動。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            before = {p.name: _sha(p) for p in src.iterdir()}
            rc, out, err = _run_cli("--source-dir", str(src))
            after = {p.name: _sha(p) for p in src.iterdir()}
            self.assertEqual(before, after, "來源目錄不得被寫入")
            self.assertFalse(
                (src / "elevation_lifecycle_events.jsonl").exists(),
                "來源目錄不得出現 sidecar（= 有人寫進去了）",
            )
            self.assertEqual(rc, 0, out[-500:] + err[-500:])


class TestCliWritesOnlyToOwnSandbox(unittest.TestCase):
    """守衛 1b：唯一寫入目標是自建沙盒；生產與來源都不得被動到。"""

    def test_sandbox_only_write_and_production_untouched(self):
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            prod_before = _sha(PROD_SIDECAR)
            src_before = {p.name: _sha(p) for p in src.iterdir()}

            rc, out, err = _run_cli("--source-dir", str(src), "--json")
            self.assertEqual(rc, 0, out[-500:] + err[-500:])
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])

            # 唯一寫入目標在 temp 之下
            sandbox = pathlib.Path(data["sandbox_dir"])
            self.assertEqual(data["mode"], "sandbox-only")
            self.assertTrue(
                str(sandbox.resolve()).lower().startswith(
                    str(pathlib.Path(tempfile.gettempdir()).resolve()).lower()),
                "沙盒必須位於 temp 之下：%s" % sandbox)

            # 評估確實有跑（合成圖譜的 10 天 pattern 應轉 weakening）
            self.assertEqual(data["transitions"], 1, "合成圖譜應產生 1 筆轉變")
            self.assertEqual(data["sidecar_after"] - data["sidecar_before"], 1)
            self.assertGreater(data["elapsed_seconds"], 0.0)
            self.assertEqual(data["verdict"], "PASS", str(data.get("failures")))

            # 來源與生產都不得被動到
            self.assertEqual({p.name: _sha(p) for p in src.iterdir()}, src_before)
            self.assertEqual(_sha(PROD_SIDECAR), prod_before, "生產 sidecar 不得被改動")

    def test_production_sidecar_matches_module_import_baseline(self):
        """跨測試的生產基準檢查：模組匯入後生產 sidecar 不得被本 CLI 改動。"""
        self.assertEqual(
            _sha(PROD_SIDECAR), PROD_SIDECAR_SHA_BEFORE,
            "生產 sidecar 相對於『模組匯入時』的基準已被改動 —— 有東西寫進生產了",
        )

    def test_sandbox_is_cleaned_up(self):
        """沙盒必須在結束時刪除（不留殘留狀態）。"""
        before = set(pathlib.Path(tempfile.gettempdir()).glob("lifecycle_cli_sandbox_*"))
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            _run_cli("--source-dir", str(src))
        after = set(pathlib.Path(tempfile.gettempdir()).glob("lifecycle_cli_sandbox_*"))
        self.assertEqual(after - before, set(), "沙盒殘留：%s" % (after - before))


class TestNoServerNoNetwork(unittest.TestCase):
    """守衛 3：CLI 不啟伺服器、不發網路請求。"""

    def test_cli_imports_no_server_or_network_modules(self):
        src = CLI_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"subprocess", "socket", "requests", "httpx", "urllib",
                     "uvicorn", "fastapi", "asyncio", "multiprocessing"}
        hit = imported & forbidden
        self.assertFalse(hit, "CLI 不得 import 伺服器／網路模組：%s" % hit)

    def test_cli_never_calls_run_server(self):
        src = CLI_PATH.read_text(encoding="utf-8")
        self.assertNotIn("run_server", src, "CLI 不得啟動 server")
        self.assertNotIn("server_ops", src, "CLI 不得呼叫重啟腳本")

    def test_cli_has_no_write_outside_its_own_sandbox(self):
        """原始碼層面：CLI 的寫入點應只有報告檔一處。"""
        src = CLI_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        writes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in ("write_text", "open"):
                    mode = None
                    if name == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = node.args[1].value
                    if name == "write_text" or (mode and "w" in mode and "r" not in mode):
                        writes.append((node.lineno, name, mode))
        self.assertLessEqual(len(writes), 1,
                             "CLI 的寫入點應只有報告檔一處：%s" % writes)


class TestSimulatedTimeMustBeTzAware(unittest.TestCase):
    """🔴 守衛 4（2026-10-22）：naive 評估時鐘必須被**硬拒**，不得進入假綠路徑。

    實測事故（2026-10-22 11:30 EDT 值守複核）：
    `--simulated-time 2026-10-23T02:00:00`（naive）⇒ 引擎 `_days_between` 拿
    offset-aware anchor 減 offset-naive now ⇒ `TypeError` ⇒ 被
    `evaluate_lifecycle_all` 的 fail-closed `except` 吞掉回 `[]` ⇒ CLI 判
    「0 筆新轉變但圖非空 = 冪等 no-op」⇒ **VERDICT: PASS（假綠）**。
    對照組（同一張生產圖譜）：naive ⇒ 225→225／PASS；`Z` ⇒ 225→239／FAIL。
    """

    def test_naive_clock_is_refused_with_nonzero_exit(self):
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            rc, out, err = _run_cli("--source-dir", str(src),
                                   "--simulated-time", "2026-10-23T02:00:00")
            self.assertEqual(rc, 2, "naive 時鐘必須以 2 退出：%s / %s" % (out[-400:], err[-400:]))
            self.assertIn("tz-aware", err)
            self.assertIn("時區", err)

    def test_refused_run_produces_no_verdict_at_all(self):
        """**關鍵**：被拒絕的執行不得輸出任何 VERDICT —— 寧可沒有結果，也不要假綠。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            rc, out, err = _run_cli("--source-dir", str(src),
                                   "--simulated-time", "2026-10-23T02:00:00")
            self.assertNotIn("VERDICT", out, "被拒絕的執行不得產生 VERDICT：%s" % out[-400:])
            self.assertEqual(rc, 2)

    def test_tz_aware_clock_is_accepted(self):
        """帶時區的時鐘（含 `Z` 形式）必須照常評估。"""
        for raw in ("2026-10-23T02:00:00+00:00", "2026-10-23T02:00:00Z"):
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
                    src = _synthetic_graph(pathlib.Path(td), ref=_SIMULATED_CLOCK)
                    rc, out, err = _run_cli("--source-dir", str(src),
                                           "--simulated-time", raw, "--json")
                    self.assertEqual(rc, 0, out[-400:] + err[-400:])
                    data = json.loads(out[out.index("{"):out.rindex("}") + 1])
                    self.assertEqual(data["verdict"], "PASS", str(data.get("failures")))
                    self.assertEqual(data["transitions"], 1)

    def test_fixture_is_anchored_to_evaluation_clock_not_wall_clock(self):
        """🔴 時間炸彈守衛（2026-10-22 22:00 EDT 實測事故）：合成 fixture 的時間戳
        必須由**評估時鐘**推導，不得貼近真實 now。

        真實事故：fixture 用「真實 now − 10 天」而評估時鐘寫死 `2026-10-23T02:00:00Z`
        ⇒ 真實時鐘越過評估時鐘後 elapsed 掉到 9.98 天（整日取整 9 < 10 門檻）
        ⇒ 守衛從「綠」變成「必紅」。本測試把錨點契約釘死。
        """
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td), ref=_SIMULATED_CLOCK)
            node = json.loads(
                (src / "elevation_nodes.jsonl").read_text(encoding="utf-8").strip()
            )
            expected = _iso(_SYNTHETIC_STALE_DAYS, _SIMULATED_CLOCK)
            self.assertEqual(node["last_support_ts"], expected)
            self.assertEqual(node["created_ts"], expected)
            real_now = datetime.now(timezone.utc)
            # 🔴 用 fromisoformat 解析（`strptime` 會把 `_ISO` 裡的 "+00:00" 當**字面量**
            # 而不掛 tzinfo ⇒ 與 aware 相減會 TypeError —— 與 2026-10-22 11:30 那起
            # 引擎事故同型，勿退回去用 strptime）。
            parsed = datetime.fromisoformat(node["last_support_ts"])
            self.assertIsNotNone(parsed.tzinfo, "合成節點的時間戳必須帶時區")
            gap = abs((parsed - real_now).total_seconds())
            self.assertGreater(gap, 86400.0,
                               "合成節點時間戳貼近真實 now ⇒ 又是時間炸彈：%s" % node["last_support_ts"])

    def test_transition_is_stable_across_evaluation_clocks(self):
        """同一張 fixture 在多個評估時鐘下都必須轉 1 筆 ⇒ 結果不受真實時鐘綁定。"""
        for raw in ("2026-10-23T02:00:00Z", "2026-10-24T02:00:00Z", "2026-11-23T02:00:00Z"):
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
                    src = _synthetic_graph(pathlib.Path(td), ref=_SIMULATED_CLOCK)
                    rc, out, err = _run_cli("--source-dir", str(src),
                                           "--simulated-time", raw, "--json")
                    self.assertEqual(rc, 0, out[-400:] + err[-400:])
                    data = json.loads(out[out.index("{"):out.rindex("}") + 1])
                    self.assertEqual(data["transitions"], 1, str(data))

    def test_garbage_clock_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            rc, out, err = _run_cli("--source-dir", str(src),
                                   "--simulated-time", "not-a-timestamp")
            self.assertEqual(rc, 2, out[-300:] + err[-300:])
            self.assertIn("ISO 8601", err)


class TestKeyNodePrefixMatching(unittest.TestCase):
    """🔴 守衛 5（2026-10-22）：關鍵反例比對必須是**前綴語意**。

    實測缺陷：`DEFAULT_KEY_NODE = "6a92c906b245"` 是 **12 字元前綴**，真實事件的
    `node_id` 是 **32 字元完整 id**（`6a92c906b245458cb0c4c64ea214f2eb`）。舊碼用
    `==` 精確比對 ⇒ 守衛 ③ 在真實資料上**恆為 False（完全空轉）**。
    舊單元測試餵的是假 id `"KEY"`（精確比對成立）⇒ 測試綠但守衛空轉。

    端到端實證（拿生產圖譜跑 `2026-10-25T02:00:00Z`）：關鍵反例**真的會被轉**
    1 次 —— 舊守衛報 0（漏報真紅燈），修完後報 1（正確觸發 ③）。
    """

    REAL_FULL_ID = "6a92c906b245458cb0c4c64ea214f2eb"
    REAL_PREFIX = "6a92c906b245"

    def test_full_32char_id_is_matched_by_12char_prefix(self):
        fails = CLI.assess(
            new_events=[{"node_id": self.REAL_FULL_ID, "node_type": "pattern",
                         "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node=self.REAL_PREFIX,
            weak_before=0, weak_after=0, graph_nodes=708)
        self.assertTrue(any("關鍵反例" in f for f in fails),
                        "完整 id 必須被前綴命中（舊碼會漏）: %s" % fails)

    def test_exact_id_still_matches(self):
        fails = CLI.assess(
            new_events=[{"node_id": "KEY", "node_type": "pattern",
                         "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=0, graph_nodes=399)
        self.assertTrue(any("關鍵反例" in f for f in fails), fails)

    def test_unrelated_node_does_not_match(self):
        fails = CLI.assess(
            new_events=[{"node_id": "ffffffffffffffffffffffffffffffff",
                         "node_type": "pattern", "lifecycle_state": "weakening"}],
            prompt_diffs=[], key_node=self.REAL_PREFIX,
            weak_before=0, weak_after=0, graph_nodes=708)
        self.assertFalse(any("關鍵反例" in f for f in fails),
                         "無關節點不得誤判為關鍵反例: %s" % fails)

    def test_cli_result_counts_key_node_transitions_by_prefix(self):
        """端到端：CLI 的 `key_node_transitions` 必須用前綴比對計算。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            node_id = json.loads(
                (src / "elevation_nodes.jsonl").read_text(encoding="utf-8").strip()
            )["node_id"]
            prefix = node_id[:12]
            rc, out, err = _run_cli("--source-dir", str(src), "--key-node", prefix,
                                   "--json")
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertEqual(data["key_node_transitions"], 1,
                             "前綴比對必須數到那筆轉變：%s" % data)
            self.assertTrue(any("關鍵反例" in f for f in data["failures"]), data)


class TestEvaluationExceptionIsVisible(unittest.TestCase):
    """🔴 守衛 6（2026-10-22）：**「評估失敗」不得被偽裝成「冪等 no-op」**。

    `evaluate_lifecycle_all` 是 fail-closed 的（`except Exception` → warning → 回 `[]`），
    與「真的 0 筆轉變」在回傳值上**長得一模一樣**。⇒ CLI 必須從日誌把例外撈出來，
    否則會出現「CLI 報 PASS，實際上根本没評估」。
    """

    def _run_with_failing_evaluation(self, td):
        src = _synthetic_graph(pathlib.Path(td))
        original = CLI.evaluate_lifecycle_all

        def _fake_fail(store_dir=None, now_ts=None):
            logger = logging.getLogger("soul_os.inner_life.elevation_adapter")
            try:
                raise TypeError("can't subtract offset-naive and offset-aware datetimes")
            except Exception as exc:
                logger.warning(
                    "evaluate_lifecycle_all failed (不影響主路徑): %s: %s",
                    type(exc).__name__, exc, exc_info=True)
            return []

        CLI.evaluate_lifecycle_all = _fake_fail
        try:
            return _run_cli("--source-dir", str(src), "--json")
        finally:
            CLI.evaluate_lifecycle_all = original

    def test_eval_exception_makes_verdict_fail(self):
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            rc, out, err = self._run_with_failing_evaluation(td)
            self.assertNotEqual(rc, 0, "評估拋例外時不得回 0")
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertEqual(data["verdict"], "FAIL", str(data))
            self.assertTrue(data["eval_error"], "eval_error 必須非空")
            self.assertTrue(any("拋例外" in f for f in data["failures"]), data["failures"])

    def test_eval_exception_disables_no_op_note(self):
        """例外發生時不得標成「冪等 no-op」——那是假綠的說辭。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            rc, out, err = self._run_with_failing_evaluation(td)
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertFalse(data["no_op_note"], data)

    def test_undelivered_transitions_are_flagged(self):
        """🔴 評估回傳了轉變、但 sidecar 沒增加 ⇒ 轉變沒落地 ⇒ 必須 FAIL。

        這是「0 筆新轉變」的**第三種成因**：`evaluate_lifecycle_all` 回傳了 events，
        但 `_append_jsonl` 寫入失敗（adapter 只發 warning，不回 0）⇒ sidecar 增量 0
        ⇒ 舊邏輯會判成「冪等 no-op = PASS」。
        """
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_graph(pathlib.Path(td))
            original = CLI.evaluate_lifecycle_all

            def _fake_return_events_but_write_nothing(store_dir=None, now_ts=None):
                return [{"node_id": "x" * 32, "node_type": "pattern",
                         "lifecycle_state": "weakening"}]

            CLI.evaluate_lifecycle_all = _fake_return_events_but_write_nothing
            try:
                rc, out, err = _run_cli("--source-dir", str(src), "--json")
            finally:
                CLI.evaluate_lifecycle_all = original
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertEqual(data["undelivered_transitions"], 1, data)
            self.assertNotEqual(rc, 0, "轉變沒落地時不得回 0")
            self.assertEqual(data["verdict"], "FAIL", str(data))
            self.assertTrue(any("未落地" in f for f in data["failures"]), data["failures"])
            self.assertFalse(data["no_op_note"], data)


class TestNamingHygiene(unittest.TestCase):
    """CLI 放在 scripts/，不得被 pytest 當成測試檔收集。"""

    def test_cli_filename_is_not_collected_by_pytest(self):
        self.assertFalse(CLI_PATH.name.startswith("test_"),
                         "scripts/ 下的腳本不得以 test_ 開頭（會被 pytest 收集）")
        self.assertFalse(CLI_PATH.name.startswith("manual_"),
                         "manual_ 前綴另有語意（原本的 test_*.py 改名而來），勿混用")


# ── 預期退役 cohort 台帳 ＋ 三分類判定（Owner 2026-10-25）──────────────────
#
# 🔴 **為什麼需要「台帳」而不是單一 cohort**：
# 生產圖譜裡「建立後從未被背書的 value」**每天都在產生新 cohort**
# （10/11: 10 顆、10/18: 10 顆、10/19: 10 顆、10/20: 1 顆、10/21: 19 顆、
# 10/22: 38 顆、10/23: 46 顆、10/24: 58 顆、10/25: 10 顆）
# ⇒ 單一 cohort 白名單是一台**結構性的假紅產生器**（每個 night slot 都會再炸
# 一次，而它其實是 Owner 親自宣告過的「預期代謝」）。
#
# 🔴 **三分類**（Owner 2026-10-23 裁定「改判準」的延伸）：
#   expected（已核准）→ pending（結構同構但**未核准**）→ breaks（真破線）
# ⚠️ pending **不是**「任何未被核准的 value 弱化」——那會架空一票否決；
#    pending 必須滿足**窄版四條件同構**（見 `_is_pending_retirement`）。

#: 合成 fixture 的評估時鐘（固定 ⇒ 不受真實時鐘漂移影響）。
_LEDGER_CLOCK = "2026-10-23T02:00:00+00:00"
#: 一個**不在台帳內**的建立日（台帳涵蓋 2026-10-11 與 2026-10-18～10-25）。
_PENDING_CREATED = "2026-10-01T08:00:00+00:00"
#: 台帳**有**的建立日，但節點不在核准清單內（⇒ 觸發逐 cohort 自我否決）。
_ROGUE_LEDGER_DATE = "2026-10-19"


def _synthetic_value_graph(root: pathlib.Path, created_ts: str,
                           last_support_ts: str = None,
                           node_id: str = None,
                           content: str = "清晨的第一口空氣") -> pathlib.Path:
    """合成圖：1 顆 **value** 節點（建立後從未被背書）⇒ 會在評估時鐘前轉 weakening。

    `last_support_ts` 給另一個值 ⇒ 節點「曾被背書過」⇒ 結構**不同構**
    ⇒ 依判準必須是 **breaks**（不是 pending）。
    """
    store = root / "synthetic_value_graph"
    store.mkdir(parents=True, exist_ok=True)
    node = {
        "node_id": node_id or uuid.uuid4().hex,
        "node_type": "value",
        "content": content,
        "agent_id": "agent_ledger",
        "candidate_node_type": None,
        "confidence": 0.5,
        "stability": 0.3,
        "valence": "neutral",
        "created_ts": created_ts,
        "last_support_ts": last_support_ts or created_ts,
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
        "lifecycle_state": "active",
        "superseded_by": None,
    }
    (store / "elevation_nodes.jsonl").write_text(
        json.dumps(node, ensure_ascii=False) + "\n", encoding="utf-8")
    (store / "elevation_edges.jsonl").write_text("", encoding="utf-8")
    return store


class TestRetirementCohortLedger(unittest.TestCase):
    """守衛：預期退役 cohort 台帳的**結構**與**自我否決**。"""

    def test_ledger_structure_is_valid(self):
        """(a) 每個 cohort 都有 date / nodes / not_before；date 唯一；節點非空；
        `not_before` 必須帶時區；同一節點不得跨 cohort 重複。"""
        cohorts = CLI.EXPECTED_RETIREMENT_COHORTS
        self.assertTrue(cohorts, "台帳不得為空")
        dates = []
        seen_nodes = set()
        for c in cohorts:
            for key in ("date", "nodes", "not_before"):
                self.assertIn(key, c, "cohort 缺少 %s：%s" % (key, c))
            date = c["date"]
            dates.append(date)
            self.assertTrue(c["nodes"], "cohort %s 的 nodes 不得為空" % date)
            # not_before 必須帶時區（判準層只比對宣告式時刻常數）
            parsed = datetime.fromisoformat(str(c["not_before"]))
            self.assertIsNotNone(parsed.tzinfo,
                                 "cohort %s 的 not_before 必須帶時區：%s"
                                 % (date, c["not_before"]))
            # 節點不得跨 cohort 重複（否則「逐節點的時間下界」會有歧義）
            overlap = seen_nodes & set(c["nodes"])
            self.assertEqual(overlap, set(),
                             "節點不得同時出現在多個 cohort：%s" % sorted(overlap))
            seen_nodes |= set(c["nodes"])
        self.assertEqual(len(dates), len(set(dates)),
                         "cohort 的 date 必須唯一：%s" % dates)

    def test_ledger_covers_the_measured_cohorts(self):
        """台帳必須涵蓋主大腦實測到的 9 批 cohort（10/11 ＋ 10/18～10/25）。

        ⚠️ 只檢查「⊇」（允許日後追加新 cohort），不檢查「＝」。
        """
        dates = {c["date"] for c in CLI.EXPECTED_RETIREMENT_COHORTS}
        measured = {"2026-10-11", "2026-10-18", "2026-10-19", "2026-10-20",
                    "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24",
                    "2026-10-25"}
        self.assertTrue(measured <= dates,
                        "台帳缺少實測到的 cohort：%s" % sorted(measured - dates))

    def test_ledger_self_negation_catches_per_cohort_overflow(self):
        """(b) 逐 cohort 的自我否決：某 cohort 的推導集合超出**該 cohort** 的
        核准清單 ⇒ `baseline_violation()` 必須非空。

        🔴 這個 fixture 是**刻意**挑的：違規節點在**聯集**層面是合法的
        （它被核准在另一個 cohort 底下）⇒ 只有「逐 cohort」檢查抓得到。
        ⇒ 若有人把逐 cohort 檢查改成聯集檢查，本守衛必須轉紅。
        """
        node_id = "a" * 32
        nodes = [_value_node(node_id, "2026-10-18T08:00:00+00:00")]
        edges = []
        custom = (
            {"date": "2026-10-11", "not_before": "2026-10-25T02:00:00+00:00",
             "nodes": frozenset({node_id})},      # 核准在 10-11 這批底下
            {"date": "2026-10-18", "not_before": "2026-10-26T02:00:00+00:00",
             "nodes": frozenset()},               # 但推導落在 10-18 這批
        )
        derived = CLI.expected_retirement_sets_by_cohort(nodes, edges, cohorts=custom)
        self.assertEqual(derived.get("2026-10-18"), {node_id},
                         "推導必須落在 2026-10-18 這批（測試資料有問題）")
        self.assertEqual(derived.get("2026-10-11"), set(),
                         "2026-10-11 這批不該推導出任何節點")
        fails = CLI.baseline_violation(derived, cohorts=custom)
        self.assertTrue(fails, "推導超出該 cohort 的核准清單 ⇒ 必須被自我否決抓到")
        self.assertTrue(any("2026-10-18" in f for f in fails), fails)
        # 聯集語意抓不到這個違規 ⇒ 證明「逐 cohort 檢查」不可被聯集檢查取代。
        self.assertEqual(CLI.baseline_violation({node_id}, cohorts=custom), [],
                         "聯集語意抓不到逐 cohort 的違規（這正是必須逐 cohort 檢查的原因）")

    def test_ledger_self_negation_makes_the_cli_fail(self):
        """(b, 端到端) 一顆「落在台帳建立日、卻不在核准清單內」的合規 value
        ⇒ 逐 cohort 自我否決 ⇒ `VERDICT: FAIL` ＋ exit 1。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_value_graph(
                pathlib.Path(td), "%sT08:00:00+00:00" % _ROGUE_LEDGER_DATE)
            rc, out, err = _run_cli("--source-dir", str(src),
                                    "--simulated-time", _LEDGER_CLOCK, "--json")
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertEqual(data["verdict"], "FAIL", str(data))
            self.assertEqual(rc, 1, out[-500:] + err[-500:])
            self.assertTrue(any("基線" in f for f in data["failures"]),
                            "必須報「推導超出核准基線」：%s" % data["failures"])


class TestThreeWayRetirementClassification(unittest.TestCase):
    """守衛：expected / pending / breaks 三分類。"""

    def _approved_node(self, cohort_date="2026-10-18"):
        """取台帳裡某個 cohort 的**第一顆**核准節點（不手抄 id）。"""
        for c in CLI.EXPECTED_RETIREMENT_COHORTS:
            if c["date"] == cohort_date:
                return sorted(c["nodes"])[0]
        raise AssertionError("台帳沒有 cohort %s" % cohort_date)

    def test_approved_cohort_value_retirement_is_expected(self):
        """(c) 已核准 cohort（2026-10-18）內的 value 轉變 ⇒ **expected**
        （不是 pending、不是 break、不是失敗）。"""
        wid = self._approved_node("2026-10-18")
        nodes = [_value_node(wid, "2026-10-18T08:00:00+00:00")]
        edges = []
        event = {"node_id": wid, "node_type": "value", "lifecycle_state": "weakening",
                 "ts": "2026-10-26T02:00:22.072571+00:00"}
        expected, pending, breaks = CLI.classify_soul_retirements(
            [event], nodes, edges)
        self.assertEqual(expected, [event],
                         "已核准 cohort 的 value 弱化必須是 expected：%s" % expected)
        self.assertEqual(pending, [], "已核准的不得被算成 pending")
        self.assertEqual(breaks, [], "已核准的不得被算成破線")
        fails = CLI.assess(
            new_events=[event], prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=CLI.approved_retirement_ids(),
            elevation_nodes=nodes, elevation_edges=edges)
        self.assertEqual(list(fails), [], "已核准 cohort 的退役不得報任何失敗項：%s"
                         % list(fails))

    def test_unapproved_isomorphic_retirement_is_pending_with_exit_3(self):
        """(d) 台帳**沒有的** cohort 的同構 value 轉變 ⇒ **pending**
        ⇒ `VERDICT: PENDING_COHORT` ＋ exit code **3**（不是 0、不是 1）。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_value_graph(pathlib.Path(td), _PENDING_CREATED)
            rc, out, err = _run_cli("--source-dir", str(src),
                                    "--simulated-time", _LEDGER_CLOCK, "--json")
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertEqual(data["verdict"], "PENDING_COHORT", str(data))
            self.assertEqual(rc, 3, "未核准的退役批次必須以 3 退出：%s / %s"
                             % (out[-800:], err[-400:]))
            self.assertEqual(data["failures"], [],
                             "pending 不得被混進破線清單：%s" % data["failures"])
            self.assertEqual(data["pending_retirements_count"], 1, str(data))
            self.assertEqual(data["pending_cohorts"][0]["date"], "2026-10-01",
                             str(data["pending_cohorts"]))
            self.assertEqual(data["pending_cohorts"][0]["count"], 1, str(data))

    def test_non_isomorphic_retirement_is_a_break_with_exit_1(self):
        """(e) **非同構**的 value 轉變（曾被背書過 ⇒ `last_support_ts !=
        created_ts`）⇒ **breaks** ⇒ `VERDICT: FAIL` ＋ exit 1。

        🔴 這是刻意的嚴格性：pending 的判準是**窄版四條件同構**；
        「被背書過才退役」的成員若未獲核准 ⇒ 一票否決（FAIL），不是 pending。
        """
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_value_graph(
                pathlib.Path(td), _PENDING_CREATED,
                last_support_ts="2026-10-10T08:00:00+00:00")
            rc, out, err = _run_cli("--source-dir", str(src),
                                    "--simulated-time", _LEDGER_CLOCK, "--json")
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertEqual(data["verdict"], "FAIL", str(data))
            self.assertEqual(rc, 1, "非同構的 value 弱化必須以 1 退出：%s / %s"
                             % (out[-800:], err[-400:]))
            self.assertTrue(any("靈魂結構誤傷" in f for f in data["failures"]),
                            "非同構的 value 弱化必須報靈魂結構誤傷：%s" % data["failures"])
            self.assertEqual(data["pending_retirements_count"], 0, str(data))

    def test_dormant_or_superseded_target_is_always_a_break(self):
        """(f) dormant／superseded 目標 ⇒ 一律 breaks（即使節點在核准清單內）。"""
        wid = self._approved_node("2026-10-18")
        nodes = [_value_node(wid, "2026-10-18T08:00:00+00:00")]
        edges = []
        for state in ("dormant", "superseded"):
            event = {"node_id": wid, "node_type": "value",
                     "lifecycle_state": state,
                     "ts": "2026-10-26T02:00:22.072571+00:00"}
            expected, pending, breaks = CLI.classify_soul_retirements(
                [event], nodes, edges)
            self.assertEqual(expected, [], "%s 不得被視為 expected" % state)
            self.assertEqual(pending, [], "%s 不得被視為 pending" % state)
            self.assertEqual(breaks, [event], "%s 必須是破線" % state)
            fails = CLI.assess(
                new_events=[event], prompt_diffs=[], key_node="KEY",
                weak_before=0, weak_after=0, graph_nodes=399,
                expected_retirement=CLI.approved_retirement_ids(),
                elevation_nodes=nodes, elevation_edges=edges)
            self.assertTrue(any("轉變目標含" in f for f in fails),
                            "%s 必須報破線：%s" % (state, list(fails)))

    def test_retirement_before_cohort_not_before_is_a_break(self):
        """(g) 提前早衰：已核准 cohort 的 value 在**該 cohort 的 `not_before`
        之前**轉變 ⇒ breaks（既有守衛只覆蓋 10/11 那批）。"""
        wid = self._approved_node("2026-10-18")
        nodes = [_value_node(wid, "2026-10-18T08:00:00+00:00")]
        edges = []
        early = {"node_id": wid, "node_type": "value", "lifecycle_state": "weakening",
                 "ts": "2026-10-25T02:00:22.072571+00:00"}   # 早於 10-18 的退役槽
        self.assertFalse(
            CLI.is_expected_metabolism(early, frozenset({wid})),
            "早於該 cohort 核准退役槽的弱化不得被歸類為預期代謝")
        expected, pending, breaks = CLI.classify_soul_retirements(
            [early], nodes, edges)
        self.assertEqual(expected, [], "提前早衰不得算成 expected")
        self.assertEqual(pending, [], "提前早衰不得算成 pending")
        self.assertEqual(breaks, [early], "提前早衰必須是破線")
        fails = CLI.assess(
            new_events=[early], prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=CLI.approved_retirement_ids(),
            elevation_nodes=nodes, elevation_edges=edges)
        self.assertTrue(any("靈魂結構誤傷" in f for f in fails),
                        "提前早衰必須報破線：%s" % list(fails))

    def test_pending_state_prints_cohorts_and_approval_hint(self):
        """(h) pending 狀態下，stdout 與報告檔都必須含 `pending_cohorts`
        與可執行的核准指示（CLI **不得**自行核准）。"""
        with tempfile.TemporaryDirectory(prefix="cli_guard_") as td:
            src = _synthetic_value_graph(pathlib.Path(td), _PENDING_CREATED)
            report = pathlib.Path(td) / "report.md"
            rc, out, err = _run_cli("--source-dir", str(src),
                                    "--simulated-time", _LEDGER_CLOCK,
                                    "--report", str(report))
            self.assertEqual(rc, 3, out[-800:] + err[-400:])
            self.assertIn("pending_cohorts", out,
                          "stdout 必須列出 pending_cohorts：%s" % out[-800:])
            self.assertIn("EXPECTED_RETIREMENT_COHORTS", out,
                          "stdout 必須指出核准位置：%s" % out[-800:])
            body = report.read_text(encoding="utf-8")
            self.assertIn("pending_cohorts", body,
                          "報告檔必須列出 pending_cohorts")
            self.assertIn("核准指示", body, "報告檔必須含核准指示")
            self.assertIn("EXPECTED_RETIREMENT_COHORTS", body,
                          "報告檔必須指出核准位置")

    def test_assess_result_exposes_pending_retirements(self):
        """`assess()` 的結果必須攜帶 pending 清單（且仍是 list ⇒ 既有守衛相容）。"""
        wid = self._approved_node("2026-10-18")
        nodes = [_value_node(wid, "2026-10-18T08:00:00+00:00"),
                 _value_node("b" * 32, _PENDING_CREATED)]
        edges = []
        event = {"node_id": "b" * 32, "node_type": "value",
                 "lifecycle_state": "weakening",
                 "ts": "2026-10-26T02:00:22.072571+00:00"}
        result = CLI.assess(
            new_events=[event], prompt_diffs=[], key_node="KEY",
            weak_before=0, weak_after=1, graph_nodes=399,
            expected_retirement=CLI.approved_retirement_ids(),
            elevation_nodes=nodes, elevation_edges=edges)
        self.assertIsInstance(result, list, "assess() 必須仍回傳 list（既有守衛相容）")
        self.assertEqual([e["node_id"] for e in result.pending_retirements],
                         ["b" * 32])
        self.assertEqual(list(result), [], "pending 不得報失敗項：%s" % list(result))

    def test_classification_is_exclusive_and_covers_pattern(self):
        """三分類必須**互斥**；`pattern` 轉變不屬於任何一類（正常演化）。"""
        wid = self._approved_node("2026-10-18")
        nodes = [_value_node(wid, "2026-10-18T08:00:00+00:00"),
                 _value_node("b" * 32, _PENDING_CREATED)]
        edges = []
        approved = {"node_id": wid, "node_type": "value",
                    "lifecycle_state": "weakening",
                    "ts": "2026-10-26T02:00:22.072571+00:00"}
        pending_ev = {"node_id": "b" * 32, "node_type": "value",
                      "lifecycle_state": "weakening",
                      "ts": "2026-10-26T02:00:22.072571+00:00"}
        breaking = {"node_id": "c" * 32, "node_type": "trait",
                    "lifecycle_state": "weakening",
                    "ts": "2026-10-26T02:00:22.072571+00:00"}
        pattern_ev = {"node_id": "d" * 32, "node_type": "pattern",
                      "lifecycle_state": "weakening",
                      "ts": "2026-10-26T02:00:22.072571+00:00"}
        expected, pending, breaks = CLI.classify_soul_retirements(
            [approved, pending_ev, breaking, pattern_ev], nodes, edges)
        self.assertEqual([e["node_id"] for e in expected], [wid])
        self.assertEqual([e["node_id"] for e in pending], ["b" * 32])
        self.assertEqual([e["node_id"] for e in breaks], ["c" * 32])
        self.assertEqual(len(expected) + len(pending) + len(breaks), 3,
                         "pattern 轉變不屬於任何一類（正常演化）")


if __name__ == "__main__":
    unittest.main()
