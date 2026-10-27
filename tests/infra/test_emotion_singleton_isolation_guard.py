# -*- coding: utf-8 -*-
"""tests/infra/test_emotion_singleton_isolation_guard.py

BUG-EMOTION-IMPORT-SIDE-EFFECT 的守衛（Owner 2026-10-24 核定工單
`logs/BUG-EMOTION-IMPORT-SIDE-EFFECT-WORK-ORDER.md` §驗收）。

背景（機制事實，非推測）
------------------------
`src/agent/emotion.py` 舊版在**模組層級**做兩件事：
  ① `DB_PATH = data_root() / "memory.db"` ⇒ import 期解析資料根（且 `data_root()`
     會 mkdir，並把 `src.paths._DATA_ROOT` 快取成**行程期單例**）；
  ② `emotion_engine = EmotionEngine()` ⇒ import 期就 `mkdir` + `sqlite3.connect`
     + `_init_schema()`（DDL）。
pytest 的順序是「**先收集（import 全部測試模組）→ 再跑測試**」，
`tests/conftest.py` 的 autouse fixture（設 `SOUL_OS_DATA_DIR` ＋ `reset_data_root()`）
是在**測試執行期**才生效 ⇒ **收集期** `data_root()` 仍回傳 `<repo>/data`（生產）
⇒ 測試套件在收集期就對**生產** `data/memory.db` 開了 sqlite 連線並跑 DDL。

本檔守的是：**import `src.agent.emotion` 不得有任何檔案系統／資料庫副作用**。

🔴 本檔**不使用子行程**（`tests/**` 禁用 `subprocess`，見
`tests/infra/test_no_production_spawn_guard.py` 的 R5）：改以
`importlib.util.spec_from_file_location` 載入**一份全新的模組物件**來觀察 import 期行為。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]   # tests/infra/xxx.py -> repo root
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EMOTION_PATH = REPO_ROOT / "src" / "agent" / "emotion.py"
PROXY_PATH = REPO_ROOT / "src" / "llm" / "proxy.py"


def _load_fresh_emotion_module():
    """以**獨立名稱**載入一份全新的 `emotion.py` 模組物件（不動 sys.modules 的既有項）。"""
    name = "soul_emotion_probe_%s" % uuid.uuid4().hex[:8]
    spec = importlib.util.spec_from_file_location(name, EMOTION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _count_connect(monkeypatch):
    """把 `sqlite3.connect` 包成計數器 ⇒ 回傳 (計數 dict, 原函式)。"""
    calls = []
    real = sqlite3.connect

    def counting(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", counting)
    return calls


def _module_level_tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


class TestEmotionImportPurity(unittest.TestCase):
    """G1～G6：import 期零副作用 ＋ 延遲建立 ＋ 匯入面乾淨。"""

    def setUp(self) -> None:
        self._saved_env = os.environ.get("SOUL_OS_DATA_DIR")

    def tearDown(self) -> None:
        from src.paths import reset_data_root

        if self._saved_env is None:
            os.environ.pop("SOUL_OS_DATA_DIR", None)
        else:
            os.environ["SOUL_OS_DATA_DIR"] = self._saved_env
        reset_data_root()

    # ── G1：import 不得開啟任何 sqlite 連線、不得建立資料根 ─────────────
    def test_g1_import_opens_no_sqlite_and_creates_no_data_root(self) -> None:
        """import `src.agent.emotion` ⇒ `sqlite3.connect` 呼叫次數 **0**、資料根**未被建立**。"""
        from src.paths import reset_data_root

        # 🔴 沙盒必須在 **repo 之外** ⇒ 測試不得在生產 `data/` 留下任何痕跡
        #    （本票要消滅的正是這種行為）⇒ 用系統 temp 目錄並自行清理。
        #    （`unittest.TestCase` 不支援 pytest fixture 注入 ⇒ 不能用 tmp_path。）
        tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="emo_guard_g1_"))
        # 資料根放在 temp 根底下的**事先不存在**的子路徑 ⇒ 任何建立痕跡都可量。
        sandbox = tmp_root / "data"

        calls = []
        real_connect = sqlite3.connect

        def counting(*args, **kwargs):
            calls.append(args)
            return real_connect(*args, **kwargs)

        os.environ["SOUL_OS_DATA_DIR"] = str(sandbox)
        reset_data_root()
        sqlite3.connect = counting          # type: ignore[assignment]
        try:
            _load_fresh_emotion_module()
        finally:
            sqlite3.connect = real_connect  # type: ignore[assignment]
            shutil.rmtree(tmp_root, ignore_errors=True)

        self.assertEqual(calls, [],
                         "import 期間不得開啟 sqlite 連線（收集期會打到生產 memory.db）")
        # 🔺 量「sandbox 內有沒有 memory.db」而不是「sandbox 存不存在」：
        #    `reset_data_root()` **本身**就會建立資料根 ⇒ 存在與否不是 import 的指標；
        #    真正會被 import 留下的痕跡是**那個 DB 檔**。
        self.assertFalse((sandbox / "memory.db").exists(),
                         "import 期間不得建立 DB 檔（import 期建檔＝寫入的起點）")

    # ── G2：延遲建立（import 期不得已有 `emotion_engine` 實例）──────────
    def test_g2_emotion_engine_is_not_created_at_import_time(self) -> None:
        """import 之後 `emotion_engine` **不得**已在模組 `__dict__` 裡 ⇒ 必須是延遲建立。"""
        from src.paths import reset_data_root

        tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="emo_guard_g2_"))
        # 資料根放在 temp 根底下的**事先不存在**的子路徑 ⇒ 任何建立痕跡都可量。
        sandbox = tmp_root / "data"
        os.environ["SOUL_OS_DATA_DIR"] = str(sandbox)
        reset_data_root()
        try:
            mod = _load_fresh_emotion_module()
            self.assertNotIn("emotion_engine", vars(mod),
                             "emotion_engine 不得在 import 期建立（必須第一次存取才建立）")
            self.assertFalse((sandbox / "memory.db").exists(),
                             "import 期間不得建立 DB 檔")
            # 第一次存取 ⇒ 才建立（且仍不得開連線）
            engine = mod.emotion_engine
            self.assertIsNotNone(engine)
            self.assertIn("emotion_engine", vars(mod),
                          "第一次存取後必須緩存到模組 __dict__（不得每次重建）")
        finally:
            from src.paths import reset_data_root
            reset_data_root()
            shutil.rmtree(tmp_root, ignore_errors=True)

    # ── G3：生產語意不變（第一次存取仍回可用實例；用到 DB 時才連線）────
    def test_g3_first_access_yields_usable_engine_and_defers_connect(self) -> None:
        """`emotion_engine` 第一次存取 ⇒ 可用的 `EmotionEngine` 實例；取用 `conn` 才開連線。"""
        import tempfile

        from src.agent.emotion import EmotionEngine
        from src.paths import reset_data_root

        with tempfile.TemporaryDirectory(prefix="emotion_guard_") as td:
            db = pathlib.Path(td) / "memory.db"
            eng = EmotionEngine(db_path=db)
            self.assertIsNone(eng._conn, "建構後不得已連線（惰性）")
            self.assertFalse(db.exists(), "建構後不得已建立 DB 檔")
            conn = eng.conn
            self.assertIsInstance(conn, sqlite3.Connection)
            self.assertTrue(db.exists(), "取用 conn 後才建立 DB 檔")
            # 第二次取用 ⇒ 同一條連線（不得重開）
            self.assertIs(conn, eng.conn, "conn 必須是同一條長生命週期連線")
            # 🔴 Windows：檔案被連線占用時無法刪除 ⇒ 先關連線。
            conn.close()

    # ── G4：proxy 不得有模組層匯入（AST）──────────────────────────────
    def test_g4_proxy_has_no_module_level_emotion_engine_import(self) -> None:
        """`src/llm/proxy.py` 不得在**模組層級**匯入 `emotion_engine`。"""
        tree = _module_level_tree(PROXY_PATH)
        offenders = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.module or "") == "src.agent.emotion":
                for alias in node.names:
                    if alias.name == "emotion_engine":
                        offenders.append((node.lineno, alias.name))
        self.assertEqual(offenders, [],
                         "proxy 不得模組層匯入 emotion_engine（會提前觸發 singleton 建立）：%s" % offenders)

    # ── G5：emotion.py 模組層級不得呼叫 `data_root()`（AST）────────────
    def test_g5_emotion_module_level_never_calls_data_root(self) -> None:
        """`src/agent/emotion.py` 的**模組層級**不得呼叫 `data_root()`。"""
        tree = _module_level_tree(EMOTION_PATH)
        offenders = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id == "data_root":
                    offenders.append(getattr(sub, "lineno", "?"))
        self.assertEqual(offenders, [],
                         "模組層級不得呼叫 data_root()（import 期就解析並快取資料根）：%s" % offenders)
        # 必須提供模組層 __getattr__（PEP 562）供延遲屬性
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.assertIn("__getattr__", names,
                      "必須提供模組層 __getattr__（PEP 562）以延遲提供 DB_PATH／emotion_engine")

    # ── G7：模組內部**不得**以全域名 `emotion_engine` 引用（AST）─────────
    def test_g7_module_never_uses_bare_global_emotion_engine(self) -> None:
        """`src/agent/emotion.py` 的函式**不得**以全域名 `emotion_engine` 查找。

        🔴 **為什麼**：Python 的全域名查找**不會**走模組層的 `__getattr__`
        （PEP 562 只攔到 `模組屬性存取`）⇒ 模組內部若直接用全域名
        `emotion_engine`，只要沒有人先做過 `module.emotion_engine`，就會 **NameError**。
        而該處的 `except Exception` 會把這個錯誤吞掉 ⇒
        **親密衰減靜默失效**且無人知。
        ⇒ 內部一律用 `_engine()`（本守衛釘死這條）。
        """
        tree = _module_level_tree(EMOTION_PATH)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ("__getattr__", "_engine"):
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id == "emotion_engine" \
                            and isinstance(sub.ctx, ast.Load):
                        offenders.append((node.name, getattr(sub, "lineno", "?")))
        self.assertEqual(offenders, [],
                         "模組內部不得以全域名引用 emotion_engine（會 NameError）：%s" % offenders)

    # ── G8：`_engine()` 不依賴模組屬性存取即可用（行為守衛）────────────
    def test_g8_engine_accessor_works_without_module_attribute_access(self) -> None:
        """新載入模組獨立一份（未經過任何模組屬性存取）⇒ `_engine()` 仍可用。"""
        from src.paths import reset_data_root

        tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="emo_guard_g8_"))
        # 資料根放在 temp 根底下的**事先不存在**的子路徑 ⇒ 任何建立痕跡都可量。
        sandbox = tmp_root / "data"
        os.environ["SOUL_OS_DATA_DIR"] = str(sandbox)
        reset_data_root()
        try:
            mod = _load_fresh_emotion_module()
            self.assertNotIn("emotion_engine", vars(mod))
            engine = mod._engine()
            self.assertIsNotNone(engine)
            self.assertIn("emotion_engine", vars(mod), "_engine() 必須緩存到模組 globals")
            self.assertIs(engine, mod._engine(), "必須是同一個 singleton")
        finally:
            from src.paths import reset_data_root
            reset_data_root()
            shutil.rmtree(tmp_root, ignore_errors=True)

    # ── G6：`__init__` 不得開連線（AST）───────────────────────────────
    def test_g6_engine_init_does_not_connect(self) -> None:
        """`EmotionEngine.__init__` 不得 `sqlite3.connect`（連線必須在 `conn` 屬性裡惰性建立）。"""
        tree = _module_level_tree(EMOTION_PATH)
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "EmotionEngine")
        init = next(n for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        offenders = []
        for sub in ast.walk(init):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Attribute) and f.attr == "connect":
                    offenders.append(getattr(sub, "lineno", "?"))
                if isinstance(f, ast.Name) and f.id in ("sqlite3_connect", "connect"):
                    offenders.append(getattr(sub, "lineno", "?"))
        self.assertEqual(offenders, [],
                         "__init__ 不得開 sqlite 連線（建構期副作用）：%s" % offenders)


if __name__ == "__main__":
    unittest.main()
