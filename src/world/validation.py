"""
src/world/validation.py — Soul OS M3 Phase 1

薄 input validation layer (Bry 拍板 2026-08-07 19:40):
- source whitelist
- required fields
- timestamp validation
- payload shape
- novelty_id normalization

Invalid event → reject → trace → no context → no memory。
不建立大型 validation framework。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict

from .perception import WorldEvent, VALID_SOURCES

logger = logging.getLogger("soul_os.world.validation")


class WorldEventValidationError(ValueError):
    """Raised when WorldEvent fails thin validation. Caller should reject + trace."""
    pass


# novelty_id 格式: [a-z0-9_]+, 長度 4-128
# 為什麼: 避免 injection / 路徑穿越 / 特殊字元
# 不嚴格 (e.g. 不強制 UUID), 因為 source 通常自帶意義 (e.g. "weather_rain_20260807")
_NOVELTY_ID_RE = re.compile(r"^[a-z0-9_]{4,128}$")


def _validate_timestamp(ts: str) -> None:
    """
    驗證 timestamp 是 ISO 8601 UTC。

    Bry 拍板 2026-08-07 19:40: timestamp validation 是必填 (避免 time skew / 偽造)。
    規則: 必須能 fromisoformat, 必須是 UTC (有 Z 或 +00:00 或 +0000)。
    """
    if not ts or not isinstance(ts, str):
        raise WorldEventValidationError(f"ts 必填且為字串, got: {ts!r}")
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError) as e:
        raise WorldEventValidationError(f"ts 不是 ISO 8601: {ts!r} ({e})")
    # 確認 UTC
    if parsed.tzinfo is None:
        raise WorldEventValidationError(f"ts 缺時區: {ts!r} (M3 要求 UTC)")
    # 從 UTC offset 確認
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise WorldEventValidationError(f"ts 非 UTC: {ts!r} (offset={parsed.utcoffset()})")


def _validate_novelty_id(novelty_id: str) -> str:
    """
    驗證 novelty_id 格式 + normalize (lowercase + strip)。

    規則: 只能含 [a-z0-9_], 長度 4-128。
    返回 normalized 形式 (給 state 用同一個 key, 避免大小寫不一致)。
    """
    if not novelty_id or not isinstance(novelty_id, str):
        raise WorldEventValidationError(f"novelty_id 必填且為字串, got: {novelty_id!r}")
    normalized = novelty_id.strip().lower()
    if not _NOVELTY_ID_RE.match(normalized):
        raise WorldEventValidationError(
            f"novelty_id 格式不對: {novelty_id!r} → {normalized!r} "
            f"(需 [a-z0-9_]{{4,128}})"
        )
    return normalized


def validate_world_event(payload: Dict[str, Any]) -> WorldEvent:
    """
    薄 input validation: 通過後回 WorldEvent, 失敗 raise WorldEventValidationError。

    Bry 拍板 (Phase 1 必查):
    1. source whitelist
    2. required fields
    3. timestamp validation
    4. payload shape
    5. novelty_id normalization

    注意: payload shape 限制很鬆, source-specific data 不嚴格驗證
    (Phase 2 才考慮 per-source schema validation)。
    """
    if not isinstance(payload, dict):
        raise WorldEventValidationError(f"payload 必須是 dict, got: {type(payload).__name__}")

    # ── 1. required fields
    for required in ("source", "type", "novelty_id", "ts", "summary"):
        if required not in payload:
            raise WorldEventValidationError(f"缺必填欄位: {required!r}")

    # ── 2. source whitelist
    source = payload["source"]
    if source not in VALID_SOURCES:
        raise WorldEventValidationError(
            f"source {source!r} 不在白名單 {sorted(VALID_SOURCES)}"
        )

    type_ = payload["type"]
    if not isinstance(type_, str) or not type_.strip():
        raise WorldEventValidationError(f"type 必填且為非空字串, got: {type_!r}")

    # ── 3. timestamp validation
    _validate_timestamp(payload["ts"])

    # ── 4. summary 簡單驗證
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise WorldEventValidationError(f"summary 必填且為非空字串, got: {summary!r}")
    if len(summary) > 500:
        # 防止超大 payload
        raise WorldEventValidationError(f"summary 太長 ({len(summary)} chars, 上限 500)")

    # ── 5. novelty_id normalization
    normalized_nid = _validate_novelty_id(payload["novelty_id"])

    # ── 6. data optional dict
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise WorldEventValidationError(f"data 必須是 dict, got: {type(data).__name__}")

    return WorldEvent(
        source=source,
        type=type_,
        novelty_id=normalized_nid,
        ts=payload["ts"],
        summary=summary.strip(),
        data=data,
    )
