"""
src/inner_life/lifecycle_overlay.py — 生命週期狀態的讀取面覆寫層（sidecar overlay）

定位（SOUL-BUG-010 v2）：
  - ``elevation_nodes.jsonl`` 是 **append-only 單一權威**：既有的
    ``supersede`` / ``decay`` / ``evaluate_lifecycle`` 全部只改**引擎內部物件**，
    持久化層沒有 update 路徑（``elevation_adapter.py:1275`` 自陳「supersede 原地
    改不动文件」）。⇒ 節點檔裡的 ``lifecycle_state`` 永遠停在寫入當下的值。
  - 本模組提供**不動 append-only 紅線**的替代方案：生命週期轉變另外記錄成
    ``elevation_lifecycle_events.jsonl`` 的差分事件（append-only sidecar），
    讀取時由 :func:`apply_lifecycle_overlay` 疊加到節點 dict 上。
  - 這是「讀取面收斂」的**單一資料源**：所有消費端都經由
    ``emergent_projection.load_elevation_nodes()`` 取得節點，因此只要該函式內部
    套用本 overlay，全庫消費端即同時生效——散落守門正是 SOUL-BUG-009 的病根，
    不重蹈。

設計約束（皆為硬性，非風格偏好）：
  - **純標準函式庫**：不得 import ``soul_elevation``。該套件是 repo 外可選依賴，
    read-side 必須在缺裝時照常工作（``emergent_projection.py:61-63`` 明文要求）。
    亦不得 import ``src.inner_life`` 下任何模組，避免與 ``emergent_projection``
    形成循環（後者要 import 本模組）。
  - **絕不 raise**：缺檔／讀失敗／單行壞一律降級為「沒有事件」。
    讀取面降級成空＝行為等同本模組不存在，這是刻意的 fail-silent。
  - **純函式**：:func:`apply_lifecycle_overlay` 回傳**新的 dict 淺拷貝**，
    絕不原地改動呼叫端傳入的 dict（``load_elevation_nodes`` 的回傳物件會被多個
    消費端共享，現場改會造成跨呼叫污染，且破壞「呼叫端看到的東西只反映自己那次
    呼叫」的直覺）。
  - **不製造新狀態**：只覆蓋事件中出現的欄位（``lifecycle_state`` /
    ``superseded_by``），且 ``lifecycle_state`` 取值必須落在
    ``VALID_LIFECYCLE_STATES`` 內，否則該筆事件被忽略——避免污染
    ``_normalize_lifecycle_state`` 的 additive 語意（未知值缺省 → active）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger("soul_os.inner_life.lifecycle_overlay")

# 生命週期差分事件 sidecar（掛在 data_root()/elevation/ 下，與 elevation 三件套並列）。
LIFECYCLE_EVENTS_FILENAME = "elevation_lifecycle_events.jsonl"

# 四態（與 emergent_projection.VALID_LIFECYCLE_STATES 同值，但**刻意獨立定義**：
# 本模組不得 import emergent_projection（會循環），而 emergent_projection 才是
# 唯一 import 本模組的方向。代價是兩處常數，緩解方式是兩邊都以守衛測試釘住
# 兩者恆等，任何一邊改動都會被測試擋下）。
VALID_LIFECYCLE_STATES: frozenset = frozenset(
    {"active", "weakening", "dormant", "superseded"}
)

# 終態或休眠：不再主動投影、且昇華層不得再拿出來湊證據。
# 語意**不是**「年紀大就沒資格」——見 elevation_adapter 步驟 5 註解：
# 跨週習慣在 ``weakening`` 區間內仍有完整投票權。
TERMINAL_OR_DORMANT_LIFECYCLE_STATES: frozenset = frozenset({"dormant", "superseded"})

# 隔離事件的類型標記（SOUL-BUG-010 對截斷污染節點的處置）。
# 帶這個標記的事件＝該節點被隔離 ⇒ **不得**被回憶召回（否則等於把污染正文
# 重新帶回對話面，違反隔離裁決的「禁止捏造文字」）。
QUARANTINE_EVENT = "quarantine"


def load_lifecycle_events(store_dir: Optional[Any]) -> List[dict]:
    """讀 ``elevation_lifecycle_events.jsonl``（append-only 差分事件）。

    失敗隔離：缺檔 → ``[]``；``OSError`` → ``[]`` 並 warning；
    單行壞 JSON → 跳過該行並 debug。**絕不 raise**（讀取面降級成「沒有事件」
    ＝ 行為等同本模組不存在）。
    """
    if store_dir is None:
        return []
    try:
        path = Path(store_dir) / LIFECYCLE_EVENTS_FILENAME
    except TypeError:
        return []

    events: List[dict] = []
    try:
        if not path.exists():
            return events
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(
                        "lifecycle events line skipped (bad json): %r", line[:80]
                    )
                    continue
                if isinstance(record, dict):
                    events.append(record)
    except OSError as exc:
        logger.warning("lifecycle events read failed (%s): %s", path, exc)
        return []
    return events


def _reduce_events(events: Sequence[dict]) -> Dict[str, dict]:
    """把事件序列收斂成 ``node_id -> 最後一筆有效事件`` 的對照。

    同 ``node_id`` 多筆事件取**檔案順序最後一筆**（append-only ⇒ 檔案順序即時間序）。

    濾掉兩類無效事件（它們若寫進節點 dict 會污染既有 additive 語意）：
      - 缺 ``node_id`` 或不是 str／空字串；
      - ``lifecycle_state`` 不在 :data:`VALID_LIFECYCLE_STATES` 內。
    """
    latest: Dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        node_id = event.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        state = event.get("lifecycle_state")
        if state not in VALID_LIFECYCLE_STATES:
            continue
        latest[node_id] = event
    return latest


def apply_lifecycle_overlay(
    nodes: Sequence[dict], events: Sequence[dict]
) -> List[dict]:
    """把生命週期事件覆寫到節點 dict 上（**純函式**，回傳新的淆拷貝列表）。

    - 無事件時回傳等值列表（零 sidecar ⇒ 逐位元 no-op）。
    - ``nodes`` 中非 dict 元素**原樣保留**、不套用（不因型別異常丟資料）。
    - 只覆寫事件中出現的欄位；事件未帶 ``superseded_by`` 則不動該欄。
    """
    latest = _reduce_events(events)
    if not latest:
        return list(nodes)

    overlaid: List[dict] = []
    for node in nodes:
        if not isinstance(node, dict):
            overlaid.append(node)
            continue
        event = latest.get(node.get("node_id"))
        if event is None:
            overlaid.append(node)
            continue
        merged = dict(node)
        merged["lifecycle_state"] = event["lifecycle_state"]
        if "superseded_by" in event:
            merged["superseded_by"] = event["superseded_by"]
        overlaid.append(merged)
    return overlaid


def load_quarantined_node_ids(store_dir: Optional[Any]) -> Set[str]:
    """被隔離的節點 id 集合（隔離處置的唯讀查詢面）。

    隔離（quarantine）是 SOUL-BUG-010 對截斷污染節點的處置，**唯一**的合法
    載體是生命週期 sidecar（append-only）——節點檔本身保持位元組不變。

    本函式是「哪些節點被隔離」的**單一判定點**（single point of convergence）：
    消費端（回憶檢索）不得自行解讀 sidecar，否則守門會散落到第 N 個消費端
    ——那正是 SOUL-BUG-009／010 的病根。

    以**最新事件**為準（同 ``_reduce_events`` 的 last-wins 語意）：節點先被
    隔離、後又發生 transition ⇒ 不再算隔離（與 overlay 的狀態語意一致）。

    ⚠️ **為什麼隔離旗標不掛在節點 dict 上**（ELEVATION-RECALL-HARDENING-1 實作發現）：
    ``elevation_adapter._rebuild_engine_for`` 以 ``ElevationNode(**n)`` 建引擎，
    而 ``ElevationNode`` 是外部套件 ``soul_elevation`` 的 frozen dataclass（欄位固定）
    ——節點 dict 多一個 key 就會 ``TypeError``，整條昇華路徑爆掉（SOUL-BUG-010 的
    M4 守衛會轉紅，且 M3 會因為例外被吞而**假綠**）。
    ⇒ 隔離資訊改走「節點 id 集合」這條獨立通道：**節點 dict 的形狀保持引擎可吃**，
    而隔離判定的單一收斂點仍在本模組（sidecar 的唯一解讀者）。

    失敗隔離：缺檔 → ``set()``；讀失敗 → ``set()``；**絕不 raise**。
    """
    if store_dir is None:
        return set()
    latest = _reduce_events(load_lifecycle_events(store_dir))
    return {
        node_id
        for node_id, event in latest.items()
        if event.get("event") == QUARANTINE_EVENT
    }


__all__ = [
    "LIFECYCLE_EVENTS_FILENAME",
    "QUARANTINE_EVENT",
    "TERMINAL_OR_DORMANT_LIFECYCLE_STATES",
    "VALID_LIFECYCLE_STATES",
    "apply_lifecycle_overlay",
    "load_lifecycle_events",
    "load_quarantined_node_ids",
]