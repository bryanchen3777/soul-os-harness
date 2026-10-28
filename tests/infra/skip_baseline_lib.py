"""tests/infra/skip_baseline_lib.py — skip 集合基準機制的**純函式**。

🔴 本檔**刻意不叫** `test_*.py` ⇒ pytest **不會**收集它（否則它自己就會變成
一支「測試基準機制的測試」）。職責只有四個純函式：

  - ``load_baseline(path=None)``   ：讀回基準檔並**驗格式**（格式錯就 raise）。
  - ``save_baseline(node_ids, path=None)`` ：寫回基準檔（**只放排序後的 ID 清單
    與 count**）。
  - ``diff_skip_ids(baseline_ids, observed_ids)`` ：比對兩個 ID 集合 ⇒
    ``{"added": [...], "removed": [...]}``。
  - ``collect_skip_ids(reports)``  ：從 terminal reporter 的報告物件取出
    「被 skip 的測試 ID」。

設計紀律（FEAT-TEST-ENV-ISOLATION-CLEANUP D4）：

  1. **只比 ID，不比理由** —— 理由含時間戳／絕對路徑 ⇒ 會造成假漂移。
  2. **基準檔不得含時間戳** ⇒ 每日 churn = 0（否則每次開工都要改基準）。
  3. **基準檔只放排序後的 ID 清單與 count** ⇒ 可逐位元 diff。
  4. 純函式 ⇒ `tests/conftest.py`（執行期）與
     `tests/infra/test_skip_baseline_guard.py`（守衛期）**共用同一份判準**，
     守衛測的就是執行期真正在用的那一份。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

#: 基準檔路徑（與本檔同目錄）。
BASELINE_PATH = Path(__file__).resolve().parent / "skip_baseline.json"

#: 基準檔**只允許**的頂層欄位（⇒ 「不得含時間戳欄位」是可執行的形狀，不是口頭）。
ALLOWED_TOP_LEVEL_KEYS = frozenset({"count", "node_ids"})

PathLike = Union[str, Path]


class BaselineError(ValueError):
    """基準檔格式錯誤（缺檔 / JSON 壞 / 欄位缺 / 未排序 / count 不符）。"""


def load_baseline(path: Optional[PathLike] = None) -> List[str]:
    """讀回基準的 skip 測試 ID 清單（已排序、去重）。

    Args:
        path: 基準檔路徑；預設用同目錄的 ``skip_baseline.json``。

    Returns:
        排序後的 node ID 清單。

    Raises:
        BaselineError: 檔案不存在 / JSON 格式錯 / 欄位型別錯 / 未排序 / 有重複 /
            ``count`` 與清單長度不一致 / 出現未允許的頂層欄位（例如時間戳）。
    """
    target = Path(path) if path is not None else BASELINE_PATH
    if not target.is_file():
        raise BaselineError(f"skip 基準檔不存在: {target}")
    try:
        raw: Any = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BaselineError(f"skip 基準檔不是合法 JSON: {target} ({exc})") from exc
    if not isinstance(raw, dict):
        raise BaselineError(f"skip 基準檔必須是 JSON object: {target}")
    extra = sorted(set(raw) - ALLOWED_TOP_LEVEL_KEYS)
    if extra:
        raise BaselineError(
            f"skip 基準檔出現未允許的頂層欄位（時間戳之類會造成每日 churn）: {extra}"
        )
    node_ids = raw.get("node_ids")
    count = raw.get("count")
    if not isinstance(node_ids, list) or not all(isinstance(n, str) for n in node_ids):
        raise BaselineError(f"skip 基準檔的 node_ids 必須是字串清單: {target}")
    if not isinstance(count, int) or isinstance(count, bool):
        raise BaselineError(f"skip 基準檔的 count 必須是整數: {target}")
    if count != len(node_ids):
        raise BaselineError(
            f"skip 基準檔的 count({count}) != node_ids 長度({len(node_ids)}): {target}"
        )
    if node_ids != sorted(node_ids):
        raise BaselineError(f"skip 基准檔的 node_ids 未排序: {target}")
    if len(set(node_ids)) != len(node_ids):
        raise BaselineError(f"skip 基準檔的 node_ids 有重複: {target}")
    return list(node_ids)


def save_baseline(node_ids: Iterable[str], path: Optional[PathLike] = None) -> Path:
    """把 ID 清單寫成基準檔（排序、去重、**不含時間戳**）。

    Args:
        node_ids: 測試 ID 清單。
        path: 目標路徑；預設用同目錄的 ``skip_baseline.json``。

    Returns:
        寫入的檔案路徑。
    """
    target = Path(path) if path is not None else BASELINE_PATH
    ids = sorted({str(n) for n in node_ids})
    payload = {"count": len(ids), "node_ids": ids}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def diff_skip_ids(
    baseline_ids: Iterable[str], observed_ids: Iterable[str]
) -> Dict[str, List[str]]:
    """比對兩個 ID 集合 ⇒ ``{"added": [...], "removed": [...]}``（皆已排序）。

    Args:
        baseline_ids: 基準清單。
        observed_ids: 本次實際觀察到的清單。

    Returns:
        ``added`` ＝觀察到但基準沒有的；``removed`` ＝基準有但觀察不到的。
    """
    base = {str(n) for n in baseline_ids}
    observed = {str(n) for n in observed_ids}
    return {
        "added": sorted(observed - base),
        "removed": sorted(base - observed),
    }


def collect_skip_ids(reports: Iterable[Any]) -> List[str]:
    """從報告物件取出「被 skip 的測試 ID」（排序、去重）。

    Args:
        reports: terminal reporter 的報告物件（``terminalreporter.stats["skipped"]``）。

    Returns:
        排序後的 node ID 清單。
    """
    out = set()
    for report in reports or []:
        outcome = getattr(report, "outcome", "skipped")
        if outcome != "skipped":
            continue
        nodeid = getattr(report, "nodeid", None)
        if nodeid:
            out.add(str(nodeid))
    return sorted(out)
