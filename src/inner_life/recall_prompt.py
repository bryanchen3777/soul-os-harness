"""
src/inner_life/recall_prompt.py — 方向 A 第二階段：回憶區塊的 prompt 組裝 + 資料判準啟用閘門

**為什麼存在**（SIM-ACCEL-RECALL-30D 工單 §3.2）
------------------------------------------------
第一階段（ELEVATION-RECALL-HARDENING-1）把「DORMANT / SUPERSEDED 可回憶」做成了
**讀側能力**（``elevation_recall.py``：查得到、但還沒接到 prompt）。第二階段要接，
而「接」有兩件事必須先站穩，否則接上去只是把過去的信念灌進現在的嘴裡：

  ① **語氣隔離**（契約 §3.1）：回憶是「過去」，不是現在的信念。
     ⇒ 回憶走**獨立區塊 + 獨立標題**，**禁入** EMERGENT block（「你成為什麼」＝現在的
     自我定義）。混在一起就等於讓模型把「我以前覺得」當成「我現在覺得」。
  ② **什麼時候才接**（工單 §9 的四項 GO 判準）：**不是日期、不是天數、不是人工開關**，
     而是**生產圖譜本身的成熟度**——四項判準全過才啟用。日期判準會在圖譜還沒長出
     回憶池的時候就把「我以前很在意」塞進 prompt（空轉）；人工開關則會讓「接上」
     變成一個靠人記得的事實。

本模組的紀律（與 ``elevation_recall.py`` 同款）
----------------------------------------------
  - **唯讀**：不寫任何節點檔／邊檔／sidecar，0 狀態遷移，0 LLM，0 網路請求。
  - **fail-silent**：任何例外 → 空字串 ⇒ prompt 與未實作時**逐位元等價**。
  - **deterministic**：同一份圖譜 ＋ 同一句話 ⇒ 同一段區塊。

啟用閘門（:func:`is_recall_enabled`）
------------------------------------
  - 四項判準用**同一套純函式**算（``recall_readiness.pool_summary`` / ``hit_rate`` /
    ``go_no_go``）——**不重寫一份算法**，避免量測面與啟用面漂移。
  - **行程內快取**（TTL 30 分鐘）：key ＝ 節點檔 mtime ＋ sidecar mtime ＋ 對話目錄
    最新 mtime。避免每次 prompt 組裝都重掃 200 筆真實訊息。
  - **唯一人工開關** ＝ 環境變數 ``SOUL_OS_RECALL_INJECTION``（``off``/``0``/``false``
    ⇒ 一律關）。用途只有一個：緊急止血。預設是「自動 ＝ 資料判準」。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("soul_os.inner_life.recall_prompt")

#: 回憶區塊的獨立標題（**不得**與 MEMORY 區塊共用前綴——兩個語氣必須分得開）。
RECALL_BLOCK_HEADER = "【回憶】（這些是你過去在意過的事，不代表你現在的想法）"

#: 啟用閘門的人工 kill switch（唯一的人工開關；預設＝自動＝資料判準）。
RECALL_INJECTION_ENV = "SOUL_OS_RECALL_INJECTION"
#: kill switch 的關閉值（小寫比對；缺省＝未設定＝不關閉）。
RECALL_INJECTION_OFF_VALUES: frozenset = frozenset({"off", "0", "false"})

#: 啟用判準的行程內快取 TTL（秒）＝ 30 分鐘。
RECALL_ENABLE_CACHE_TTL_SECONDS = 30 * 60

#: 單一區塊最多呈現幾顆命中（呈現層守恆：區塊不能變成 context dump）。
MAX_RECALL_BLOCK_HITS = 3

#: 命中率判準的探針數（單位：probe ＝ 一筆真實 user 訊息）；與量測 CLI 同口徑。
PROBE_LIMIT = 200

#: 話頭掛載區塊的獨立標題（SIM-EPIS-PIGGYBACK-1）。
#:
#: 🔴 **不得**與 ``【回憶】`` 共用前綴——兩者語氣必須分得開：
#: 回憶是「我以前在意過的事」，話頭是「我後來自己想通、此刻可以順嘴提一句的事」。
PIGGYBACK_BLOCK_HEADER = (
    "【話頭】（這是你後來自己想通的一點小事；只有當話題正好相關時才順嘴帶一句，"
    "不相關就當沒這回事）"
)

#: 行程內快取（單例；key 未變且 TTL 未到 ⇒ 直接回傳快取值）。
_ENABLE_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "enabled": False, "verdict": None}

#: 上一次寫過 log 的啟用狀態（**只在狀態變化時**寫一行，避免每輪刷日誌）。
_LAST_LOGGED_STATE: Dict[str, Any] = {"enabled": None}


# ── 路徑解析（皆可被測試 monkeypatch；生產走 data_root()）───────────────
def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    """解析圖譜目錄：顯式傳入 > ``data_root()/elevation``（對齊 emergent_projection）。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root  # lazy import 避免循環

    return Path(data_root()) / "elevation"


