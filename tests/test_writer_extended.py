"""
test_writer_extended.py
Soul OS — Phase 2.3: 擴充 SAGE writer pattern 表的驗證

5 個 case 對應你 spec 的 5 條 trigger pattern：
  - 「我去台北出差」         → "去"
  - "Bryan 買了貓食"         → "買"
  - "你說過要帶我去夜市"     → "說過"
  - "我住台北"               → "住在"（補漏洞）
  - "你答應過陪我玩遊戲"     → "答應"

執行：
  python tests/test_writer_extended.py
"""
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.sage.graph_store import GraphStore
from src.memory.sage.writer import MemoryWriter

CASES: List[Tuple[str, str]] = [
    ("我去台北出差",         "去"),
    ("Bryan 買了貓食",       "買"),
    ("你說過要帶我去夜市",   "說過"),
    ("我住台北",             "住在"),
    ("你答應過陪我玩遊戲",   "答應"),
]


def test_new_patterns() -> None:
    data_dir = tempfile.mkdtemp(prefix="sage_writer_test_")
    try:
        # GraphStore __init__ 會自動 _init_db + _load_from_db
        # db_path 必須是 Path 物件（不是 str）
        store = GraphStore(db_path=Path(data_dir) / "graph.sqlite")
        writer = MemoryWriter(store, default_session_id="test_session")

        failed = []
        for text, expected_pred in CASES:
            triples = writer.extract(text)
            predicates = [t.predicate for t in triples]
            if expected_pred not in predicates:
                failed.append(
                    f"  FAIL: '{text}' → {predicates}（期望 '{expected_pred}'）"
                )
            else:
                # 顯示抽到的 fact 全貌，debug 友善
                facts_str = ", ".join(
                    f"{t.subject} {t.predicate} {t.object}" for t in triples
                )
                print(f"  ✓ '{text}' → [{facts_str}]")

        if failed:
            raise AssertionError(
                f"\n❌ {len(failed)}/{len(CASES)} pattern 命中失敗：\n"
                + "\n".join(failed)
            )

        print(f"\n✓ test_writer_extended 全部通過（{len(CASES)}/{len(CASES)} case）")
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    test_new_patterns()
