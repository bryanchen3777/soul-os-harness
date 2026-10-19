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


class TestNamingHygiene(unittest.TestCase):
    """CLI 放在 scripts/，不得被 pytest 當成測試檔收集。"""

    def test_cli_filename_is_not_collected_by_pytest(self):
        self.assertFalse(CLI_PATH.name.startswith("test_"),
                         "scripts/ 下的腳本不得以 test_ 開頭（會被 pytest 收集）")
        self.assertFalse(CLI_PATH.name.startswith("manual_"),
                         "manual_ 前綴另有語意（原本的 test_*.py 改名而來），勿混用")


if __name__ == "__main__":
    unittest.main()
