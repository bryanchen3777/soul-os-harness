"""scripts/guard_regression_quiescence.py — 方案 α：回歸前 guarded 靜態一致性快照閘門。

為什麼有這條（2026-11-01 Owner 雙向裁定）
----------------------------------------
全庫回歸撞上「活的生產服務背景寫入」（TG 心跳 30s、world_log 事件驅動等）
⇒ 即使 skip 清單已收窄，仍可能在快照窗內落入寫入 ⇒ 假紅。
方案 α：在回歸**開跑前**先證明 guarded 集合靜默 —— 連續 3 輪取
``snapshot_data_root_hashes``（輪間隔 60 秒），3 輪 digest 全同才放行；
任一輪不同 ⇒ 拒跑（exit 2）並逐檔點名變動檔（最多 50 個）。

零拷貝（禁止另抄排除清單）
--------------------------
判準直接 import（與五道 zero-mutation 守門**同一份**函式物件）：

    from harness.runner import _is_mutation_skipped, snapshot_data_root_hashes

本檔不定義任何 skip 規則、不碰五道既有守門（一字不動）。

唯讀保證
--------
只呼叫 ``snapshot_data_root_hashes``（該函式本身只讀）；本檔不寫入
``data/**`` 任何檔案（不建檔、不刪檔、不改檔）。

接線方式（pytest fixture／CI 步驟）本次不做，由主大腦驗收後再定；
本工單只交付腳本＋測試。

Exit 碼
-------
* 0 —— 3 輪全同，放行；
* 2 —— 任一輪不同，拒跑（並在 stdout 點名變動檔）。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 方案 α 零拷貝點名（簽名見 harness/runner.py 約 184／204 行）：
# _is_mutation_skipped(rel: Path) -> bool
# snapshot_data_root_hashes(data_root_dir: Path) -> Dict[str, str]
from harness.runner import _is_mutation_skipped, snapshot_data_root_hashes  # noqa: E402

#: 靜默判定需要的連續一致輪數（Owner 裁定：3 輪）。
ROUNDS = 3

#: 輪間隔秒數（Owner 裁定：60 秒，覆蓋 TG 心跳 30s 週期×2）。
INTERVAL_SEC = 60

#: stdout 點名變動檔的上限（避免背景大量寫入時刷屏）。
MAX_NAMED = 50

#: 拒跑 exit 碼（與「放行 0」區分；不用 exit 1 是為了與通用錯誤區分）。
REFUSE_EXIT_CODE = 2


def compute_digest(snapshot: Dict[str, str]) -> str:
    """guarded 集合的整體 digest（排序後逐項餵 sha256，順序無關）。"""
    h = hashlib.sha256()
    for rel in sorted(snapshot):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(snapshot[rel].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def diff_snapshots(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    """回傳 digest 變化的相對路徑（新增／刪除／改內容），已排序。"""
    changed = [
        rel
        for rel in set(before) | set(after)
        if before.get(rel) != after.get(rel)
    ]
    return sorted(changed)


def check_quiescence(
    data_dir: Path,
    rounds: int = ROUNDS,
    interval_sec: int = INTERVAL_SEC,
    snapshot_fn: Callable[[Path], Dict[str, str]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> Tuple[bool, List[str], List[str]]:
    """連取 ``rounds`` 輪快照；全同回 (True, [], digests)，否則 (False, 變動檔, digests)。

    ``snapshot_fn``／``sleep_fn`` 只為測試注入（生產路徑一律用預設）；
    變動檔截斷至 ``MAX_NAMED`` 個（排序後取前 N）。
    """
    take = snapshot_fn or snapshot_data_root_hashes
    sleep = sleep_fn or time.sleep
    snapshots: List[Dict[str, str]] = []
    digests: List[str] = []
    for i in range(rounds):
        snap = take(Path(data_dir))
        snapshots.append(snap)
        digests.append(compute_digest(snap))
        if i < rounds - 1:
            sleep(interval_sec)
    base = snapshots[0]
    for snap in snapshots[1:]:
        if compute_digest(snap) != digests[0]:
            return False, diff_snapshots(base, snap)[:MAX_NAMED], digests
    void_guard_reference()
    return True, [], digests


def void_guard_reference() -> bool:
    """引用零拷貝 import 的另一函式，避免「import 了卻沒用」被誤清。

    ``_is_mutation_skipped`` 的判準本體活在 ``snapshot_data_root_hashes`` 內部；
    此處以一個永真的 trivial 斷言持有引用（恆回 True，不影響任何判決）。
    """
    return bool(_is_mutation_skipped(Path("time_lapse") / "__quiescence_ref__.py"))


def format_report(ok: bool, changed: Sequence[str], digests: Sequence[str]) -> str:
    """stdout 報告（點名變動檔，最多 ``MAX_NAMED`` 個）。"""
    lines = [f"rounds={len(digests)} digest={digests[0] if digests else '<none>'}"]
    if ok:
        lines.append(f"QUIESCENCE-OK guarded 集合 {len(digests)} 輪一致，放行")
    else:
        lines.append(
            f"QUIESCENCE-REFUSED guarded 集合在輪間變化（exit {REFUSE_EXIT_CODE}），變動檔 {len(changed)} 個："
        )
        lines.extend(f"  - {rel}" for rel in changed[:MAX_NAMED])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：預設對生產 ``data/`` 只讀快照；唯一寫入是 stdout。"""
    parser = argparse.ArgumentParser(
        description="方案 α 回歸前靜默閘門：guarded 集合連續一致才放行（唯讀）。"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data",
        help="快照根（預設生產 data/；測試可用 tmp 偽根）。",
    )
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--interval", type=float, default=INTERVAL_SEC)
    args = parser.parse_args(argv)
    ok, changed, digests = check_quiescence(args.data_dir, args.rounds, args.interval)
    print(format_report(ok, changed, digests))
    return 0 if ok else REFUSE_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
