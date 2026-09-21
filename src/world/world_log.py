"""
src/world/world_log.py — Soul OS World Log Phase A (Step 59/60)

Owner-locked contract (this ticket):

    Adapter fact → World Log → Event Bus → Perception

世界事實必須在感知之前存在。三個 production adapter 的 ``_emit_via_bus()``
在 publish **之前**，先把「已建構、已通過既有 validation、**尚未 publish**」的
``WorldEvent`` append 落盤成一筆 JSONL。理由：Event Bus ``QueueFull`` 時
subscriber 未必看到事件，World Log 必須保留 adapter 已接到事實的證據。

唯一合法 runtime 寫入點（本票授權的三處，全部在 publish 之前）:
  - src/world/source/calendar_ical.py  (IcalCalendarSource)
  - src/world/source/open_meteo.py     (OpenMeteoWeatherSource)
  - src/world/source/news_rss.py       (RssNewsSource)

禁止寫 World Log 的位置（本模組不得被這些地方 import 使用）:
  WorldInnerLifeAdapter.handle_event() / 其他 Event Bus subscriber /
  middleware / perception trace writer / wake gate / scheduler /
  lifecycle / Inner Life consumer。

明確排除（runtime）: SyntheticWorldEventSource、
``SOULOS_WORLD_PERCEPTION_TEST_SOURCE``、synthetic / fixture-only source
runtime route、test bootstrap / mock runtime source。這些路徑一律不得寫
World Log。真正的防線是**結構性**的：上述 runtime route / fixture factory
都不經過三個 production adapter 的 ``_emit_via_bus()``（synthetic runtime
route 直接走 ``WorldPerceptionMiddleware.process_world_event_direct()``），
所以結構上就到不了 World Log。

本模組的 writer-level 檢查（``is_world_log_eligible`` /
``EXCLUDED_WORLD_LOG_SOURCES``）覆蓋範圍必須精確敘述（**不得擴張解讀**）:
  - 只做 source **字面值** ``"synthetic"`` 比對，**不做任何正規化**
    （不 casefold、不 strip）⇒ ``"SYNTHETIC"`` / ``" synthetic "`` 皆仍
    eligible。
  - ``SyntheticWorldEventSource`` 的 **fixture factory** 事件
    （``build_rain_started()`` / ``build_celebrity_news()`` 等）source 是
    ``weather`` / ``news`` / ``calendar`` / ``social`` ⇒ **不在**本檢查
    覆蓋範圍（本 writer-level 檢查對它們回 eligible）。
  - 因此本檢查只是**額外的 fail-closed 冗餘**（擋掉「source 字面 synthetic
    且誤入 writer」這一種情境），**不是**「synthetic 永不污染 World Log」
    的完整防線，也不得被當成防線敘述。

落盤位置::

    data_root() / "world" / "world_log" / "<UTC YYYY-MM-DD>.jsonl"

路徑慣例對齊 src/world/trace.py:36-38 (``data_root() / "world" / ...``)，
一律走 ``src.paths.data_root()`` 以便測試隔離。

Fixed record contract (每筆至少含這些欄位)::

    {
      "world_event_id": "world:{source}:{novelty_id}",
      "source":         WorldEvent.source,
      "event_type":     WorldEvent.type,
      "novelty_id":     WorldEvent.novelty_id,
      "happened_at":    WorldEvent.ts          (逐字, 不得重解釋),
      "observed_at":    UTC ISO-8601, 由寫入時鐘獨立取得,
      "summary":        WorldEvent.summary,
      "priority":       WorldEvent.priority,
      "provenance":     {"source": ..., "payload_reference": bounded-or-null}
    }

必守語意:
  - ``world_event_id`` 逐字等於 ``world:{source}:{novelty_id}``；不得使用
    ``SoulEvent.event_id`` UUID。
  - ``happened_at`` 逐字來自 ``WorldEvent.ts``（calendar 的未來 DTSTART 仍是
    合法的 source-native happened_at；本模組不得修正或重解釋）。
  - ``observed_at`` 必須獨立由寫入時鐘取得，不得由 ``happened_at`` 複製、
    推導或回填。
  - payload / provenance 必須**有界**：不落盤原始 RSS / iCal / provider
    response；``payload_reference`` 只是一個 bounded immutable digest。
  - record 內不得寫入 token / secret / authorization header / 完整環境值 /
    user conversation / SAGE / Inner Life 內容 / LLM payload。

Append-only, forward-only:
  - 只 append，永不 rewrite / truncate / merge / edit 既有 shard。
  - 同一 ``source + novelty_id`` 重複 emit ⇒ 保留**各次 adapter observation**
    （每筆有獨立的 ``observed_at``）；World Log 不是 dedup cache，
    canonical ``world_event_id`` 不因重複而改。
  - 0 backfill / 0 historical rewrite。

Retention (A2, 30 UTC 日曆日):
  - shard 名稱固定為 UTC ``YYYY-MM-DD.jsonl``；保留 30 個 UTC 日曆日
    （``cutoff = today_utc - (30 - 1) days``，keep ``shard_date >= cutoff``）。
  - 僅可刪除**完整 shard**；cutoff 判定**基於檔名中的 UTC 日期，不看 mtime**。
  - 只處理精確路徑 ``data_root()/world/world_log/*.jsonl``：不掃整個
    ``data/world/``，不碰 perception_trace.jsonl、World Fact Text 或任何其他
    world artifact。
  - malformed filename / 未知副檔名 / 子目錄 / trace file ⇒ fail-quiet 且不刪。
  - writer path 的 frequency 明確：每個 writer **每個 UTC 日曆日至多掃一次**
    （``WorldLogWriter._last_retention_date``），不是每一筆寫入就掃。

Frozen contract note: 本模組 0 改 relevance baseline / score / threshold /
priority weight / WorldPerceptionTrace schema / trace 行內容 / trace
``timestamp`` 的 perception-time 語意 / collision 4h window / wake gate /
scheduler / WorldEvent / WorldEventSource ABC / Event Bus contract。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("soul_os.world.world_log")


# ───────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────

#: World Log 目錄（相對 data_root()）。對齊 src/world/trace.py 的
#: ``data_root() / "world" / ...`` 慣例。
WORLD_LOG_SUBDIR: tuple = ("world", "world_log")

#: 固定 shard 名稱 = UTC 日曆日 + 副檔名。
SHARD_DATE_FORMAT = "%Y-%m-%d"
SHARD_SUFFIX = ".jsonl"

#: A2 retention policy: 保留 30 個 UTC 日曆日（含今天）。
WORLD_LOG_RETENTION_DAYS = 30

#: Runtime 明確排除：這些 source 一律不得寫 World Log。
#:
#: 精確覆蓋範圍（**不得擴張解讀**）：本集合只做 source **字面值**比對，
#: **不正規化**（不做 casefold / strip）⇒ 只覆蓋 source 恰為 ``"synthetic"``
#: 者；``"SYNTHETIC"`` / ``" synthetic "`` 仍被判為 eligible。
#: ``SyntheticWorldEventSource`` 的 **fixture factory** 事件
#: （``build_rain_started()`` / ``build_celebrity_news()``）source 是
#: ``weather`` / ``news`` / ``calendar`` / ``social`` ⇒ **不在**本集合覆蓋
#: 範圍。真正的 synthetic 防線是**結構性**的（synthetic runtime route 與
#: fixture factory 都不經過三個 production adapter 的 ``_emit_via_bus()``）；
#: 本集合只是額外的 fail-closed 冗餘，不構成「synthetic 永不污染」的保證。
#: （詳見模組 docstring）
EXCLUDED_WORLD_LOG_SOURCES = frozenset({"synthetic"})

#: JSONL 行界安全（R-1）：``json.dumps(..., ensure_ascii=False)`` **不**逃逸
#: 這三個 Unicode 行界碼位，而 Python ``str.splitlines()``（本 repo 讀 JSONL
#: 的慣例，見 tests 的 ``read_text().splitlines()``）把它們當**換行** ⇒
#: 一個 record 會被 naive 讀者看成多筆。writer 端在 dumps 之後、寫檔之前
#: 逐字元把它們換成 JSON escape 序列，讓「一行 = 一 record」在**輸出位元組**
#: 上成立（reader 不需改，``json.loads()`` 逐字還原）。
#:
#: 註：``\x0b`` / ``\x0c`` / ``\x1c``–``\x1e`` 等 <0x20 的邊界字元已被 JSON
#: 強制逃逸（``json.dumps`` 本身處理），不在本機制範圍。
LINE_BOUNDARY_ESCAPES: tuple = (
    ("\u0085", "\\u0085"),  # NEL — NEXT LINE
    ("\u2028", "\\u2028"),  # LINE SEPARATOR
    ("\u2029", "\\u2029"),  # PARAGRAPH SEPARATOR
)

#: provenance.payload_reference 形式：固定長度、不可逆、有界。
PAYLOAD_REFERENCE_PREFIX = "sha256:"

#: 嚴格 shard 檔名 pattern（只有這個形狀可被 retention 處理）。
_SHARD_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


# ───────────────────────────────────────────────────────────
# Path helpers (lazy data_root() so tests can re-point SOUL_OS_DATA_DIR)
# ───────────────────────────────────────────────────────────

def world_log_dir() -> Path:
    """World Log shard 目錄 = ``data_root() / "world" / "world_log"``。

    每次呼叫都重新解析 ``data_root()``（不在此處 mkdir），讓 pytest 的
    ``SOUL_OS_DATA_DIR`` 隔離（含 ``reset_data_root()``）對 writer 生效。
    """
    from src.paths import data_root

    return data_root().joinpath(*WORLD_LOG_SUBDIR)


def _as_utc(moment: datetime) -> datetime:
    """Normalize to an aware UTC datetime (naive input is read as UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def utc_calendar_date(moment: datetime) -> date:
    """UTC 日曆日（shard / cutoff 判定的唯一依據）。"""
    return _as_utc(moment).date()


def utc_iso(moment: datetime) -> str:
    """UTC ISO-8601（帶 +00:00 offset）。"""
    return _as_utc(moment).isoformat()


def shard_name(moment: datetime) -> str:
    """``<UTC YYYY-MM-DD>.jsonl``。"""
    return _as_utc(moment).strftime(SHARD_DATE_FORMAT) + SHARD_SUFFIX


def shard_path(moment: datetime, log_dir: Optional[Path] = None) -> Path:
    """指定時刻所屬的 shard 完整路徑。"""
    base = Path(log_dir) if log_dir is not None else world_log_dir()
    return base / shard_name(moment)


# ───────────────────────────────────────────────────────────
# Record building (fixed data contract)
# ───────────────────────────────────────────────────────────

def build_world_event_id(source: str, novelty_id: str) -> str:
    """``world:{source}:{novelty_id}`` — 逐字，canonical，不用 bus UUID。"""
    return f"world:{source}:{novelty_id}"


def build_payload_reference(data: Any) -> Optional[str]:
    """Bounded immutable source reference，或 ``None``。

    只落一個固定長度的 sha256 digest（``sha256:<64 hex>``，共 71 字元），
    永不落盤原始 RSS / iCal / provider response 內容；digest 只做為
    「同一份 payload」的可比對引用。
    """
    if not data:
        return None
    try:
        canonical = json.dumps(
            data,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
    except Exception:  # pragma: no cover - defensive (non-serializable payload)
        return None
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{PAYLOAD_REFERENCE_PREFIX}{digest}"


def build_record(world_event: Any, observed_at: datetime) -> Dict[str, Any]:
    """Build one World Log JSONL record (fixed data contract, bounded)."""
    source = world_event.source
    novelty_id = world_event.novelty_id
    return {
        "world_event_id": build_world_event_id(source, novelty_id),
        "source": source,
        "event_type": world_event.type,
        "novelty_id": novelty_id,
        "happened_at": world_event.ts,          # 逐字來自 WorldEvent.ts
        "observed_at": utc_iso(observed_at),    # 獨立寫入時鐘
        "summary": world_event.summary,
        "priority": world_event.priority,
        "provenance": {
            "source": source,
            "payload_reference": build_payload_reference(
                getattr(world_event, "data", None)
            ),
        },
    }


# ───────────────────────────────────────────────────────────
# JSONL line-boundary safety (R-1) — writer-side byte-level fix
# ───────────────────────────────────────────────────────────

def escape_line_boundary_chars(text: str) -> str:
    """把 U+0085 / U+2028 / U+2029 換成 JSON escape 序列（``LINE_BOUNDARY_ESCAPES``）。

    為什麼在 writer 端、dumps 之後做：``json.dumps(..., ensure_ascii=False)``
    不逃逸這三個碼位，而 ``str.splitlines()`` 把它們當換行 ⇒ naive 讀者會把
    一個 record 看成多筆。修補必須落在**輸出位元組**上；只改 reader
    （例如改 ``split("\\n")``）不算修好。

    語意保證:
      - 只做**字面**替換，一次、不遞迴 ⇒ 不產生雙重轉義
        （``ensure_ascii=False`` 下這三個字元是以原字元存在於輸出字串中）。
      - ``json.loads()`` 逐字還原原碼位（round-trip）。
      - 不含這三個字元的輸入 ⇒ 回傳**逐位元相同**的字串（零副作用）。
      - 不動其他欄位語意、不做全域重編碼、不改任何非行界字元。
    """
    for char, escaped in LINE_BOUNDARY_ESCAPES:
        if char in text:
            text = text.replace(char, escaped)
    return text


# ───────────────────────────────────────────────────────────
# Runtime exclusion (synthetic / fixture-only sources)
# ───────────────────────────────────────────────────────────

def is_world_log_eligible(world_event: Any) -> bool:
    """False ⇒ 這個 event 一律不得進 World Log。

    Runtime 排除（本票明文）：``SyntheticWorldEventSource`` /
    ``SOULOS_WORLD_PERCEPTION_TEST_SOURCE`` / synthetic / fixture-only
    source runtime route / test bootstrap / mock runtime source。

    精確覆蓋範圍（**不得擴張解讀**）:
      - 本檢查只做 source **字面值** ``"synthetic"`` 比對
        （``EXCLUDED_WORLD_LOG_SOURCES``），**不正規化**大小寫或前後空白
        ⇒ ``"SYNTHETIC"`` / ``" synthetic "`` 仍回 ``True``（eligible）。
      - ``SyntheticWorldEventSource`` 的 **fixture factory** 事件
        （``build_rain_started()`` / ``build_celebrity_news()``）source 是
        ``weather`` / ``news`` / ``calendar`` / ``social`` ⇒ 本檢查對它們回
        ``True``；它們**不在**本檢查覆蓋範圍。
      - 真正的 synthetic 防線是**結構性**的：synthetic runtime route 與
        fixture factory 都不經過三個 production adapter 的
        ``_emit_via_bus()``，所以結構上就不會落到 World Log。
      - 因此本檢查只是**額外的 fail-closed 冗餘**；它**不保證**「synthetic
        永不污染 World Log」，也不得被敘述成能防「未來接線錯誤」的完整
        防線。
    """
    source = getattr(world_event, "source", None)
    if not isinstance(source, str) or not source:
        return False
    if source in EXCLUDED_WORLD_LOG_SOURCES:
        return False
    return True


# ───────────────────────────────────────────────────────────
# A2 retention — complete world-log shards only, by filename date
# ───────────────────────────────────────────────────────────

def parse_shard_date(name: str) -> Optional[date]:
    """嚴格解析 ``YYYY-MM-DD.jsonl`` → date；任何其他形狀回 ``None``。

    非嚴格（malformed）輸入一律 fail-quiet：呼叫端不得刪除無法解析的檔案。
    """
    match = _SHARD_NAME_RE.match(name)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group(1), SHARD_DATE_FORMAT).date()
    except ValueError:
        return None
    # 拒絕「格式合法但日期不存在」者（e.g. 2026-02-30）與 round-trip 失敗者
    if parsed.strftime(SHARD_DATE_FORMAT) != match.group(1):
        return None
    return parsed


def retention_cutoff(now: datetime) -> date:
    """Keep-從此日起（含）：``today_utc - (WORLD_LOG_RETENTION_DAYS - 1)``。

    31 個連續 shard（today-30 .. today）⇒ cutoff = today-29 ⇒ 恰好保留 30 個，
    最早那個（today-30）被刪。
    """
    return utc_calendar_date(now) - timedelta(days=WORLD_LOG_RETENTION_DAYS - 1)


def prune_world_log_shards(
    log_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> List[str]:
    """刪除**嚴格早於 cutoff** 的完整 world-log shard，回傳被刪檔名。

    安全邊界（A2 + 施工補強 B）:
      - 只掃 ``log_dir`` 這一層（非遞迴）的 ``*.jsonl``；
        ``log_dir`` 預設 = ``data_root()/world/world_log``。
      - 只刪 ``parse_shard_date(name) < cutoff`` 的 regular file。
      - cutoff 依**檔名日期**，不看 mtime。
      - symlink / 目錄 / malformed filename / 未知副檔名 / 子目錄 ⇒ 不刪。
      - 任何 OSError ⇒ fail-quiet（log warning，不 raise）。
      - 永不 rewrite / truncate / merge / edit 任何 shard。
    """
    base = Path(log_dir) if log_dir is not None else world_log_dir()
    moment = now if now is not None else datetime.now(timezone.utc)
    cutoff = retention_cutoff(moment)

    deleted: List[str] = []
    try:
        if not base.is_dir():
            return deleted
        candidates = sorted(base.glob("*" + SHARD_SUFFIX))
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning(f"[WorldLog] retention scan 失敗 (不影響主路徑): {exc}")
        return deleted

    for path in candidates:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            shard_date = parse_shard_date(path.name)
            if shard_date is None or shard_date >= cutoff:
                continue
            path.unlink()
            deleted.append(path.name)
        except OSError as exc:
            logger.warning(
                f"[WorldLog] retention 刪除失敗 (fail-quiet): {path.name} | {exc}"
            )
            continue

    if deleted:
        logger.info(
            f"[WorldLog] retention cutoff={cutoff.isoformat()} "
            f"deleted={deleted} dir={base}"
        )
    return deleted


# ───────────────────────────────────────────────────────────
# Writer
# ───────────────────────────────────────────────────────────

class WorldLogWriter:
    """Append-only World Log writer.

    Args:
        log_dir: 覆寫 shard 目錄（測試用）。``None`` ⇒ 每次呼叫時解析
            ``data_root()/world/world_log``（讓 SOUL_OS_DATA_DIR 隔離生效）。
    """

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self._log_dir: Optional[Path] = (
            Path(log_dir) if log_dir is not None else None
        )
        # retention frequency gate: 每個 writer 每個 UTC 日曆日至多掃一次
        self._last_retention_date: Optional[date] = None

    @property
    def log_dir(self) -> Path:
        return self._log_dir if self._log_dir is not None else world_log_dir()

    @property
    def last_retention_date(self) -> Optional[date]:
        """最近一次 retention 掃描所對應的 UTC 日曆日（None = 尚未掃過）。"""
        return self._last_retention_date

    def write(self, world_event: Any, now: Optional[datetime] = None) -> bool:
        """Append 一筆 world fact record。

        Args:
            world_event: 已建構、已 validated、尚未 publish 的 ``WorldEvent``。
            now: 測試用注入點；``None`` ⇒ 用寫入當下的 wall clock（production
                一律走這條，``observed_at`` 由此獨立取得，與 ``happened_at``
                無關）。

        Returns:
            True = 已 append；False = 未寫入（excluded source / 任何寫入失敗）。

        寫入失敗不 raise（與 ``WorldPerceptionTraceWriter.write`` 同派工精神）。
        """
        if not is_world_log_eligible(world_event):
            return False

        try:
            observed_at = now if now is not None else datetime.now(timezone.utc)
            record = build_record(world_event, observed_at)
            path = self.log_dir / shard_name(observed_at)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, default=str)
            # R-1: 行界安全必須落在輸出位元組上（唯一呼叫點）。
            line = escape_line_boundary_chars(line)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            logger.warning(
                f"[WorldLog] 寫入失敗 (不影響主路徑): "
                f"{type(exc).__name__}: {exc}"
            )
            return False

        # Retention 是 best-effort，且與本次寫入成功無關。
        try:
            self._maybe_prune(observed_at)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[WorldLog] retention 失敗 (不影響主路徑): {exc}")
        return True

    def _maybe_prune(self, observed_at: datetime) -> List[str]:
        """每個 UTC 日曆日至多掃一次 world-log 目錄（frequency gate）。"""
        today = utc_calendar_date(observed_at)
        if self._last_retention_date == today:
            return []
        self._last_retention_date = today
        return prune_world_log_shards(log_dir=self.log_dir, now=observed_at)


# ───────────────────────────────────────────────────────────
# Adapter entry point (the ONLY runtime call the adapters make)
# ───────────────────────────────────────────────────────────

_default_writer: Optional[WorldLogWriter] = None


def get_world_log_writer() -> WorldLogWriter:
    """Process-lifetime default writer（不快取路徑，每次寫入重新解析）。"""
    global _default_writer
    if _default_writer is None:
        _default_writer = WorldLogWriter()
    return _default_writer


def record_world_event(world_event: Any, now: Optional[datetime] = None) -> bool:
    """Adapter-first World Log write — 三個 production adapter 的唯一入口。

    必須在 ``_emit_via_bus()`` 之內、``bus.publish()`` **之前**呼叫。

    Never raises: World Log 是證據面，不得影響 adapter 主路徑。
    """
    try:
        return get_world_log_writer().write(world_event, now=now)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"[WorldLog] record_world_event 失敗 (不影響主路徑): "
            f"{type(exc).__name__}: {exc}"
        )
        return False
