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
import pathlib
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
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


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(_ISO)


def _synthetic_graph(root: pathlib.Path) -> pathlib.Path:
    """建立一個**合成**來源圖譜：1 顆 10 天沒人支持的 pattern ⇒ 應轉 weakening。

    合成（而非複製生產）才能讓「來源是否被寫入」成為可判定的觀察量。
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
        "created_ts": _iso(10.0),
        "last_support_ts": _iso(10.0),
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
                    src = _synthetic_graph(pathlib.Path(td))
                    rc, out, err = _run_cli("--source-dir", str(src),
                                           "--simulated-time", raw, "--json")
                    self.assertEqual(rc, 0, out[-400:] + err[-400:])
                    data = json.loads(out[out.index("{"):out.rindex("}") + 1])
                    self.assertEqual(data["verdict"], "PASS", str(data.get("failures")))
                    self.assertEqual(data["transitions"], 1)

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


if __name__ == "__main__":
    unittest.main()