def runtime_conversations_dir() -> Path:
    """對話（探針）目錄 ＝ ``data_root()/conversations``（唯讀）。

    與 ``src/llm/proxy.py::CONV_DIR`` 同一條佈局約定（``data/conversations/``）。
    """
    from src.paths import data_root  # lazy import 避免循環

    return Path(data_root()) / "conversations"


# ── 探針收集（詞元化走 SSOT，不重寫一份）───────────────────────────────
def _agent_id_from_stem(stem: str) -> str:
    """從對話檔名推回 agent id（``…_agent_<name>_private`` → ``agent_<name>``）。"""
    head = stem
    if head.endswith("_private"):
        head = head[: -len("_private")]
    if "_agent_" in head:
        return "agent_" + head.rsplit("_agent_", 1)[-1]
    return ""


def collect_probe_messages(
    limit: int = PROBE_LIMIT,
    *,
    conversations_dir: Optional[Any] = None,
) -> List[Tuple[str, List[str]]]:
    """收集真實 user 訊息並詞元化 → ``[(agent_id, tags), ...]``（唯讀）。

    規則與 ``scripts/recall_readiness_cli.py::collect_user_messages`` **逐條同語意**
    （同一份漂移守衛見 ``tests/test_recall_prompt_injection.py``）：

      - 只認 ``…_agent_<name>_private.json``（單一靈魂的私聊）；群聊沒有唯一 agent。
      - 依檔案 mtime 由新到舊排序（＝「最近」），檔內照原序取 ``role == "user"``。
      - 詞元化走 SSOT ``src/memory/v1/loader.py::derive_query_tags``（**不重寫一份**）。

    失敗隔離：目錄不存在／檔壞／非 list → 跳過，**絕不 raise**。
    """
    try:
        from src.memory.v1.loader import derive_query_tags
    except Exception as exc:  # pragma: no cover — 依賴缺失時明確失敗（不讓 prompt 爆）
        logger.warning("[recall] 無法載入詞元 SSOT（derive_query_tags）：%s: %s",
                       type(exc).__name__, exc)
        return []

    conv_dir = (
        Path(conversations_dir) if conversations_dir is not None
        else runtime_conversations_dir()
    )
    if not conv_dir.exists():
        return []

    try:
        files = [p for p in conv_dir.glob("*.json") if p.is_file()]
        files.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    except OSError as exc:
        logger.warning("[recall] 對話目錄讀取失敗：%s: %s", type(exc).__name__, exc)
        return []

    out: List[Tuple[str, List[str]]] = []
    for path in files:
        if len(out) >= limit:
            break
        agent_id = _agent_id_from_stem(path.stem)
        if not agent_id:
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(records, list):
            continue
        for rec in records:
            if len(out) >= limit:
                break
            if not isinstance(rec, dict) or rec.get("role") != "user":
                continue
            content = rec.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            out.append((agent_id, derive_query_tags(content)))
    return out


