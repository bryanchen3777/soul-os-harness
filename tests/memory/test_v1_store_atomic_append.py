"""
tests/memory/test_v1_store_atomic_append.py
  — INFRA-V1-MIRROR-APPEND-RACE：memory v1 mirror 併寫整筆遺失守衛

缺陷（2026-10-22 實測重現）
---------------------------
`src/memory/v1/store.py::V1Store.add()` 原本用 **text-mode** ``open(path, "a")``
追加。Windows 上 text mode 有兩個獨立問題：

1. **newline translation**：``\\n`` 被轉成 ``\\r\\n`` ⇒ 檔案不是乾淨的
   LF-delimited JSONL（Owner 2026-10-22 裁示：全面收斂為 LF-only）。
2. 🔴 **併發 append 整筆遺失**（本票的核心）：MSVCRT ``_O_APPEND`` 在每次寫入前
   用「**該 handle 自己快取的檔案大小**」seek 到 EOF ⇒ 先後開啟的 handle 各自
   快取到**同一個過期大小** ⇒ 寫到同一偏移 ⇒ **後者整筆覆蓋前者**。

   實測（CPU 負載下，D2 測試：10 執行緒同時 append 同一內容）：
   ``v1_count`` = **8 或 9**，而非 10 ⇒ **整筆無痕跡消失**（零損壞行、可完整解析）。

   🔴 這**推翻了** P1 票 INFRA-CONCURRENT-APPEND-MUTEX 當時把「約 25 處
   text-mode append 點」列為 out-of-scope 的前提 —— 當時的理由是「單寫入者路徑」，
   而本測試證明這條路徑**有 10 個併發寫入者**。

修法
----
改走 ``src.fsio.atomic_append.append_bytes()``：**二元寫入（⇒ LF-only）＋ 行程內
互斥 ＋ Windows 核心層 ``FILE_APPEND_DATA`` 原子追加**（跨行程安全）。

⚠️ 為什麼**不**直接用 ``open(path, "ab")``：Windows 上 CPython 的
``open(path, "ab")`` 走 MSVCRT ``_O_APPEND``，**本身就不是原子追加** ——
「二元」不等於「原子」。那正是 INFRA-CONCURRENT-APPEND-MUTEX 實測定案的根因
（見 ``src/fsio/atomic_append.py`` 檔頭）。

這檔守的不變量
--------------
* **T1** LF-only：檔案位元組**不得**出現 ``\\r\\n``；每行恰以一個 ``\\n`` 結尾；
  檔案以 ``\\n`` 結尾。（**確定性**守衛：舊的 text-mode 寫法在 Windows 必產
  CRLF ⇒ 必定轉紅。）
* **T2** 結構守衛：``V1Store.add`` **不得**使用 text-mode ``open(..., "a")``，
  必須走 ``append_bytes``。（**確定性**守衛：讀原始碼，舊寫法必定轉紅。）
* **T3** 併發完整性：N 執行緒 × M 次 append ⇒ 檔案裡的完整行數 == N×M
  （負載敏感型；舊寫法在負載下會掉筆）。
* **T4** 可解析性：每一行都必須是合法 JSON ⇒ 不得出現 torn write。
"""
from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

from src.memory.v1.schema import Memory
from src.memory.v1.store import V1Store

_AGENT = "agent_v1_atomic_test"


def _mem(idx: int) -> Memory:
    return Memory(
        memory_id="m%03d" % idx,
        agent_id=_AGENT,
        content="Bry likes apples %03d" % idx,
        tags=["bryan"],
        created_at=1000.0 + idx,
    )


# ── T1：LF-only（確定性）─────────────────────────────────────────────


def test_t1_store_file_is_lf_only(tmp_path):
    """🔴 檔案必須是乾淨的 LF-delimited JSONL（不得有 CRLF）。

    這條是**確定性**守衛：舊的 text-mode ``open(path, "a")`` 在 Windows 上會把
    ``\\n`` 轉成 ``\\r\\n`` ⇒ 必定轉紅，不依賴任何負載。
    """
    store = V1Store(tmp_path / "data" / "memory", _AGENT)
    for i in range(5):
        store.add(_mem(i))

    raw = store.store_file.read_bytes()
    assert b"\r\n" not in raw, (
        "檔案出現 CRLF —— text-mode newline translation 未消滅：%r" % raw[:120]
    )
    assert raw.endswith(b"\n"), "檔案必須以單一 LF 結尾"
    lines = raw.split(b"\n")
    assert lines[-1] == b"", "檔案結尾不得有多餘位元組"
    assert all(l != b"" for l in lines[:-1]), "不得有空白行"


# ── T2：結構守衛（確定性）────────────────────────────────────────────


def test_t2_add_must_not_use_text_mode_append(tmp_path):
    """🔴 ``V1Store.add`` 不得用 text-mode ``open(..., "a")`` 追加。

    讀原始碼（AST）判定：舊寫法必定轉紅 ⇒ 這條是**確定性**守衛。
    語意理由見本檔檔頭：「二元不等於原子」，text-mode 同時帶來 newline
    translation 與併發遺失兩個問題。
    """
    src = Path(__file__).resolve().parents[2] / "src" / "memory" / "v1" / "store.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    add_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "add":
            add_fn = node
            break
    assert add_fn is not None, "找不到 V1Store.add"

    for node in ast.walk(add_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
        ):
            mode = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            assert mode != "a", (
                "V1Store.add 不得使用 text-mode open(..., 'a') —— "
                "必須走 src.fsio.atomic_append.append_bytes（二元＋原子＋LF-only）"
            )
            assert mode != "at", (
                "V1Store.add 不得使用 text-mode open(..., 'at') —— 同上"
            )


# ── T3：併發完整性（負載敏感型；修好後必定成立）───────────────────────


def test_t3_concurrent_appends_lose_no_record(tmp_path):
    """🔴 N 執行緒 × M 次 append ⇒ 完整行數必須剛好是 N×M。

    舊寫法在負載下會掉筆（實測 8/9 而非 10）⇒ 這條在負載下會轉紅；
    修好後走核心層原子追加 ⇒ **必定** N×M。
    """
    store = V1Store(tmp_path / "data" / "memory", _AGENT)
    n_threads, per_thread = 8, 25
    errors: list = []

    def worker(t: int) -> None:
        try:
            for k in range(per_thread):
                store.add(_mem(t * 1000 + k))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"append 拋例外：{errors}"
    raw = store.store_file.read_bytes()
    lines = [l for l in raw.split(b"\n") if l]
    assert len(lines) == n_threads * per_thread, (
        f"併發 append 遺失記錄：應為 {n_threads * per_thread} 筆，"
        f"實得 {len(lines)} 筆"
    )


# ── T4：可解析性（不得 torn write）──────────────────────────────────


def test_t4_every_record_is_parseable_and_complete(tmp_path):
    """🔴 每一行都必須是合法 JSON 且含完整欄位 ⇒ 不得 torn write。"""
    store = V1Store(tmp_path / "data" / "memory", _AGENT)
    for i in range(20):
        store.add(_mem(i))

    lines = [
        l for l in store.store_file.read_bytes().split(b"\n") if l
    ]
    assert len(lines) == 20
    for raw in lines:
        obj = json.loads(raw.decode("utf-8"))  # 解析失敗即 torn write
        assert obj.get("memory_id", "").startswith("m")
        assert obj.get("agent_id") == _AGENT
        assert "content" in obj
