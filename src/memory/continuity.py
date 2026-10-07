"""Memory Continuity Observability v1 — Layer 2 (injection) + Layer 3 (turn) sidecar.

D4 frozen-surface exception（Bry 2026-10-07 拍板）：

    不得改變任何 memory selection / rejection / injection behavior；
    sidecar 只能觀測已經發生的決策。
    不得讓 snapshot 變成「順便調整 eligible 結構」的入口。

本模組的每一個公開入口都是**旁路（observability sidecar）**：
寫入失敗只會 ``logger.warning``，絕不 raise 進記憶注入或回覆生成主路徑。
因此本模組**不得** import / 參照任何 LLM client（`httpx` / `openai` /
`anthropic` / `google.generativeai` / `src.llm.proxy`）—— 見 tests/infra/
test_continuity_sidecar_is_additive.py::test_g2_no_llm_client_import。

資料流（本票只補中間兩段）::

    retrieval（loader_trace.jsonl，既有）→ injection（本模組 Layer 2）
        → turn（本模組 Layer 3）→ use（離線推導）→ influence（尚未建立）

🔴 Join Key ≠ Identity（Bry 2026-10-07 D7）—— 四個識別碼角色完全不同：

======================  ==================  =============================  ====================
欄位                    角色                定義                           唯一性範圍
======================  ==================  =============================  ====================
``correlation_id``      **join key**        ``AGENT_INTENT.(correlation_id
                        （關聯同一條 chain）  or event_id)``，由 middleware
                                              的 re-publish 透傳
                                              **不唯一**：同一 chain 內 Layer 2
                                              與 Layer 3 共用同一值
``snapshot_id``         **row group 身分**  單次注入共用同一 uuid4，其下多個
                        （一次觀測的身分）    memory row 共用               每回合 1 個
Layer 2 ``response_id`` **row 身分**        ``loader.load()`` 開頭 mint 的
                        （loader 的身分）    uuid4                          每次 ``load()`` 1 個
Layer 3 ``response_id`` **row 身分**        recorder 為該列 mint 的 uuid4   每 turn 1 個
======================  ==================  =============================  ====================

一句話：**``correlation_id`` 是關聯用的；``snapshot_id`` 與兩層的
``response_id`` 是辨識用的。Layer 2 ↔ Layer 3 的 join 走 ``correlation_id``
（或 ``injection_snapshot_id``），絕不是走 ``response_id``。**
兩個都叫 ``response_id`` 的欄位不可直接 join。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("soul_os.memory.continuity")

# ── 凍結常數（工單已拍板，執行者不得調整）─────────────────────
SCHEMA_INJECTION = "continuity_injection_v1"
SCHEMA_TURN = "continuity_turn_v1"

#: use heuristic 的演算法版本。``--rescore`` 見此版本不符時標記待重算。
ALGORITHM_VERSION = "ngram4-v1"

#: 🔴 永遠是 "heuristic"。本模組**不得**在任何地方記錄「這筆記憶被用上了」
#: 這種斷言式欄位（T5：use 只能是訊號，不能是結論）。
USE_CONFIDENCE = "heuristic"

#: D2 retention：30 天。過期 sidecar 不得升格進 Soul Memory / Palace。
RETENTION_DAYS = 30

#: use heuristic 參數（Bry 凍結）：4-gram、至少 2 個**彼此不同**的命中。
NGRAM_N = 4
MIN_NGRAM_HITS = 2

#: 環境變數：``"1"``（預設）= 啟用，``"0"`` = 完全停用（連檔案都不開）。
ENV_FLAG = "SOULOS_CONTINUITY_OBSERVABILITY"

INJECTION_FILENAME = "continuity_injection.jsonl"
TURN_FILENAME = "continuity_turn.jsonl"

#: D10：任何 sidecar 內部失敗都用這個固定前綴 warning，**不得靜默 return**。
GAP_WARNING_PREFIX = "[MemoryContinuity] instrumentation_gap:"


def enabled() -> bool:
    """回報 continuity sidecar 是否啟用（讀 ``SOUL_OS_CONTINUITY_OBSERVABILITY``）。

    讀取 env **每次呼叫時**都做，所以測試可以 monkeypatch env 或直接 monkeypatch
    本函式。停用時 recorder 的所有入口短路，**連檔案都不會開啟**。
    """
    return os.environ.get(ENV_FLAG, "1") != "0"


# ────────────────────────────────────────────────────────────
# 純函式（兩個 hash 都用同一份 canonicalize）
# ────────────────────────────────────────────────────────────


def canonicalize(text: str) -> str:
    """NFKC + casefold。兩層的 text hash 都必須走這一份。

    刻意**不去空白、不去標點** —— 保留可審計性。
    已知保守偏差（工單指定寫入本說明）：``"喜歡,咖啡"`` 與 ``"喜歡咖啡"``
    因為多了一個標點而 4-gram 邊界錯開，**不會**互相命中。
    因此該 heuristic 是**保守的：傾向低估 use，不會高估**。
    """
    return unicodedata.normalize("NFKC", text or "").casefold()


def text_hash(text: str) -> str:
    """``sha256(canonicalize(text))`` 的 hex digest。"""
    return hashlib.sha256(canonicalize(text).encode("utf-8")).hexdigest()


def ngrams(text: str) -> set:
    """``{ norm[i:i+4] for i in range(len(norm)-3) }``；``len(norm) < 4`` → 空集合。"""
    norm = canonicalize(text)
    if len(norm) < NGRAM_N:
        return set()
    return {norm[i:i + NGRAM_N] for i in range(len(norm) - (NGRAM_N - 1))}


def match_ngrams(memory_text: str, response_text: str) -> set:
    """兩段文字共有的 4-gram 集合（已去重 → 重複的同一 4-gram 只算一次）。"""
    return ngrams(memory_text) & ngrams(response_text)


def score_against_memories(
    response_text: str,
    memories: Sequence[Tuple[str, str]],
) -> Dict[str, Any]:
    """對 ``memories``（``[(memory_id, memory_text), ...]``）算 Layer 3 的 rollup。

    回傳結構即 ``continuity_turn_v1.use_heuristic``。``matched_ngram_count`` 是
    各筆命中數的**加總**；``candidate`` = 是否有任一筆達到 ``MIN_NGRAM_HITS``。
    """
    matched_memory_ids: List[str] = []
    total = 0
    for memory_id, memory_text in memories:
        hits = match_ngrams(memory_text or "", response_text or "")
        if not hits:
            continue
        total += len(hits)
        if len(hits) >= MIN_NGRAM_HITS:
            matched_memory_ids.append(memory_id)
    return {
        "candidate": bool(matched_memory_ids),
        "matched_memory_ids": matched_memory_ids,
        "matched_ngram_count": total,
        "algorithm_version": ALGORITHM_VERSION,
        "use_confidence": USE_CONFIDENCE,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retention_from(timestamp_iso: str, retention_days: int = RETENTION_DAYS) -> str:
    base = datetime.fromisoformat(timestamp_iso)
    return (base + timedelta(days=retention_days)).isoformat()


def _event_attr(event: Any, name: str, default: Any = None) -> Any:
    """SoulEvent 是 pydantic model；測試可能塞簡單 stub。兩者都支援。"""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def event_correlation_id(event: Any) -> str:
    """join key：``correlation_id or event_id``（與 middleware re-publish 同規則）。"""
    return _event_attr(event, "correlation_id") or _event_attr(event, "event_id") or ""


def classify_response_status(event: Any) -> str:
    """回傳 ``ok`` / ``stub`` / ``empty_text``。

    ``stub`` 優先於 ``empty_text``：``src/llm/proxy.py`` 的 stub 路徑發出的
    AGENT_SPEAK 帶 ``text=""`` **且** ``is_stub=True``，兩者同時成立時必須留下
    ``stub`` 這個更具診斷價值的標記，而不是被空字串蓋掉。
    """
    payload = _event_attr(event, "payload", {}) or {}
    if payload.get("is_stub"):
        return "stub"
    if not (payload.get("text") or ""):
        return "empty_text"
    return "ok"


# ────────────────────────────────────────────────────────────
# TurnLinkIndex
# ────────────────────────────────────────────────────────────


class TurnLinkIndex:
    """``correlation_id → {snapshot_id, linked_at}`` 的短命記憶體索引。

    凍結參數：TTL 15 分鐘、上限 200 筆，過期或超限逐出最舊。

    設計上的**可見缺口**（非缺陷）：這是純記憶體狀態，服務重啟或事件跨 process
    就會失聯，屆時 Layer 3 會寫 ``link_status="unlinked"`` + null —— 缺資料是
    **被標記出來的**，不是靜默消失（P5）。
    """

    TTL_SECONDS = 15 * 60.0
    MAX_ENTRIES = 200

    def __init__(
        self,
        ttl_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
        clock=None,
    ) -> None:
        self.ttl_seconds = self.TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self.max_entries = self.MAX_ENTRIES if max_entries is None else max_entries
        self._clock = clock or time.monotonic
        self._entries: Dict[str, Dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def record(self, correlation_id: str, snapshot_id: str) -> None:
        if not correlation_id:
            return
        self._prune()
        self._entries[correlation_id] = {
            "snapshot_id": snapshot_id,
            "linked_at": self._clock(),
        }
        self._evict()

    def pop(self, correlation_id: str) -> Optional[str]:
        """取出並移除（pop 語意：同一 correlation_id 不可被重複消費）。"""
        if not correlation_id:
            return None
        self._prune()
        entry = self._entries.pop(correlation_id, None)
        if not entry:
            return None
        return entry.get("snapshot_id")

    def _prune(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, value in self._entries.items()
            if (now - value["linked_at"]) >= self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

    def _evict(self) -> None:
        while len(self._entries) > self.max_entries:
            oldest = min(
                self._entries,
                key=lambda k: self._entries[k]["linked_at"],
            )
            self._entries.pop(oldest, None)


# ────────────────────────────────────────────────────────────
# ContinuityRecorder
# ────────────────────────────────────────────────────────────


class ContinuityRecorder:
    """Layer 2 / Layer 3 的 append-only JSONL sidecar writer。

    🔴 D8 lazy：``__init__`` 只建立記憶體中的物件與路徑物件，**不 mkdir、不開檔、
    不寫入任何資料**。目錄與檔案一律等到第一次 ``record_injection()`` /
    ``record_turn()`` 才 lazy 建立 —— sidecar 不該在 middleware 啟動時就改變
    filesystem state。

    🔴 D10 非阻塞：``record_turn()`` 同步、非 await、零 LLM call。任何內部例外
    都由本類吞掉並以 :data:`GAP_WARNING_PREFIX` 標記 instrumentation gap。
    """

    #: 一次注入的 memory 原文暫存量上限（供 Layer 3 寫入時算 use heuristic）。
    MAX_TRACKED_SNAPSHOTS = 200

    def __init__(
        self,
        data_dir: Path,
        enabled: bool = True,
        retention_days: int = RETENTION_DAYS,
    ) -> None:
        # 🔴 D8：這裡只組 Path 物件。不得 mkdir / open / write。
        self._data_dir = Path(data_dir)
        self._enabled = bool(enabled)
        self._retention_days = retention_days
        self._injection_path = self._data_dir / INJECTION_FILENAME
        self._turn_path = self._data_dir / TURN_FILENAME
        # snapshot_id → [(memory_id, memory_text), ...]
        # Layer 3 寫入時要重算 use heuristic，就靠這份「當下已注入的原文」。
        # 這是純記憶體的短期暫存，不落盤、不參與任何決策。
        self._snapshot_memories: Dict[str, List[Tuple[str, str]]] = {}
        self._snapshot_order: List[str] = []
        self.gap_count = 0

    # ── 內部基礎設施 ──────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def injection_path(self) -> Path:
        return self._injection_path

    @property
    def turn_path(self) -> Path:
        return self._turn_path

    def _gap(self, stage: str, exc: BaseException) -> None:
        """標記 instrumentation gap：D10 要求可見，**不是靜默 return**。"""
        self.gap_count += 1
        logger.warning(
            f"{GAP_WARNING_PREFIX} stage={stage} "
            f"type={type(exc).__name__} error={exc} "
            f"(sidecar 失效，不影響 memory injection / 回覆生成)"
        )

    def _append_lines(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        """append-only JSONL 寫入；任何失敗只 warning。**絕不 raise**。"""
        if not rows:
            return
        try:
            # 🔴 D8 lazy：目錄到第一次寫入才建。
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 — 旁路必須吞掉一切
            self._gap(f"write:{path.name}", exc)

    def _track_snapshot(
        self,
        snapshot_id: str,
        memories: List[Tuple[str, str]],
    ) -> None:
        self._snapshot_memories[snapshot_id] = memories
        self._snapshot_order.append(snapshot_id)
        while len(self._snapshot_order) > self.MAX_TRACKED_SNAPSHOTS:
            self._snapshot_memories.pop(self._snapshot_order.pop(0), None)

    # ── Layer 2: injection snapshot ──────────────────────

    def record_injection(
        self,
        *,
        event: Any,
        eligible: Sequence[Any],
        response_id: Optional[str] = None,
        link_index: Optional[TurnLinkIndex] = None,
    ) -> Optional[str]:
        """把「這一回合實際決定注入的 eligible」逐筆寫成 Layer 2。

        D9：呼叫點必須在 ``format_for_prompt(eligible)`` 與 context 拼接**之後**，
        抓的是已決定注入的那份清單 —— **不得**從 rendered prompt 反解析
        （``format_for_prompt()`` 不含 memory_id）。

        D4：``eligible`` **只讀**。本函式不得賦值、重排或增刪其內容。

        回傳本次 snapshot_id（失敗或停用時為 None）。**不 raise。**
        """
        if not self._enabled:
            return None
        try:
            rows: List[Dict[str, Any]] = []
            snapshot_id = str(uuid.uuid4())
            timestamp = _utc_now_iso()
            correlation_id = event_correlation_id(event)
            agent_id = _event_attr(event, "payload", {}).get("agent_id") or _event_attr(
                event, "payload", {}
            ).get("target_agent") or "unknown"
            tracked: List[Tuple[str, str]] = []

            for rank, memory in enumerate(eligible or ()):
                content = getattr(memory, "content", "") or ""
                memory_id = getattr(memory, "memory_id", "") or ""
                rows.append({
                    "schema_version": SCHEMA_INJECTION,
                    "snapshot_id": snapshot_id,
                    "event_id": _event_attr(event, "event_id"),
                    "correlation_id": correlation_id,
                    "session_id": _event_attr(event, "session_id"),
                    "agent_id": agent_id,
                    "memory_id": memory_id,
                    "memory_text": content,
                    "memory_text_hash": text_hash(content),
                    "retrieval_confidence": getattr(memory, "confidence", None),
                    "injection_rank": rank,
                    "injection_timestamp": timestamp,
                    "response_id": response_id,
                    "retention_expires_at": _retention_from(
                        timestamp, self._retention_days
                    ),
                })
                tracked.append((memory_id, content))

            self._append_lines(self._injection_path, rows)
            self._track_snapshot(snapshot_id, tracked)
            if link_index is not None:
                link_index.record(correlation_id, snapshot_id)
            return snapshot_id
        except Exception as exc:  # noqa: BLE001 — 旁路必須吞掉一切
            self._gap("record_injection", exc)
            return None

    # ── Layer 3: turn sidecar ────────────────────────────

    def record_turn(
        self,
        event: Any,
        *,
        link_index: Optional[TurnLinkIndex] = None,
    ) -> Optional[Dict[str, Any]]:
        """把「這一 turn 的回應」寫成 Layer 3。同步、非阻塞、零 await、零 LLM。

        D10：這個呼叫必須是 ``_on_agent_speak`` 的**第一個 statement**
        （早於 commit cooldown 與空 text 兩個既有 early return），否則 stub
        turn（``text=""``）會被靜默吃掉 —— 那正是 P5 違反。

        回傳寫出的 row（停用或失敗時為 None）。**不 raise。**
        """
        if not self._enabled:
            return None
        try:
            payload = _event_attr(event, "payload", {}) or {}
            correlation_id = event_correlation_id(event)
            snapshot_id = link_index.pop(correlation_id) if link_index else None
            link_status = "linked" if snapshot_id else "unlinked"
            response_text = payload.get("text") or ""
            memories = self._snapshot_memories.get(snapshot_id, []) if snapshot_id else []
            heuristic = score_against_memories(response_text, memories)
            timestamp = _utc_now_iso()
            row = {
                "schema_version": SCHEMA_TURN,
                # 🔴 Layer 3 的 response_id 是**本列自己的** uuid4，
                # 不是 loader 的 —— 兩個同名欄位不可互相 join。
                "response_id": str(uuid.uuid4()),
                "event_id": _event_attr(event, "event_id"),
                "correlation_id": correlation_id,
                "session_id": _event_attr(event, "session_id"),
                "agent_id": payload.get("agent_id") or "unknown",
                "injection_snapshot_id": snapshot_id,
                "link_status": link_status,
                "response_status": classify_response_status(event),
                "response_text": response_text,
                "response_text_hash": text_hash(response_text),
                "use_heuristic": heuristic,
                "timestamp": timestamp,
                "retention_expires_at": _retention_from(
                    timestamp, self._retention_days
                ),
            }
            self._append_lines(self._turn_path, [row])
            return row
        except Exception as exc:  # noqa: BLE001 — 旁路必須吞掉一切
            self._gap("record_turn", exc)
            return None