# ── 啟用判準（資料判準，不是日期判準）─────────────────────────────────
def _kill_switch_off() -> bool:
    """人工 kill switch 是否關閉（``SOUL_OS_RECALL_INJECTION`` in off/0/false）。"""
    raw = os.environ.get(RECALL_INJECTION_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in RECALL_INJECTION_OFF_VALUES


def _mtime_ns(path: Path) -> int:
    """檔案 mtime（ns）；檔不存在回 ``-1``（不 raise）。"""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _latest_mtime_ns(directory: Path) -> int:
    """目錄下最新檔案的 mtime（ns）；目錄不存在／讀不到回 ``-1``。"""
    try:
        mtimes = [p.stat().st_mtime_ns for p in directory.glob("*.json") if p.is_file()]
    except OSError:
        return -1
    return max(mtimes) if mtimes else -1


def _enable_cache_key(store_dir: Optional[Any]) -> Tuple[Any, ...]:
    """快取 key：節點檔 mtime ＋ sidecar mtime ＋ 對話目錄最新 mtime（任一變動即重算）。"""
    from src.inner_life.emergent_projection import NODES_FILENAME
    from src.inner_life.lifecycle_overlay import LIFECYCLE_EVENTS_FILENAME

    resolved = _resolve_store_dir(store_dir)
    return (
        str(resolved),
        _mtime_ns(resolved / NODES_FILENAME),
        _mtime_ns(resolved / LIFECYCLE_EVENTS_FILENAME),
        _latest_mtime_ns(runtime_conversations_dir()),
    )


def _compute_enable_verdict(store_dir: Optional[Any]) -> Tuple[bool, dict]:
    """用**同一套純函式**算四項 GO 判準；回 ``(是否 GO, verdict)``。"""
    from src.inner_life import recall_readiness
    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    resolved = _resolve_store_dir(store_dir)
    nodes = load_elevation_nodes(resolved)
    quarantined = load_quarantined_node_ids(resolved)
    summary = recall_readiness.pool_summary(nodes, quarantined)
    probes = collect_probe_messages(PROBE_LIMIT)
    hit = recall_readiness.hit_rate(nodes, probes, quarantined_ids=quarantined)
    verdict = recall_readiness.go_no_go(summary, hit)
    return verdict.get("verdict") == "GO", verdict


def _log_state_change(enabled: bool, verdict: Optional[dict]) -> None:
    """啟用狀態**變化時**寫一行 log（含四項判準的值）。"""
    if _LAST_LOGGED_STATE.get("enabled") == enabled:
        return
    _LAST_LOGGED_STATE["enabled"] = enabled
    checks = (verdict or {}).get("checks") or {}
    detail = ", ".join(
        "%s=%s" % (key, checks[key].get("value")) for key in sorted(checks)
    ) or "<no checks>"
    logger.info(
        "[recall] 注入啟用狀態變化：enabled=%s（%s）",
        enabled, detail,
    )


def is_recall_enabled(*, store_dir: Optional[Any] = None) -> bool:
    """生產圖譜的四項 GO 判準是否全過。全過才啟用注入。

    - **資料判準**：用 ``pool_summary`` / ``hit_rate`` / ``go_no_go`` 對圖譜算一次。
    - **不得**用日期／天數／人工開關決定啟用（唯一例外是 kill switch，見上）。
    - 行程內快取 TTL 30 分鐘；key ＝ 節點檔／sidecar／對話目錄的 mtime。
    - 任何例外 → ``False``（寧可不注入，不可讓 prompt 組裝失敗）。
    """
    try:
        if _kill_switch_off():
            _log_state_change(False, {"checks": {"kill_switch": {"value": "off"}}})
            return False

        key = _enable_cache_key(store_dir)
        now = time.monotonic()
        cached = _ENABLE_CACHE
        if (
            cached.get("key") == key
            and cached.get("verdict") is not None
            and (now - float(cached.get("ts") or 0.0)) < RECALL_ENABLE_CACHE_TTL_SECONDS
        ):
            enabled = bool(cached.get("enabled"))
            _log_state_change(enabled, cached.get("verdict"))
            return enabled

        enabled, verdict = _compute_enable_verdict(store_dir)
        _ENABLE_CACHE.update({"key": key, "ts": now, "enabled": enabled, "verdict": verdict})
        _log_state_change(enabled, verdict)
        return enabled
    except Exception as exc:  # noqa: BLE001 — fail-silent：啟用判準壞掉＝不注入
        logger.debug("[recall] 啟用判準失敗（不注入）：%s: %s", type(exc).__name__, exc)
        return False


# ── 區塊組裝（呈現層；語氣隔離在這裡）────────────────────────────────
def _superseder_contents(
    hits: List[Dict[str, Any]], *, store_dir: Optional[Any] = None
) -> Dict[str, str]:
    """取「取代者正文」對照表（``hit.node_id`` → 取代者 content）。

    ``format_recall_hit`` 的 superseded 分句需要取代者正文才會渲染成
    「我以前覺得 A，但後來發現 B」；recall 本身**不**展開遞迴查詢（契約 §3.1），
    故由本模組（呼叫端）另查一次。查不到就退回「但後來改變了」。
    """
    out: Dict[str, str] = {}
    wanted = [h.get("superseded_by") for h in hits if h.get("superseded_by")]
    if not wanted:
        return out
    try:
        from src.inner_life.emergent_projection import load_elevation_nodes

        nodes = load_elevation_nodes(_resolve_store_dir(store_dir))
    except Exception:  # noqa: BLE001 — fail-silent：拿不到正文就退化句式
        return out
    index = {
        n.get("node_id"): n.get("content")
        for n in nodes
        if isinstance(n, dict) and isinstance(n.get("content"), str)
    }
    for hit in hits:
        superseder = index.get(hit.get("superseded_by"))
        if isinstance(superseder, str) and superseder.strip():
            out[str(hit.get("node_id"))] = superseder
    return out


def format_recall_block(
    agent_id: str,
    utterance: str,
    *,
    store_dir: Optional[Any] = None,
    max_hits: int = MAX_RECALL_BLOCK_HITS,
) -> str:
    """回憶區塊（契約 §3.1 語氣隔離 + 工單 §9 附錄 B）。fail-silent：例外 → ``""``。

    - 命中來源：``query_recall_by_tags``（詞元級 union 比對）；詞元走 SSOT
      ``derive_query_tags``（**不重寫一份詞元化**）。
    - 呈現走既有 ``format_recall_hit``（**逐字不改**）：DORMANT →「我以前很在意……」，
      SUPERSEDED →「我以前覺得 A，但後來發現 B」。
    - **啟用判準**：``is_recall_enabled`` 為 False ⇒ 直接回 ``""``（現況生產池 = 0 ⇒
      prompt 與未實作時逐位元相同）。
    - 區塊**不得**進 EMERGENT block（由呼叫端以獨立 ``system_parts`` 項目接入）。
    """
    try:
        if not isinstance(agent_id, str) or not agent_id:
            return ""
        if not isinstance(utterance, str) or not utterance.strip():
            return ""
        if max_hits <= 0:
            return ""
        if not is_recall_enabled(store_dir=store_dir):
            return ""

        from src.memory.v1.loader import derive_query_tags

        tags = derive_query_tags(utterance)
        if not tags:
            return ""

        from src.inner_life.elevation_recall import format_recall_hit, query_recall_by_tags

        hits = query_recall_by_tags(agent_id, tags, store_dir=store_dir)
        if not hits:
            return ""

        superseders = _superseder_contents(hits, store_dir=store_dir)
        lines = [RECALL_BLOCK_HEADER]
        for hit in hits[:max_hits]:
            lines.append(
                format_recall_hit(hit, superseders.get(str(hit.get("node_id"))))
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 — fail-silent：prompt 與未實作時等價
        logger.debug("[recall] 區塊組裝失敗（不注入）：%s: %s", type(exc).__name__, exc)
        return ""


def format_piggyback_block(
    agent_id: str,
    utterance: str,
    *,
    store_dir: Optional[Any] = None,
    now: Optional[float] = None,
) -> str:
    """話頭掛載區塊（SIM-EPIS-PIGGYBACK-1 ②）。fail-silent：例外 → ``""``。

    - **獨立區塊**：標題與 ``【回憶】`` 分開，**不得**混入回憶、**不得**進
      EMERGENT block（語氣隔離同款紀律）。
    - **一次最多 1 個**：挑選由 ``piggyback_queue.pick_for_topic`` 負責
      （話題 tags 重疊 ≥ 1 才挑、帶出即消耗）。
    - **無相關話題就不帶**（寧可不帶）。
    - **預設關閉**：全票旗標 ``SOUL_OS_EPISTEMIC_PIGGYBACK`` 未開 ⇒ 直接回 ``""``
      ⇒ 生產 prompt 逐位元不變（工單 §4.5）。
    - **不影響發訊決策**：本函式只是把**已經要發的訊**加上尾巴；它不參與
      「是否主動發訊」的判斷。
    """
    try:
        if not isinstance(agent_id, str) or not agent_id:
            return ""
        if not isinstance(utterance, str) or not utterance.strip():
            return ""
        from src.inner_life import piggyback_queue

        if not piggyback_queue.feature_enabled():
            return ""

        from src.memory.v1.loader import derive_query_tags

        tags = derive_query_tags(utterance)
        if not tags:
            return ""

        item = piggyback_queue.pick_for_topic(
            agent_id, tags, store_dir=store_dir, now=now
        )
        if item is None:
            return ""
        payload = (item.payload or "").strip()
        if not payload:
            return ""
        return f"{PIGGYBACK_BLOCK_HEADER}\n{payload}"
    except Exception as exc:  # noqa: BLE001 — fail-silent：prompt 與未實作時等價
        logger.debug("[piggyback] 區塊組裝失敗（不注入）：%s: %s", type(exc).__name__, exc)
        return ""


__all__ = [
    "MAX_RECALL_BLOCK_HITS",
    "PIGGYBACK_BLOCK_HEADER",
    "PROBE_LIMIT",
    "RECALL_BLOCK_HEADER",
    "RECALL_ENABLE_CACHE_TTL_SECONDS",
    "RECALL_INJECTION_ENV",
    "RECALL_INJECTION_OFF_VALUES",
    "collect_probe_messages",
    "format_piggyback_block",
    "format_recall_block",
    "is_recall_enabled",
    "runtime_conversations_dir",
]
