"""
src/inner_life/elevation_adapter.py — Soul OS ↔ soul-elevation adapter seam

本模块是 Soul OS 侧**唯一**接触 `soul_elevation` 的地方。它把上游只读数据
（`InnerLifeEvent` + v1 `Memory` + SAGE `Fact`）映射成 soul-elevation 的
`ElevationInput`，喂给 `InternalizingEngine`，产出升华节点（belief / value /
trait / essence），并把结果写入 soul-elevation 自有 store（`data/elevation/`）。

定位（照 docs/MEMORY-ELEVATION-DESIGN.md §2）：
  - **只读消费者 / 旁路观察者**：adapter 只读 InnerLifeEvent + Memory，
    **不调用** ``InnerLifeWriter.create_event()``（不是第 6 个 producer）。
  - **不改任何 frozen contract**：不改 InnerLifeEvent schema（M5.4-5.1）、
    TriggerEnvelope（M5.2-F）、Agency 4 stages / 4 handlers、SAGE 写入逻辑、
    InnerLifeWriter、NarrativeTrace。
  - **只写自有 store**：写 ``data/elevation/``（elevation_trace.jsonl +
    elevation_nodes.jsonl + elevation_edges.jsonl），不碰 memory.db / SAGE
    graph / trace.jsonl。

触发方式（照工单关键决策 #4）：Soul OS 在 InnerLifeEvent 写入**之后**调用
``run_elevation(inner_life_event, memory_facts)``（或经 ``ElevationObserver``），
adapter 接在写路径之后，是 fire-and-forget，失败隔离不阻断写路径。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from soul_elevation import (
    DEFAULT_ELEVATE_MIN_EVIDENCE,
    ElevationInput,
    ElevationNode,
    ElevationTraceWriter,
    EvidenceEdge,
    InternalizingEngine,
    SOUL_NODE_TYPES,
    StubElevationLLM,
)

if TYPE_CHECKING:  # 仅类型标注，运行时不执行（避免拉入 networkx 等重依赖链）
    from src.memory.sage.models import Fact
    from src.memory.v1.schema import Memory

    from .event import InnerLifeEvent

logger = logging.getLogger("soul_os.inner_life.elevation_adapter")

# 默认 store 目录名（挂在 data_root() 下）。
ELEVATION_DIR_NAME = "elevation"

# 三个自有 store 文件名（独立于上游 store，不碰 trace.jsonl / memory.db / SAGE graph）。
TRACE_FILENAME = "elevation_trace.jsonl"
NODES_FILENAME = "elevation_nodes.jsonl"
EDGES_FILENAME = "elevation_edges.jsonl"

# 事件 / memory / fact 归一化为 ElevationInput 时的统一 event_type（走 prior.py 的
# CATEGORY_TRIGGER_TYPES 分支，依 provenance 里的 category 决定先验维度）。
_EVENT_TYPE_MEMORY_FACT = "memory_fact"

# EH-2.1 垂直防火牆（契約 §4.3）: 阻斷判定與 submission_gate 同源 ——
# is_world_media_trigger（外部媒體情報 world:news* / world:feed* /
# world:celebrity_news 阻斷;環境與日程 world:weather* / world:rain* /
# world:calendar* / world:user_going_outside* 放行）。origin 常數由
# src/memory/sage/horizon.py 提供（單一事實來源, 輕量常數, 無重依賴）。
from .submission_gate import is_world_media_trigger
# SOUL-BUG-010 防線二：分級消費的終態／休眠集合（單一事實來源於 lifecycle_overlay）。
# 模組層 import 安全：lifecycle_overlay 是純標準函式庫，不 import 本檔也不 import
# emergent_projection，故無循環。
from .lifecycle_overlay import TERMINAL_OR_DORMANT_LIFECYCLE_STATES

_ORIGIN_EXTERNAL_WORLD = "external_world"
_ORIGIN_ASSIMILATED = "assimilated"


def _fact_origin(fact: Any) -> str:
    """SAGE Fact 的 origin 標記（EH-2 additive; 缺省空字串 = 未標記, 不阻斷）。"""
    return str(getattr(fact, "origin", None) or "")


def _load_fact_origins(agent_id: str) -> dict[str, str]:
    """讀該 agent 的 SAGE graph.sqlite 建 fact_id → origin 映射（EH-2 R3 用）。

    位置: ``data_root()/memory/{agent_id}/graph.sqlite``（對齊 MemoryMiddleware 佈局）。
    純讀（一般連線 + SELECT-only, 0 資料寫入）;fail-silent:
    檔案不存在 / 舊庫無 origin 欄 / 任何異常 → 回 {}（不阻斷既有昇華主路徑）。
    """
    if not agent_id:
        return {}
    try:
        from src.paths import data_root

        db_path = data_root() / "memory" / agent_id / "graph.sqlite"
        if not db_path.exists():
            return {}
        import sqlite3

        # 不能用 URI mode=ro: SAGE 走 WAL 模式 — sqlite 的唯讀連線無法讀 WAL
        # (SQLITE_READONLY_CANTLOCK), 會靜默失敗。一般連線配 SELECT-only
        # 即為讀側 (0 資料寫入; 僅可能產生 transient -shm)。
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT fact_id, origin FROM facts "
                "WHERE origin IS NOT NULL AND origin != ''"
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — fail-silent: 讀不到 origin 不阻斷昇華
        logger.debug(f"[elevation] _load_fact_origins 失敗: {type(exc).__name__}: {exc}")
        return {}


def _utcnow_iso() -> str:
    """当前 UTC 时刻的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _ts_to_iso(ts: Any) -> str:
    """把 unix 时间戳（float/int）转 ISO 8601 UTC 字符串。

    v1 Memory.created_at / SAGE Fact.timestamp 都是 unix float；ElevationInput
    的 timestamp 约定是 ISO 8601 UTC，故在此归一化。
    """
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


# SOUL-BUG-006（2026-10-17，Owner 裁示）：敘事欄位白名單。
#
# 🔴 這份清單是**從生產資料實測反推**出來的，不是憑印象寫的。
# 反解 elevation_nodes.jsonl 的 content 後，全部 extras 鍵只有：
#   world : world_novelty_id / world_source / world_type（各 353）—— **無敘事**
#   diary : narrative(290) / slot(290) / origin_type/thread_id/title/sage_fact_id(各 69)
#   dream : all_agents_count(60) / target_agent_id(60)—— **無敘事**
# ⇒ **`narrative` 是唯一帶人話正文的鍵**。
#
# 刻意**不收** `title`（只出現在 life_thread 衍生事件、共 69 次）：
# 它是標題不是敘事，用標題冒充正文會讓節點語意失真；寧可少一顆節點。
_NARRATIVE_EXTRA_KEYS = ("narrative",)

# SOUL-BUG-006 Step 4（夢境端 A-i，Owner 2026-10-17 授權）：
# 反查夢境正文時往回看幾天的 diary 檔。夢境正文在 write_dream 落盤的當天寫入，
# 而 consume 緊接在 write_dream 之後執行，故正常情況只看當天就找得到；
# 保留 3 天是為了容忍跨零點與檔名日期與 UTC 落盤時間不一致。
# **有界**：最多 3 個檔案，且每檔逐行掃描後即停。
_DREAM_LOOKBACK_DAYS = 3


def _dream_narrative_from_diary(inner_life_event: Any) -> str:
    """依 ``inner_life_event_id`` 反查夢境正文；查不到就回 ""（fail-closed）。

    🔴 為什麼需要這一段（Owner 2026-10-17 拍板的架構）：
    夢境事件的 ``extras`` 實測只有 ``all_agents_count`` / ``target_agent_id``
    —— 純排程統計，**沒有敘事**。而 `write_dream()` 是在事件建立**之後**才
    用 LLM 生成夢境正文並寫入 diary（`data/soul/<dreamer>/diary/<日期>.jsonl`，
    該筆紀錄帶 `inner_life_event_id`，見 M5.4-5.4）。
    ⇒ 正文在事件裡從來不存在，consume 端只能**用 event_id 反查剛落盤的那筆**。
    這是純加法：零簽章變更、零 Append-Only 邊界破壞、零二次 Submit。

    🔴 刻意不做的事：
      - **不寫回事件**。`InnerLifeWriter` 是 Append-Only 唯一權威、沒有任何
        update/patch API；這裡只在 consume 當下解析，事件本身一個字都不動。
      - **不改 InnerLifeEvent 物件**。注意 `SubmissionGate.verify()` 會
        `writer.get_event(event_id)` **重新撈取** canonical 事件，所以就算在
        呼叫端改記憶體中的 extras 也不會被看見 —— 反查必須發生在 adapter 內部。
      - **查不到就回 ""**，交給既有的 fail-closed。絕不拿 target_agent_id
        之類的 metadata 拼一句假敘事。
    """
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    from pathlib import Path as _Path

    try:
        event_id = getattr(inner_life_event, "event_id", None)
        dreamer = getattr(getattr(inner_life_event, "provenance", None), "actor_id", None)
        if not event_id or not dreamer:
            return ""
        from src.paths import data_root

        diary_dir = _Path(str(data_root())) / "soul" / str(dreamer) / "diary"
        if not diary_dir.is_dir():
            return ""
        today = _dt.now()
        for back in range(_DREAM_LOOKBACK_DAYS):
            day = (today - _td(days=back)).strftime("%Y-%m-%d")
            path = diary_dir / f"{day}.jsonl"
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or event_id not in line:
                        continue
                    try:
                        entry = _json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("inner_life_event_id") != event_id:
                        continue
                    if entry.get("slot") != "dream":
                        continue
                    text = entry.get("content")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
    except Exception:  # noqa: BLE001 —— 反查是附加價值，絕不可讓主路徑失敗
        return ""
    return ""


def _resolve_narrative(inner_life_event: Any) -> str:
    """決定這次昇華要用的人話敘事：先看 extras，沒有再看夢境 diary 反查。"""
    content = _event_content(inner_life_event)
    if content:
        return content
    trigger = getattr(getattr(inner_life_event, "provenance", None), "trigger_type", "") or ""
    if str(trigger).startswith("dream:"):
        return _dream_narrative_from_diary(inner_life_event)
    return ""


def _event_content(inner_life_event: Any) -> str:
    """InnerLifeEvent 的「事件內容」：乾淨的人話敘事正文；沒有就回空字串。

    SOUL-BUG-006（2026-10-17）：舊版回 ``trigger_type + provenance.extras``，
    也就是把 ``diary:night: narrative=…; slot=night`` 這種 **key=value 簿記外殼**
    當成節點正文 —— 於是靈魂節點的 content 從來不是「一句命題」，
    而是原始事件的序列化。最糟的是夢境事件：它的 extras 只有
    ``all_agents_count`` / ``target_agent_id``，於是「今天排了 10 隻 agent、
    對象是 yua」這種**純排程統計**被沉澱成靈魂的「特質」。

    現在只認 ``_NARRATIVE_EXTRA_KEYS`` 裡真正帶正文的欄位；取不到就回傳 ""，
    由呼叫端做 **fail-closed**（不建立節點）。這是刻意的 fail-closed：
    **寧可少一顆靈魂節點，也不要讓垃圾原料進圖譜。**

    metadata 並未遺失：``extras`` 仍完整並入 ``provenance``
    （見 :182），prior / LLM 後驗照樣讀得到全部欄位。
    """
    extras = getattr(inner_life_event.provenance, "extras", None) or {}
    if isinstance(extras, dict):
        for key in _NARRATIVE_EXTRA_KEYS:
            val = extras.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _fact_content(fact: Any) -> str:
    """SAGE Fact 的「内容」：subject predicate object 三元组拼成一句。"""
    return f"{fact.subject} {fact.predicate} {fact.object}"


def _fact_agent(fact: Any) -> Optional[str]:
    """从 SAGE Fact.source_pair（"<user_id>:<agent_id>"）抽取 agent_id。"""
    sp = getattr(fact, "source_pair", None)
    if isinstance(sp, str) and ":" in sp:
        return sp.split(":", 1)[1]
    return None


# ─────────────────────────────────────────────────────────────────────
# 映射（InnerLifeEvent / Memory / Fact → ElevationInput）
# ─────────────────────────────────────────────────────────────────────


def inner_life_event_to_input(
    inner_life_event: Any,
    *,
    agent_id: Optional[str] = None,
    narrative: Optional[str] = None,
) -> Optional[ElevationInput]:
    """把 ``InnerLifeEvent`` 映射成 ``ElevationInput``（source_type="inner_life_event"）。

    🔴 SOUL-BUG-006（2026-10-17）：**回傳型別改為 Optional**。
    事件若沒有可提取的人話敘事（純排程／系統 metadata），回傳 ``None``，
    由呼叫端 **fail-closed 跳過**，不送進 ``engine.consume()``。
    這是「拒絕建立垃圾節點」的唯一落點。

    字段映射（照工单关键决策 #3）：
      - event_type  = provenance.trigger_type
      - content     = **乾淨的人話敘事正文**（SOUL-BUG-006；舊版是
                      ``trigger_type + provenance.extras`` 的 key=value 外殼）
      - source_id   = event_id
      - source_type = "inner_life_event"
      - timestamp   = event.ts（已是 ISO 8601 UTC）
      - provenance  = dict（trigger_type / source_system / actor_id / agent_id +
        **完整 extras** / identity 元数据）—— metadata 一個都沒少，只有 content 脫殼
    """
    content = (narrative or "").strip() or _event_content(inner_life_event)
    if not content:
        return None

    p = inner_life_event.provenance
    provenance: dict[str, Any] = {
        "trigger_type": p.trigger_type,
        "source_system": p.source_system,
        "actor_id": p.actor_id,
    }
    # extras 是 canonical 元数据（world_type / world_source / world_novelty_id /
    # qualification_reason 等），原样并入 provenance，供 prior / LLM 后验读取。
    provenance.update(dict(getattr(p, "extras", {}) or {}))

    # 归属 agent（灵魂本体）。显式传入 > extras.agent_id > actor_id（若形似 agent）。
    resolved_agent = agent_id or provenance.get("agent_id") or p.actor_id
    if resolved_agent:
        provenance["agent_id"] = resolved_agent

    # 身份/lineage 元数据（join 回 trace 的审计锚点）。
    provenance["inner_life_event_id"] = inner_life_event.event_id
    provenance["correlation_id"] = inner_life_event.correlation_id
    provenance["session_id"] = inner_life_event.session_id
    provenance["parent_event_id"] = inner_life_event.parent_event_id
    provenance["source_world_event_novelty_id"] = (
        inner_life_event.source_world_event_novelty_id
    )

    return ElevationInput(
        event_type=p.trigger_type,
        content=content,
        source_id=inner_life_event.event_id,
        source_type="inner_life_event",
        timestamp=inner_life_event.ts,
        provenance=provenance,
    )


def v1_memory_to_input(memory: Any, *, agent_id: Optional[str] = None) -> ElevationInput:
    """把 v1 ``Memory`` 映射成 ``ElevationInput``（source_type="v1_memory"）。

    字段映射：
      - event_type  = "memory_fact"（走 prior.py 类别分支，依 category 定先验）
      - content     = memory.content（原文）
      - source_id   = memory.memory_id
      - source_type = "v1_memory"
      - timestamp   = memory.created_at（unix float → ISO 8601 UTC）
      - provenance  = dict（agent_id / category / confidence / tags / inner_life_event_id）
    """
    provenance: dict[str, Any] = {
        "agent_id": agent_id or memory.agent_id,
        "category": memory.category,
        "confidence": memory.confidence,
        "tags": list(memory.tags or []),
        "inner_life_event_id": memory.inner_life_event_id,
    }
    return ElevationInput(
        event_type=_EVENT_TYPE_MEMORY_FACT,
        content=memory.content,
        source_id=memory.memory_id,
        source_type="v1_memory",
        timestamp=_ts_to_iso(memory.created_at),
        provenance=provenance,
    )


def sage_fact_to_input(fact: Any, *, agent_id: Optional[str] = None) -> ElevationInput:
    """把 SAGE ``Fact`` 映射成 ``ElevationInput``（source_type="sage_fact"）。

    字段映射：
      - event_type  = "memory_fact"
      - content     = "subject predicate object"（三元组拼句）
      - source_id   = fact.fact_id
      - source_type = "sage_fact"
      - timestamp   = fact.timestamp（unix float → ISO 8601 UTC）
      - provenance  = dict（agent_id / confidence / weight / inner_life_event_id 等）
    """
    provenance: dict[str, Any] = {
        "agent_id": agent_id or _fact_agent(fact),
        "confidence": fact.confidence,
        "weight": fact.weight,
        "source": fact.source,
        "session_id": fact.session_id,
        "inner_life_event_id": fact.inner_life_event_id,
        # EH-2 (additive metadata): origin 標記隨 provenance 落庫, 供 R3 審查
        # （assimilated 知識封頂 Fact/Pattern/Meaning, 禁入 Belief/Value/Trait/Essence）。
        "origin": getattr(fact, "origin", None),
    }
    return ElevationInput(
        event_type=_EVENT_TYPE_MEMORY_FACT,
        content=_fact_content(fact),
        source_id=fact.fact_id,
        source_type="sage_fact",
        timestamp=_ts_to_iso(fact.timestamp),
        provenance=provenance,
    )


def _to_input(
    obj: Any,
    *,
    agent_id: Optional[str] = None,
    narrative: Optional[str] = None,
) -> Optional[ElevationInput]:
    """按对象类型分发映射（adapter 内部分发器，供 run_elevation 使用）。

    依据对象是否带 ``event_id``（InnerLifeEvent）、``memory_id``（v1 Memory）、
    ``fact_id``（SAGE Fact）区分，避免在 adapter 里强 import 上游类型。

    🔴 SOUL-BUG-006：回傳型別改為 ``Optional``。**只有 InnerLifeEvent 這一條分支
    可能回傳 None**（無人話敘事 ⇒ fail-closed）。v1 Memory 與 SAGE Fact 的分支
    保持原樣：它們的 content 來自 `memory.content` / subject-predicate-object
    三元組，本身就是正文，不受本次 fail-closed 影響。
    """
    if hasattr(obj, "event_id") and hasattr(obj, "provenance"):
        return inner_life_event_to_input(obj, agent_id=agent_id, narrative=narrative)
    if hasattr(obj, "memory_id") and hasattr(obj, "content"):
        return v1_memory_to_input(obj, agent_id=agent_id)
    if hasattr(obj, "fact_id") and hasattr(obj, "subject"):
        return sage_fact_to_input(obj, agent_id=agent_id)
    raise TypeError(
        f"elevation adapter 无法识别对象类型：{type(obj).__name__!r}；"
        f"预期 InnerLifeEvent / v1 Memory / SAGE Fact"
    )


# ─────────────────────────────────────────────────────────────────────
# 自有 store 写入（data/elevation/，append-only，失败隔离）
# ─────────────────────────────────────────────────────────────────────


def _append_jsonl(path: Path, records: Sequence[dict]) -> bool:
    """append-only 追加 JSONL 行；写失败只告警 + 返回 False，绝不 raise。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except OSError as exc:  # 失败隔离：不阻断升华主路径
        logger.warning("elevation store write failed (%s): %s", path, exc)
        return False


def _dedup_content_hash(content: Any) -> str:
    """content 去重雜湊（VALUE-DEDUP-A-1 口徑：strip only）。

    normalize 口徑註明：僅 ``str.strip()``（去首尾空白），不做全半形 /
    大小寫折疊——repo 內無既有全半形正規化工具，故按工單 fallback
    （strip 即可）。非 str 輸入先 ``str()`` 再 strip。
    """
    text = content if isinstance(content, str) else str(content)
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _load_existing_dedup_index(store_dir: Path) -> Dict[tuple, Optional[str]]:
    """讀現有 NODES 落檔建 (agent_id, node_type, content_hash) -> canonical node_id 索引。

    SOUL-BUG-001：原版 ``_load_existing_dedup_keys`` 只回 key **集合**，呼叫端只知道
    「這個 key 已經有人了」，卻無從得知重複節點的證據邊該重綁到**哪一顆**既有節點，
    只好把邊整批丟棄 ⇒ 跨天獨立證據被銷毀，``min_evidence=2`` 結構性不可達。
    本函式改回 **key -> canonical_node_id 對照**，讓呼叫端能把邊重綁（rebind）到既有
    節點上，而不是丟棄（節點冪等本身 100% 保留，VALUE-DEDUP-A-1 不動）。

    canonical 選法（Bry 2026-10-14 拍板「不回溯清倉、只對未來寫入生效」）：
    同 key 多列時取**檔案順序第一列** —— 確定性、可重現；同一份資料重跑必定綁到
    同一顆節點。

    壞列（缺 node_id / 解析失敗）→ 記 ``None``，呼叫端據此走 fail-safe（退回丟邊）。

    fail-open：檔缺失回空 dict；讀檔異常（OSError 等）只 warning 並回空 dict，
    絕不 raise（呼叫方因此不跳過，合法產出不受損）。

    🔴 SOUL-BUG-010（Owner 2026-10-19 指示「雙軌讀取封堵」）：本函式原本自己
    ``open(NODES_FILENAME)`` 直讀節點檔，是全庫僅存的**第二條節點讀取路徑**
    ——即使它當下不消費 ``lifecycle_state``，它仍是一個「側門」：未來任何
    依賴節點狀態的邏輯若接在此路徑上，就會**繞過** lifecycle overlay 而靜默
    看到過期狀態。⇒ 收斂到 SSOT loader ``load_elevation_nodes()``，
    讓全庫**只有一條**節點讀取入口，側門在結構上消失。

    語意等價性已逐條核對（見守衛 test_dedup_index_uses_ssot_loader）：
      * 檔案順序 → loader 保序（append-only JSONL 逐行 append）        ✓
      * 壞 JSON 行跳過 → loader 同樣跳過                              ✓
      * 缺 node_id 的壞列記 ``None`` → loader 原樣回傳該 dict          ✓
      * 「檔案順序第一列勝出／None 可補位」→ 逐行迭代即可保留          ✓
      * 缺檔 / OSError → 空 dict（loader 回 ``[]``）                    ✓
      * overlay 只改 ``lifecycle_state``／``superseded_by``，
        不動 ``agent_id``／``node_type``／``content`` ⇒ 鍵值不變        ✓
    """
    index: Dict[tuple, Optional[str]] = {}
    # lazy import：本檔的既有慣例（見 elevate_matured_patterns 內的同名 import），
    # 避免與 emergent_projection 形成 import 循環。
    from .emergent_projection import load_elevation_nodes

    try:
        for rec in load_elevation_nodes(store_dir):
            try:
                key = (
                    rec.get("agent_id"),
                    rec.get("node_type"),
                    _dedup_content_hash(rec.get("content", "")),
                )
            except Exception:  # noqa: BLE001 — 單行容錯，不污染整批
                continue
            # 檔案順序第一列勝出（確定性 canonical）。已存在但 canonical 為 None
            # （壞列）而本列 node_id 健康 → 補位，讓重綁仍可進行。
            if index.get(key) is None:
                index[key] = rec.get("node_id")
    except Exception as exc:  # noqa: BLE001 — fail-open：任何異常都不跳過
        logger.warning("elevation dedup read failed (%s): %s", store_dir, exc)
        return {}
    return index


def _edge_identity(e):
    """邊的冪等鍵 = ``(node_id, source_id, inner_life_event_id)``。

    🔴 本鍵**必須與引擎的「已消化」消費鍵同構**，否則會寫出引擎根本不計票的重複邊。
    ``_rebuild_engine_for`` 用 ``(source_id, inner_life_event_id)`` 判定某份證據是否
    已被同維度的靈魂節點消化；soul_elevation 的 SE-1 契約則以
    ``evidence_key = (source_id, event_identity)`` 數獨立證據。加上 ``node_id`` 只為
    「同一份證據可同時支持不同命題」，不影響獨立性計數。

    語意結果（這是 SOUL-BUG-001 能兼容兩種需求的關鍵）：
      * **同一事件重跑** → 鍵相同 → 收斂 ⇒ 冪等（邊仍 1 條）
      * **不同事件同內容** → 鍵不同 → 合法累積 ⇒ 打通 ``min_evidence=2``

    ⚠️ 刻意**不含** ``source_type``：Bry 2026-10-14 裁定 —— 同一命題（相同
    content_hash）若同時由 ``sage_fact`` 與 ``inner_life_event`` 歸納出，應**合併**
    成一顆 canonical 節點並讓兩種來源的證據邊並存（跨模態雙重印證），而非拆成
    兩顆重複命題。節點承載命題、出處由邊承載。
    """
    return (e.get("node_id"), e.get("source_id"), e.get("inner_life_event_id"))


def _dedup_edges(store_dir, edge_records):
    """對「即將落盤」的證據邊做冪等去重（VALUE-DEDUP-A-1 的邊層補完）。

    已存在於磁碟、或本批之內重複的邊跳過；其餘照寫。

    fail-open：讀檔失敗回原樣（＝不去重），**絕不丟合法產出** —— 去重是保險絲，
    不是閘門；寧可多寫一條重複邊（引擎端 SE-1 仍會正確計 1），也不要丟證據。
    """
    if not edge_records:
        return edge_records
    seen = set()
    path = store_dir / EDGES_FILENAME
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        seen.add(_edge_identity(json.loads(line)))
                    except Exception:  # noqa: BLE001 — 單行容錯
                        continue
    except Exception as exc:  # noqa: BLE001 — fail-open：不去重
        logger.warning("elevation edge dedup read failed (%s): %s", path, exc)
        return list(edge_records)
    out = []
    for e in edge_records:
        key = _edge_identity(e)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _persist_result(
    store_dir: Path,
    nodes: Sequence[ElevationNode],
    edges: Sequence[Any],
) -> None:
    """把升華节点 + 证据边持久化到 data/elevation/（自有 store）。

    节点/证据边用 ``dataclasses.asdict`` 序列化为 JSONL，与 soul-elevation 的
    elevation_trace.jsonl（审计 sidecar）并列存放，使「节点 + 证据边」可回查。

    VALUE-DEDUP-A-1 冪等（**節點層，語意完全不變**）：同 agent 同 node_type 已有
    相同 content（normalize 後 sha256，口徑見 ``_dedup_content_hash``）的節點，
    落盤時跳過 append。節點數量因此不會膨脹 —— 這是 VALUE-DEDUP-A-1 的核心保證，
    保留。

    SOUL-BUG-001 修復（Bry 2026-10-14 裁示）：舊版命中重複 key 時，會把該節點
    ``skipped_node_ids`` 標記後**連同其全部證據邊一併丟棄** ⇒ 跨天的獨立證據被銷毀，
    ``min_evidence=2`` 結構性不可達。現改為**節點合併 + 邊重綁（edge rebinding）**：

      1. 重複節點仍跳過不寫入（節點冪等 100% 保留）；
      2. 但把它底下的證據邊的 ``node_id`` **重綁**到既有的 ``canonical_node_id``；
      3. 邊再依 ``_edge_identity`` 去重（見該函式 docstring 的同構理由）。

    ⇒ 同一批資料重跑仍冪等（節點 1 顆、邊 1 條）；跨天同內容的新事件則讓第 2 條
    證據邊掛到同一顆 canonical 節點上，使獨立證據自然累積達標。

    fail-safe：canonical 不可知（磁碟上有缺 ``node_id`` 的壞列）→ 該節點的邊**照舊
    丟棄**，維持 VALUE-DEDUP-A-1 原行為，絕不產生懸空邊。引擎語意不動（回傳值
    不變）；讀檔異常則 fail-open（不跳過，只 warning，絕不丟合法產出）。
    """
    node_records = [asdict(n) for n in nodes]
    edge_records = [asdict(e) for e in edges]
    if node_records:
        existing = _load_existing_dedup_index(store_dir)
        fresh_records: list = []
        rebind: dict = {}           # 被跳過的 node_id -> canonical node_id
        unrebindable: set = set()   # canonical 不可知 → 維持舊行為（丟邊）
        for rec in node_records:
            try:
                key = (
                    rec.get("agent_id"),
                    rec.get("node_type"),
                    _dedup_content_hash(rec.get("content", "")),
                )
            except Exception:  # noqa: BLE001 — 雜湊異常則 fail-open 照寫
                fresh_records.append(rec)
                continue
            if key in existing:
                canonical = existing[key]
                node_id = rec.get("node_id")
                if node_id and canonical == node_id:
                    pass  # 已是 canonical 本身，邊本來就指對，照留
                elif node_id and canonical:
                    rebind[node_id] = canonical  # 證據邊改綁到既有節點
                else:
                    unrebindable.add(node_id)     # canonical 不可知 → fail-safe 丟邊
                continue
            existing[key] = rec.get("node_id")
            fresh_records.append(rec)
        if rebind:
            for e in edge_records:
                canonical = rebind.get(e.get("node_id"))
                if canonical:
                    e["node_id"] = canonical
        if unrebindable:
            edge_records = [
                e for e in edge_records if e.get("node_id") not in unrebindable
            ]
        if fresh_records:
            _append_jsonl(store_dir / NODES_FILENAME, fresh_records)
    # 🔴 邊層冪等（SOUL-BUG-001）：刻意**移出** `if node_records:` 區塊 ——
    # 舊版只有「本批有節點」時才碰邊，無新節點時邊完全不去重。
    # 鍵與引擎消費鍵同構（見 _edge_identity），故「同事件重跑」收斂、
    # 「新事件同內容」合法累積。
    edge_records = _dedup_edges(store_dir, edge_records)
    if edge_records:
        _append_jsonl(store_dir / EDGES_FILENAME, edge_records)


# ─────────────────────────────────────────────────────────────────────
# 触发函数（InnerLifeEvent 写入之后调用）
# ─────────────────────────────────────────────────────────────────────


def run_elevation(
    inner_life_event: Any,
    memory_facts: Sequence[Any] = (),
    *,
    llm: Any = None,
    store_dir: Optional[Any] = None,
    agent_id: Optional[str] = None,
) -> List[ElevationNode]:
    """把 InnerLifeEvent + Memory 喂给 InternalizingEngine，产出升华节点。

    Args:
        inner_life_event: 已写入的 InnerLifeEvent（canonical 触发事件）。
        memory_facts: 同一叙事上下文的 v1 Memory / SAGE Fact 序列（正文来源）。
        llm: 可选 ElevationLLM 实现；缺省用 StubElevationLLM（确定性桩，不硬编码 provider）。
        store_dir: 可选 store 目录（缺省 ``data_root()/elevation``）。
        agent_id: 可选归属 agent（灵魂本体）覆盖。

    Returns:
        产出的 ``ElevationNode`` 列表（信念/价值/性格/内涵）。失败隔离：异常时
        记录 warning 并返回 []（不 raise，不阻断写路径）。
    """
    try:
        resolved_dir = _resolve_store_dir(store_dir)

        # EH-2.1 R1 垂直防火牆 defense-in-depth（契約 §4.3 Checkpoint ②, 雙層都擋）:
        # ① 事件層: 僅外部媒體情報（world:news* / world:feed* /
        #    world:celebrity_news）阻斷 —— 不得 consume、不得產 pattern 候選
        #    （Submission Gate 已擋, 這裡是 ElevationObserver / 直接呼叫路徑的
        #    第二道防線）。環境與日程事件（world:weather* / world:rain* /
        #    world:calendar* / world:user_going_outside*）放行 —— 共同生活的
        #    感知邊界, 可正常沉澱環境 Pattern。
        ev_prov = getattr(inner_life_event, "provenance", None)
        ev_trigger = str(getattr(ev_prov, "trigger_type", "") or "")
        if is_world_media_trigger(ev_trigger):
            logger.info(
                f"[elevation] EH-2 R1 BLOCKED (defense-in-depth): "
                f"外部媒體情報不入昇華 ({ev_trigger})"
            )
            return []

        # ② fact 層: memory_facts 中 origin == external_world 剔除（不進 inputs）。
        visible_facts = [
            m for m in memory_facts
            if _fact_origin(m) != _ORIGIN_EXTERNAL_WORLD
        ]

        # 1) 归一化输入：事件 + 全部可见记忆 fact。
        #
        # 🔴 SOUL-BUG-006（2026-10-17）：fail-closed —— `_to_input` 可能回傳 None
        # （僅 InnerLifeEvent 無人話敘事時）。這裡**過濾掉 None**，使該事件
        # 不會進入 `engine.consume()`，也就不會產生垃圾 pattern / trait 節點。
        #
        # ⚠️ 副作用（必須知情）：world 與 dream 事件目前 extras **完全沒有敘事**
        #    （實測：world 只有 world_novelty_id/source/type；dream 只有
        #     all_agents_count/target_agent_id），故它們會被整批 fail-closed。
        #    這是刻意的（純排程統計不該進靈魂圖譜），但代表 world pattern 的
        #    供給會停止 —— world 本來就永不昇華（P0 修正），影響僅限 pattern 層。
        _event_input = _to_input(
            inner_life_event,
            agent_id=agent_id,
            # SOUL-BUG-006 Step 4（Owner 授權的夢境 A-i 架構）：extras 沒敘事時，
            # 依 event_id 反查剛由 write_dream 落盤的夢境正文。查不到仍 fail-closed。
            narrative=_resolve_narrative(inner_life_event),
        )
        if _event_input is None:
            logger.info(
                "[elevation] fail-closed：事件無人話敘事（extras=%s），不建立節點 "
                "event_id=%s trigger=%s",
                sorted((getattr(inner_life_event.provenance, "extras", None) or {}).keys()),
                getattr(inner_life_event, "event_id", None),
                getattr(inner_life_event.provenance, "trigger_type", None),
            )
        inputs: List[ElevationInput] = (
            [_event_input] if _event_input is not None else []
        )
        inputs.extend(
            i for i in (_to_input(m, agent_id=agent_id) for m in visible_facts)
            if i is not None
        )

        # 2) 构造引擎：注入 LLM + soul-elevation 自有 trace writer（写到 data/elevation/）。
        #    EL-OWN-0 传递链：run_elevation(agent_id) → InternalizingEngine(agent_id)，
        #    使 engine.py 从 provenance 取不到 agent_id 时兜底 self._agent_id 也归属到
        #    具体灵魂。仅当 agent_id 为真时才传（world 事件 actor_id=None、不显式归属
        #    → 不传，保持引擎默认 "default"，system-level 语义不变）。
        _engine_kwargs: dict[str, Any] = {
            "llm": llm if llm is not None else StubElevationLLM(),
            "trace_writer": ElevationTraceWriter(str(resolved_dir / TRACE_FILENAME)),
        }
        if agent_id:
            _engine_kwargs["agent_id"] = agent_id
        engine = InternalizingEngine(**_engine_kwargs)

        # 3) 逐条 consume，累积节点。
        nodes: List[ElevationNode] = []
        for inp in inputs:
            nodes.extend(engine.consume(inp))

        # 4) 持久化节点 + 证据边到 data/elevation/（自有 store，失败隔离）。
        _persist_result(resolved_dir, nodes, engine.evidence_edges)

        return nodes
    except Exception as exc:  # noqa: BLE001 — 失败隔离：升华失败不阻断写路径
        logger.warning(
            "run_elevation failed (不影響 InnerLifeEvent 写入主路径): "
            "%s: %s",
            type(exc).__name__,
            exc,
        )
        return []


def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    """解析 store 目录：显式传入 > data_root()/elevation。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root

    return data_root() / ELEVATION_DIR_NAME


class ElevationObserver:
    """可接在写路径之后的旁路观察者（触发点接线）。

    用法（InnerLifeEvent 写入之后调用，不改 InnerLifeWriter 写入逻辑）：

        observer = ElevationObserver(...)
        event = writer.create_event(...)       # 写入
        observer.on_event_written(event, memory_facts)  # 写入之后 → 升华

    每次 ``on_event_written`` 内部调 ``run_elevation``（失败隔离，永不 raise）。
    """

    def __init__(
        self,
        *,
        llm: Any = None,
        store_dir: Optional[Any] = None,
        agent_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self._store_dir = store_dir
        self._agent_id = agent_id
        self.enabled = enabled

    def on_event_written(
        self, inner_life_event: Any, memory_facts: Sequence[Any] = ()
    ) -> List[ElevationNode]:
        """InnerLifeEvent 写入之后调用的升华入口（fire-and-forget，失败隔离）。"""
        if not self.enabled:
            return []
        try:
            return run_elevation(
                inner_life_event,
                memory_facts,
                llm=self._llm,
                store_dir=self._store_dir,
                agent_id=self._agent_id,
            )
        except Exception as exc:  # noqa: BLE001 — 双保险，绝不阻断调用方
            logger.warning("ElevationObserver.on_event_written failed: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────────────
# Elevate 调用机制（证据驱动，独立于 Submission Gate）
#
# 工单「打通 diary/dream → elevation」关键决策 #2：
#   - **Submission Gate 只 consume**（产 pattern 候选），**永不 elevate()**
#     （submission_gate.py 的 AST 红线保持不变）。elevate 是独立于 Gate 的
#     机制：本函数在 consume 落盘**之后**被调用（fire-and-forget，失败隔离）。
#   - **证据驱动**：读 ``data/elevation/`` 已持久化的 pattern + 证据边，按
#     agent（灵魂本体）重建 ``InternalizingEngine`` 注册表，对「独立证据累积
#     ≥ min_evidence」的候选维度组调 ``engine.elevate()``，pattern →
#     belief/value/trait/essence（升华维度由 prior 表 / LLM 后验候选决定，
#     即 soul-elevation 的 ``candidate_node_type``）。
#   - **不提前**：独立证据 < min_evidence 的候选维度组跳过（elevate 内部
#     ValueError insufficient 捕获，不硬触发）。
#   - **不重复消化**（anti-runaway）：已被 soul node 证据边引用的
#     (source_id, event_identity) 键不再计票——同一批证据不能支持第二颗
#     灵魂结构。
#     **消化范围局部化（ELEVATION-FIX-A-1／契约 §3 方案 A、OQ-1 裁定）**：
#     该「已消化」判定以 **（候选维度, agent）** 为范围——同一批证据在
#     **同一候选维度 + 同一 agent** 内仍不得重复计票（INV-2 不变），
#     但**不再跨候选维度吃掉别的维度的可见证据**（旧行为：agent 全域键
#     集合，任一维度消化即全局剔除）。这是**缩小**消化范围，不是取消去重。
#   - **agent 隔离**：只聚合同一 agent（灵魂本体）的 pattern 计票；
#     ``world`` 事件（agent_id="default"）自成一局，不影响具体灵魂计票。
# ─────────────────────────────────────────────────────────────────────


def _load_edges(store_dir: Path) -> List[dict]:
    """读 ``elevation_edges.jsonl``（append-only JSONL），返回原始 dict 列表。

    失败隔离：文件不存在 / 读失败 / 单行解析失败 → 记录并跳过该行，
    **绝不 raise**（不阻塞 elevate 主路径）。
    """
    path = store_dir / EDGES_FILENAME
    edges: List[dict] = []
    if not path.exists():
        return edges
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(
                        "elevation edges line skipped (bad json): %r", line[:80]
                    )
                    continue
                if isinstance(record, dict):
                    edges.append(record)
    except OSError as exc:
        logger.warning("elevation edges read failed (%s): %s", path, exc)
        return []
    return edges


def _node_candidate_dimension(node: dict) -> str:
    """节点的候选维度（候选维度分组的键，与 ``engine.elevate`` 的内部一致）。

    - ``pattern`` 节点：``candidate_node_type``（LLM 后验候选）。
    - soul 节点（belief/value/trait/essence）：``engine.elevate`` 的
      ``resolved_type = node_type or candidate``，故 soul 节点的
      ``candidate_node_type`` 常为 null → 退回 ``node_type``。
    - 两者皆缺（legacy 节点）→ 返回空字串（**不猜测**，由调用方保守处理）。
    """
    return node.get("candidate_node_type") or node.get("node_type") or ""


def _rebuild_engine_for(
    store_dir: Path,
    nodes: Sequence[dict],
    edges: Sequence[dict],
    *,
    agent_id: str,
    llm: Any,
) -> InternalizingEngine:
    """为该 agent 重建 ``InternalizingEngine`` 注册表（只加载该 agent 的数据）。

    - **节点**：该 agent 的全部节点都加载（含已被消化的 pattern），保证
      lineage 完整性（``check_invariants`` 要求 ``parent_node_id`` 在注册表内）。
    - **证据边**：只加载「未被**同候选维度**的 soul node 证据边覆盖的
      (source_id, event_identity) 键」的 active pattern 边——同一候选维度内，
      已被 elevate 消化的证据不再计票（同一批证据不能支持第二颗同维度灵魂
      结构，anti-runaway／INV-2）；**跨候选维度不互相吃证据**
      （ELEVATION-FIX-A-1／契约 §3 方案 A、OQ-1 裁定）。soul node 自身的边
      不加载（elevate 只聚合 pattern 证据，无需）。
    """
    engine = InternalizingEngine(
        llm=llm,
        agent_id=agent_id,
        trace_writer=ElevationTraceWriter(str(store_dir / TRACE_FILENAME)),
    )
    soul_node_ids = {
        n["node_id"] for n in nodes if n.get("node_type") in SOUL_NODE_TYPES
    }
    soul_node_dimensions = {
        n["node_id"]: _node_candidate_dimension(n)
        for n in nodes
        if n.get("node_type") in SOUL_NODE_TYPES
    }
    pattern_node_dimensions = {
        n["node_id"]: _node_candidate_dimension(n)
        for n in nodes
        if n.get("node_type") == "pattern"
    }
    # 已消化键，按 (候选维度, agent) 局部化：agent 已由本函式参数限定，
    # 故只需再按候选维度分桶。``consumed_any_dimension`` 保留 agent 全域
    # 视图，仅用于「pattern 维度不可判定」时的保守回退（绝不放宽去重）。
    consumed_by_dimension: dict = {}
    consumed_any_dimension: set = set()
    for e in edges:
        if e.get("agent_id") != agent_id:
            continue
        dimension = soul_node_dimensions.get(e.get("node_id"))
        if dimension is None:
            continue
        key = (e["source_id"], e.get("inner_life_event_id"))
        consumed_any_dimension.add(key)
        consumed_by_dimension.setdefault(dimension, set()).add(key)
    for n in nodes:
        if n.get("agent_id") != agent_id:
            continue
        engine._nodes[n["node_id"]] = ElevationNode(**n)
    loaded_by_dimension: dict = {}
    for e in edges:
        if e.get("agent_id") != agent_id:
            continue
        if e.get("node_id") in soul_node_ids:
            continue
        if e.get("valid_until_ts") is not None:  # superseded 留痕不计票
            continue
        pattern_dimension = pattern_node_dimensions.get(e.get("node_id"))
        if pattern_dimension is None:
            consumed = consumed_any_dimension  # 维度不可判定 → 保守沿用全域语义
        else:
            consumed = consumed_by_dimension.get(pattern_dimension, ())
        if (e.get("source_id"), e.get("inner_life_event_id")) in consumed:
            continue
        engine._edges.append(EvidenceEdge(**e))
        if pattern_dimension is not None:
            loaded_by_dimension[pattern_dimension] = (
                loaded_by_dimension.get(pattern_dimension, 0) + 1
            )
    logger.debug(
        "[elevate] evidence scope agent=%s consumed_by_dimension=%s "
        "loaded_by_dimension=%s",
        agent_id,
        {k: len(v) for k, v in sorted(consumed_by_dimension.items())},
        dict(sorted(loaded_by_dimension.items())),
    )
    return engine


# ─────────────────────────────────────────────────────────────────────
# 🔴 SOUL-BUG-009（Owner 2026-10-18）：昇華錨點代表選取（解除靈魂層去重凍結）
# ─────────────────────────────────────────────────────────────────────
#
# 缺陷原委：`engine.elevate(anchor)` 用 **anchor 的 content** 當靈魂節點內容
# （soul_elevation/engine.py `resolved_content = pattern.content`），而落盤層
# `_persist_result` 又以 `(agent_id, node_type, sha256(content.strip()))` 去重。
# 舊寫法恆取 `pids[0]`（候選維度組內**最舊**的 pattern）⇒ 每次升格產出的 content
# 與既有靈魂節點逐位元相同 ⇒ 被去重丟棄 ⇒ **靈魂層自 2026-10-11 起永久凍結**
# （實測：10 顆 value 靈魂節點全在 2026-10-11T23:18:19~20 同一批建立，其後每日
# 新增 29 顆 pattern，靈魂節點數 0 變化）。
#
# Owner 裁決（2026-10-18）：錨點**只當代表，不當守門**（職責分離）
# --------------------------------------------------------------------
# * **守門**歸組級證據機制全權負責：
#   - `engine.elevate(min_evidence=...)` 的維度計票；
#   - 本函式上方的「已消化證據隔離」（VALUE-DEDUP-A-1 / INV-2）—— 被任一靈魂
#     節點消耗過的證據鍵**根本不參與計票**，且按該維度**所有**靈魂節點扣除；
#   - EH-2 R1 垂直防火牆與 `agent_id="default"` 整組排除。
# * **錨點**的唯一職責是挑「最能代表這次昇華、且 content 還沒說過」的內容載體。
#
# 曾經走過的兩條死路（都留檔，避免後人重蹈）：
#
# 1. 「單顆 pattern 的 evidence degree ≥ 2 才有資格當錨點」（Owner 原提案）。
#    實測（dry-run 2026-10-18，`data/elevation` 唯讀）**每一顆真實 pattern 的
#    degree 都恰好是 1**（value 29 顆 × 10 agent 全 deg1；belief 四組 1/2/1/1 亦
#    全 deg1）—— pattern 按 content 去重合併，而 LLM 敘事幾乎不逐字重複。
#    照字面執行 ⇒ 16 個真實分組**全部**永久停擺（`default` 那組早已整組排除）。
#    ⚠️ 但這不代表該條件「結構性不可達」：`tests/test_soul_bug_watchdogs.py::
#    test_elevation_pattern_evidence_accumulation_must_reach_two` 釘的生產形狀
#    （兩條相同事件合併成 1 顆 degree=2 的 pattern）**正是** SOUL-BUG-001 的
#    「節點合併 + 邊重綁」機制所保證的 —— 只量當下資料會把「現在還沒發生」
#    誤判成「不可能發生」。
# 2. 「語意重複群集 ≥ 2 才有資格當錨點」。實測會**閹割分散證據的昇華能力**：
#    證據常分散在多顆 degree==1 的 pattern 上，硬要求單顆錨點自己有多重關聯度
#    等於逼系統放棄那些分散但總量已達標的證據鏈，直接撞上三條既有契約
#    （INV-2 / EH R2 / test_r2_lived_facts_still_elevate）。
#
# ⚠️ 曾實作過「距上一顆同類靈魂節點建立後，新鮮獨立證據 ≥ K 才動作」的成長閘門
#   並已拔除。理由（實測，非推測）：K=2 時它與引擎的已消化證據隔離**數值相同
#   ⇒ 永遠同步觸發**，把整個閘門關掉後全部測試依然全綠（MUT-02 NOT_RED 實證）。
#   既然無法被變異實驗擊穿，就是無人能稽核的盲腸邏輯 —— 留著只會給未來埋雷。
#   限流職責因此歸引擎單一事實來源。
#
# 淨效果：SOUL-BUG-009 只改「挑誰當代表」，不新增任何門檻、不動任何既有契約
# （唯一例外：`tests/test_elevation_evidence_scope_a1.py::test_e1` 末行斷言由
# `types.count("value") == 1` 翻轉為 `== 2`，因為原值釘的正是本缺陷本身）。
#

def _occupied_soul_contents(
    engine: Any, agent_id: str, node_type: str
) -> set:
    """同 (agent, 目標維度) 既有靈魂節點已佔用的 content（strip 後）。

    這是「去重凍結」的判定依據：``_persist_result`` 以
    ``(agent_id, node_type, sha256(content.strip()))`` 去重，所以 content 已
    被佔用 = 再產一顆同樣內容的靈魂節點只會被丟棄（=_persist_result 那條假性
    冪等路徑）。比對字串而非雜湊值即可，兩者等價且可讀。
    """
    return {
        (n.content or "").strip()
        for n in engine._nodes.values()
        if n.node_type == node_type
        and n.agent_id == agent_id
        and (n.content or "").strip()
    }


def _select_anchor_representative(
    patterns: Sequence[ElevationNode],
    occupied: set,
) -> Optional[ElevationNode]:
    """從候選池挑一個 **content 尚未被既有靈魂節點佔用** 的代表。

    🔴 Owner 裁決（2026-10-18）：**錨點只當代表，不當守門。**

    職責分離（Separation of Concerns）——這是此處唯一不引發連帶崩潰的架構：

    - **守門**歸組級證據機制所有：``engine.elevate(min_evidence=...)`` 的維度
      計票（≥ 獨立證據）、本模組的「已消化證據隔離」（VALUE-DEDUP-A-1 / INV-2，
      被靈魂節點消耗過的證據鍵根本不參與計票）、EH-2 R1 垂直防火牆與
      ``agent_id="default"`` 整組排除。
    - **錨點**的唯一職責是挑「最能代表這次昇華」的內容載體，決定新靈魂節點
      的文字與 ``parent_node_id``。它**不得**兼任准入把關。

    曾經把「錨點必須具備多重關聯度」做成硬門檻，那是錯的，實測會閹割分散
    證據的昇華能力：證據常常分散在**多顆 degree == 1** 的 pattern 上
    （生產實測：value 29 顆 × 10 agent 全部 degree=1），硬要求單顆錨點自己
    有多重關聯度，等於逼系統放棄那些**分散但總量已達標**的證據鏈。
    `tests/test_elevation_evidence_scope_a1.py::test_e1_repeated_elevation_needs_two_fresh_keys_in_same_dimension`
    與 `tests/test_epistemic_horizon.py::TestVerticalFirewall::test_r2_lived_facts_still_elevate`
    正是釘這個語意的既有契約。

    SOUL-BUG-009 的實際缺陷只有一個：恆取 ``pids[0]`` ⇒ 代表 content 恆等於
    既有靈魂節點 ⇒ 被去重丟棄 ⇒ **靈魂層永久凍結**（實測：10 顆 value 靈魂
    節點全在 2026-10-11T23:18:19~20 同一批建立，其後每日新增 29 顆 pattern，
    靈魂節點數 0 變化）。所以這裡只需保證「**挑一個還沒說過的話**」。

    排序（全部確定性，同輸入必得同輸出）：

    1. 先剔除 content 已被佔用的候選；
    2. 剩下的取 **最新鮮** 者（``created_ts`` 最大）—— 昇華是「此刻發生的
       沉澱」，代表句理應來自最新累積的證據，而不是被凍結的那句最舊的；
    3. 並列時取 ``node_id`` 最小者，保證唯一。

    Returns:
        有未被佔用的 content 時回該代表；**全部被佔用時回 ``None``** ——
        這就是冪等性靜默返回（不重複造節點），不是失敗。
    """
    fresh = [
        n for n in patterns if (n.content or "").strip() not in occupied
    ]
    if not fresh:
        return None
    newest = max(_ts_key(n.created_ts) for n in fresh)
    return sorted(
        (n for n in fresh if _ts_key(n.created_ts) == newest),
        key=lambda n: n.node_id,
    )[0]


def _ts_key(ts: Any) -> str:
    """節點時間戳的可比鍵；非字串（例如合成節點的 float）一律視為最舊。

    生產上全是固定寬度 UTC ISO 字串，字典序即時間序。
    ⚠️ 這裡刻意「取 max 再過濾」而不是用一個反向排序鍵塞給 ``sorted()[0]`` ——
    那種寫法容易把方向寫反（本次就寫反過一次，害得錨點又退回最舊那顆，
    等於讓去重凍結復活，而兩支測試都只看得出「內容不對」）。
    """
    return ts if isinstance(ts, str) else ""


def elevate_matured_patterns(
    *,
    store_dir: Optional[Any] = None,
    llm: Any = None,
    min_evidence: int = DEFAULT_ELEVATE_MIN_EVIDENCE,
) -> List[ElevationNode]:
    """证据驱动的 elevate（独立于 Submission Gate 的机制）。

    读 ``data/elevation/`` 已持久化的 pattern + 证据边，按 agent（灵魂本体）
    分组重建引擎注册表，对「独立证据累积 ≥ min_evidence」的候选维度组调
    ``engine.elevate()``：pattern → belief/value/trait/essence（升华维度由
    prior 表 / LLM 后验候选决定）。新灵魂节点 + 新证据边 append 回自有
    store（``elevation_nodes.jsonl`` / ``elevation_edges.jsonl``）。

    Args:
        store_dir: 可选 store 目录（缺省 ``data_root()/elevation``）。
        llm: 可选 ElevationLLM 实现（缺省用 StubElevationLLM，确定性桩；
            elevate 本身不调 LLM，仅在重建注册表时透传给引擎构造）。
        min_evidence: 独立证据阈值（对齐 soul-elevation 默认 2 独立证据）。
        （限流刻意**不在此参数化**：引擎的「已消化证据隔离」＋ ``min_evidence``
        已更严格地负责，详见 SOUL-BUG-009 说明与上方注释。）

    Returns:
        本次新产出的灵魂节点（belief/value/trait/essence）列表。失败隔离：
        异常时记录 warning 并返回 []（不 raise，不阻断 consume 主路径）。
    """
    if not isinstance(min_evidence, int) or min_evidence < 1:
        raise ValueError(f"min_evidence must be a positive int, got {min_evidence!r}")

    resolved_dir = _resolve_store_dir(store_dir)
    try:
        from .emergent_projection import DEFAULT_AGENT_ID, load_elevation_nodes

        nodes = load_elevation_nodes(resolved_dir)
        edges = _load_edges(resolved_dir)
        if not nodes:
            return []
        agent_ids = sorted({n.get("agent_id", "default") for n in nodes})

        # 🔴 P0 修正（2026-10-03，Owner 裁定「沉澱成信念需要自己的經歷，
        # 光看完就沉澱是不合理的」）：world node（agent_id="default"）**永不昇華**。
        #
        # 缺陷原委：`elevate_matured_patterns()` 是**独立于 Submission Gate** 的机制
        # （run_server.py `_elevate_check()`，EL-DD-2）。EH-2.1 R1 垂直防火牆只擋
        # `consume()`（submission_gate.py:399），**沒有覆蓋本路徑** —— 故 world 的
        # pattern 仍被 `engine.elevate()` 升成 belief/value/trait。
        #
        # 實測危害（2026-10-03）：`data/elevation/elevation_nodes.jsonl` 累積
        # 4,125 節點，其中 agent_id="default" 佔 2,804，其中 910 條 belief 的內容
        # 是**新聞標題原文**（如 "Trump hails 'historic' deal for US to control
        # 65bn barrels of Venezuela's oil"），自 2026-08-29 起每日穩定產出
        # 36–45 筆，**直接違反 D2 裁定**。
        #
        # 修法語意：world node 為系統級標記，`emergent_projection.py:58` 已明文
        # 「永不投影」——昇華它本來就是白做，且會把「只是看過」寫成「我成為」。
        # 故此處整組跳過，與 read 側的不變式對齊。
        _default_agent_id = DEFAULT_AGENT_ID
        _skipped_world = [a for a in agent_ids if a == _default_agent_id]
        if _skipped_world:
            agent_ids = [a for a in agent_ids if a != _default_agent_id]
            logger.info(
                f"[elevate] 跳过 world node group agent={_default_agent_id!r} "
                f"({len(_skipped_world)} 组) —— world 事件不得沉澱成靈魂信念 "
                f"(EH-2.1 R1 對稱條款; read 側 emergent_projection 亦永不投影)"
            )
        if not agent_ids:
            return []

        elevated: List[ElevationNode] = []
        for agent_id in agent_ids:
            engine = _rebuild_engine_for(
                resolved_dir,
                nodes,
                edges,
                agent_id=agent_id,
                llm=llm if llm is not None else StubElevationLLM(),
            )
            # 候选维度分组（保持创建顺序），每组取第一个 pattern 为升华锚点
            # （elevate 内部聚合同候选维度的全部 pattern 有效证据边）。
            # EH-2 R3（垂直防火牆, D2 裁定 / 契約 §4.3）: assimilated 知識封頂
            # Fact/Pattern/Meaning —— 嚴禁穿透 Belief/Value/Trait/Essence。
            # 候選審查: 依 evidence 邊的 sage_fact source_id → SAGE origin 判定,
            # 「由 assimilated fact 累積出的 pattern」直接剔出候選 group（不當錨點）。
            fact_origins = _load_fact_origins(agent_id)
            eh2_blocked_patterns: set[str] = set()
            if fact_origins:
                assimilated_source_ids = {
                    e.source_id
                    for e in engine._edges
                    if str(e.source_type) == "sage_fact"
                    and fact_origins.get(e.source_id) == _ORIGIN_ASSIMILATED
                }
                if assimilated_source_ids:
                    for nid, node in engine._nodes.items():
                        if node.node_type != "pattern":
                            continue
                        pattern_sources = {
                            e.source_id
                            for e in engine._edges if e.node_id == nid
                        }
                        if pattern_sources & assimilated_source_ids:
                            eh2_blocked_patterns.add(nid)
                            logger.info(
                                f"[elevate] EH-2 R3 SKIP pattern={nid[:8]}... "
                                f"(assimilated 封頂, 禁入 Belief/Value/Trait/Essence)"
                            )
            candidates: dict[str, list[str]] = {}
            # ── SOUL-BUG-010 防線二（Owner 2026-10-19）：昇華層排除休眠／終態 ──
            # `engine.elevate()` 按「候選維度」聚合**全部** pattern 的有效證據邊，
            # 因此一個 pattern 只要還在 `candidates` 裡，它的證據就會被拿去替別的
            # 維度湊數。`dormant`（30 天無新支持）／`superseded` 的 pattern 若照樣
            # 計票，等於「陳年廢棄」仍在為新靈魂背書——這就是分級消費要擋的東西。
            #
            # 🔴 語意邊界（不可誤讀為「年紀大就沒資格」）：只排除
            # **dormant / superseded**，**weakening 保留完整投票權**。
            # `weakening` = 7 天無新支持，人類心智中「週一一次、下週二又一次
            # （間隔 8 天）」是**標準的長期跨週習慣**；若連它一起排除，系統就只剩
            # 「一週內密集成癮的短暫衝動」能升格，真正細水長流的習慣永遠升不了格，
            # 直接推翻 soul-elevation `prior.py:62`「old ≠ outdated——年齡不是退場
            # 理由，失去支持才是」的立論。
            #
            # 狀態來源：`load_elevation_nodes` 的 lifecycle overlay（sidecar）。
            # sidecar 缺文件時全數缺 `lifecycle_state` → 缺省 active → **本段
            # 逐位元 no-op**，行為與本票改動前完全相同。
            quarantined_patterns: list[str] = []
            for nid, node in engine._nodes.items():
                if node.node_type != "pattern":
                    continue
                if nid in eh2_blocked_patterns:
                    continue
                if node.lifecycle_state in TERMINAL_OR_DORMANT_LIFECYCLE_STATES:
                    quarantined_patterns.append(nid)
                    continue
                candidates.setdefault(node.candidate_node_type, []).append(nid)
            if quarantined_patterns:
                logger.info(
                    "[elevate] SOUL-BUG-010 防線二：%d 顆 %s pattern 不再參與昇華計票 "
                    "(agent=%s)",
                    len(quarantined_patterns),
                    "/".join(sorted(TERMINAL_OR_DORMANT_LIFECYCLE_STATES)),
                    agent_id,
                )

            baseline_edges = len(engine._edges)
            agent_elevated: List[ElevationNode] = []
            for cand, pids in candidates.items():
                if not pids:
                    continue
                # EH-2 R3 計票層隔離: engine.elevate 按「候選維度」聚合全部 pattern 的
                # 有效證據邊 — 被 R3 封頂的 pattern 即使不當錨點, 其證據邊仍會參與
                # 同維度計票。故在 elevate 前先把被封頂 pattern 的有效證據邊暫時
                # 失效 (valid_until_ts 置標), 使其 0 參與計票 / 0 被複製到靈魂節點;
                # 證據不足 (ValueError, 無副作用) 或異常時還原, 不永久丟失。
                _EH2_SENTINEL = "_eh2_r3_blocked"
                neutralized: List[tuple] = []
                for _i, _e in enumerate(engine._edges):
                    if (
                        _e.node_id in eh2_blocked_patterns
                        and _e.valid_until_ts is None
                    ):
                        # 🔴 SOUL-BUG-010：`EvidenceEdge` 是 **frozen dataclass**
                        # （soul-elevation `models.py:159`）⇒ 舊碼的
                        # `e.valid_until_ts = _EH2_SENTINEL` **必然拋
                        # FrozenInstanceError**，整段 R3 隔離從未真正執行過。
                        # 而該例外會被最外層 except 接住 → `elevate_matured_patterns`
                        # 整趟回傳 []，等於**整條靈魂生長線靜默死掉**（正是本函式
                        # 下方 :1281-1286 註解描述的失效型態）。
                        # 生產實測（2026-10-19）：10 隻 agent 的 SAGE fact 中
                        # `origin="assimilated"` 共 **0** 筆 ⇒ 該分支從未被觸發，
                        # 地雷尚未引爆；一旦有 assimilated fact 就會炸。
                        # 正解：frozen dataclass 只能用 `replace()` 產生新物件再換位
                        # （與引擎自身 `_supersede_edges` 同一手法，`engine.py:803`）。
                        _replacement = replace(_e, valid_until_ts=_EH2_SENTINEL)
                        engine._edges[_i] = _replacement
                        neutralized.append((_i, _e, _replacement))
                # ── SOUL-BUG-009（Owner 2026-10-18）：代表性锚点取代 pids[0] 死锚点 ──
                # 锚点的 content 直接成为灵魂节点内容（engine.py:516）。恒取最旧的
                # `pids[0]` ⇒ 内容与既有灵魂节点逐位元相同 → 被 _persist_result
                # 去重丢弃 → **灵魂层永久冻结**（实测：10 颗 value 灵魂节点全在
                # 2026-10-11T23:18:19~20 同一批建立，其后每日新增 29 颗 pattern，
                # 灵魂节点数 0 变化）。
                #
                # ⚠️ 这里**只挑代表，不设闸门**（Owner 裁決：職責分離）。守门归
                # 引擎全权负责，且已有更严格的实作：
                #   * `engine.elevate(min_evidence=...)` 的维度计票；
                #   * 本函数上方的「已消化证据隔离」（VALUE-DEDUP-A-1 / INV-2）
                #     —— 被任一灵魂节点消耗过的证据键**根本不参与计票**，而且
                #     是按**该维度所有**灵魂节点扣除，比「只扣最后一颗」更严格。
                #     因此在本函数另加一个 K=2 的「新鲜键闸门」是**结构性冗余**：
                #     两者数值相同时永远同步触发，闸门不可能先于引擎挡下任何东西。
                #     实测（把该闸门整个关掉，全部测试依然全绿）证实了这一点。
                #     Owner 裁決：拔除冗余，不留无法被变异实验击穿的盲肠逻辑。
                #
                # 🔴 SOUL-BUG-010（Owner 2026-10-19）：`try/finally` 覆蓋範圍前推至
                # **锚点选取本身**，且**成功路径也必须还原**。原碼兩處缺陷：
                #   ① `_select_anchor_representative(...)` 在 `try` 之外，其內部
                #      `[engine._nodes[pid] for pid in pids]` 可拋 KeyError ⇒ 已中和的
                #      邊永遠留在 sentinel 狀態（上方 `:1210` 註解「不永久丟失」為假）。
                #   ② 成功路徑（`agent_elevated.append(soul)` 之後）**從不還原**。
                #      而 `engine` 是 per-agent_id 重建、**跨候選維度重用**
                #      （`:1157` 建一次，`:1203` 迴圈所有維度共用）⇒ 維度 A 成功昇華
                #      會讓維度 B/C 的計票少算那些邊。累積且靜默。
                #   實測規模前提：`engine` 每次呼叫重建、無模組層單例 ⇒ 不會跨呼叫
                #      永久腐化，僅同趟呼叫內累積。
                #
                # ⚠️ 附帶的行為變更（刻意）：`_select_anchor_representative` 拋出的
                # 異常現在落在**維度級** except（記 warning 後 continue 到下一維度），
                # 而非直接冒泡到最外層 except 使整趟 `elevate_matured_patterns`
                # 回傳 []。這與本模組既有「逐維度失敗隔離、不阻斷 consume 主路徑」
                # 的契約一致，且比「一個維度的壞節點讓整趟昇華歸零」更正確。
                try:
                    _anchor = _select_anchor_representative(
                        [engine._nodes[pid] for pid in pids],
                        _occupied_soul_contents(engine, agent_id, cand),
                    )
                    if _anchor is None:
                        logger.info(
                            f"[elevate] skip agent={agent_id} candidate={cand}: "
                            f"all {len(pids)} candidate contents already occupied by "
                            f"existing {cand} soul nodes (idempotent no-op)"
                        )
                        continue
                    soul = engine.elevate(
                        _anchor.node_id, min_evidence=min_evidence
                    )
                    agent_elevated.append(soul)
                    elevated.append(soul)
                    logger.info(
                        f"[elevate] ✓ agent={agent_id} candidate={cand} "
                        f"patterns={len(pids)} anchor={_anchor.node_id[:8]}… "
                        f"→ {soul.node_type} "
                        f"(min_evidence={min_evidence})"
                    )
                except ValueError as exc:
                    # 独立证据不足 → 不升（不提前）。这是常态（多数组未达阈值）。
                    logger.info(
                        f"[elevate] skip agent={agent_id} candidate={cand}: {exc}"
                    )
                    continue
                except Exception as exc:  # noqa: BLE001 — 失败隔离
                    logger.warning(
                        f"[elevate] failed agent={agent_id} candidate={cand}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                finally:
                    # 唯一還原點：早退（continue）、證據不足、異常、成功四種路徑
                    # 全部經過這裡，確保「暫時失效」真的是暫時的。
                    #
                    # 還原用「位置 + 身分」雙重比對：只把我們自己放進去的那個
                    # 物件換回原物。若該槽位已被引擎自己改寫（例如
                    # `_supersede_edges` 的留痕標記），就不動它——避免還原動作
                    # 反過來抹掉引擎的合法寫入。
                    for _i, _original, _replacement in neutralized:
                        if (
                            _i < len(engine._edges)
                            and engine._edges[_i] is _replacement
                        ):
                            engine._edges[_i] = _original
            # 只 append 本次新增的边（加载的历史边不重复写，supersede 原地改不动文件）。
            _persist_result(
                resolved_dir, agent_elevated, engine.evidence_edges[baseline_edges:]
            )
        return elevated
    except Exception as exc:  # noqa: BLE001 — 失败隔离：不影响 consume 主路径
        # 🔴 帶 traceback。這裡刻意「吞掉一切並回傳 []」是既有契約（不阻斷
        # consume 主路徑），但代價是**程式層級錯誤會被靜默降級成靈魂層停止生長**。
        # 實測踩過：刪掉一個區塊後留下 `NameError: name '_fresh' is not defined`
        # 於 log 行，16 支昇華相關測試全紅、整條靈魂生長線靜默回傳 []，
        # 唯一線索是一行 warning 裡的 NameError 字樣。沒有 traceback 時，
        # 「一整條生命線無聲死掉」與「某個外部 IO 失敗一次」看起來一模一樣。
        logger.warning(
            "elevate_matured_patterns failed (不影響主路徑): %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return []


# ─────────────────────────────────────────────────────────────────────
# SOUL-BUG-010-B：生命週期自動老化（evaluate_lifecycle_all）
# ─────────────────────────────────────────────────────────────────────


def _parse_ts(ts: Any) -> Optional[datetime]:
    """ISO 8601 → datetime；壞值回 None（不 raise）。"""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _support_anchor_by_node(edges: Sequence[dict]) -> Dict[str, str]:
    """每個節點「仍有效支持邊」的最大 ``valid_from_ts``。

    🔴 這是 SOUL-BUG-010-B 的核心：**衰減錨點必須來自證據邊，不能來自節點欄位**。

    缺陷原委（實測 2026-10-19，生產 398 節點 / 903 邊）：
      * ``last_support_ts`` **全庫沒有任何寫入者**（grep 只命中 docstring／docs／
        一支「temporal_phenomenology 不得賦值它」的測試），生產 **398/398 節點的
        ``last_support_ts == created_ts``** ⇒ 該欄位建檔後從未更新。
      * 而 ``_rebuild_engine_for`` **不載入靈魂節點自己的邊**（本檔 :898）
        ⇒ value 節點在引擎裡沒有 active edge，錨點只能落到 ``last_support_ts`` = 建立時間。
    後果（實測）：直接呼叫 ``engine.evaluate_lifecycle()`` 會把 **10 顆 value 節點**
    判成 weakening，而它們**仍在被持續重新支持**——value ``6a92c906b245`` 有 81 條
    仍有效邊橫跨 4 天（10-11 / 10-15 / 10-16 / 10-17），最新支持距今 2.3 天。
    而 ``weakening`` 投影時會加前綴「我隱約覺得」⇒ 每隻 agent 2~3 顆 value 中的
    1 顆會被**無理由地軟化**，直接違反 ``prior.py:62``
    「old ≠ outdated——年齡不是退場理由，**失去支持才是**」。

    正解：由本函式自**原始邊檔**算出真實錨點，於評估前注入引擎（見
    :func:`evaluate_lifecycle_all`）。節點檔本身**不動**（Append-Only 紅線）。
    """
    best: Dict[str, str] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        if e.get("valid_until_ts") is not None:
            continue  # 已被取代的邊不算「仍有效支持」
        nid = e.get("node_id")
        ts = e.get("valid_from_ts")
        if not isinstance(nid, str) or not nid or not isinstance(ts, str):
            continue
        cur = best.get(nid)
        if cur is None:
            best[nid] = ts
            continue
        cur_dt, new_dt = _parse_ts(cur), _parse_ts(ts)
        if cur_dt is None or (new_dt is not None and new_dt > cur_dt):
            best[nid] = ts
    return best


def _inject_support_anchor(engine: Any, anchor_by_node: Dict[str, str]) -> int:
    """把真實錨點注入引擎內的 ``ElevationNode.last_support_ts``（僅記憶體）。

    🔴 ``ElevationNode`` 是 **frozen dataclass** ⇒ 不可用屬性賦值，必須
    ``dataclasses.replace()`` 產生新物件再換回 ``engine._nodes``
    （與 SOUL-BUG-010 修掉的 R3 同款地雷）。

    節點若**沒有**任何仍有效支持邊 ⇒ 注入 ``None``，讓引擎走優先序 3
    （``created_ts + grace``），這才是教義要求的兜底；若沿用節點檔裡那個
    停滯在 ``created_ts`` 的 legacy 值，會把「從未被支持過」誤判成
    「失去支持恰好 7 天」。

    Returns:
        實際被更換的節點數。
    """
    changed = 0
    for nid, node in list(engine._nodes.items()):
        anchor = anchor_by_node.get(nid)
        if node.last_support_ts == anchor:
            continue
        engine._nodes[nid] = replace(node, last_support_ts=anchor)
        changed += 1
    return changed


def evaluate_lifecycle_all(
    *,
    store_dir: Optional[Any] = None,
    now_ts: Optional[str] = None,
) -> List[dict]:
    """生命週期自動老化：評估全部節點並把轉變 append 進 sidecar。

    流程：
      1. 載入節點（**經 ``load_elevation_nodes``**，即已套 lifecycle overlay）
         與全部證據邊；
      2. 由**原始邊檔**算出每個節點的真實衰減錨點（仍有效支持邊的最大
         ``valid_from_ts``），**在餵入引擎前**注入 ``last_support_ts``；
      3. 逐 agent 重建引擎並呼叫 ``engine.evaluate_lifecycle()``
         （狀態機全權歸引擎：逐級轉移／essence 豁免／superseded 終態）；
      4. 把轉變以事件 dict append 進 ``elevation_lifecycle_events.jsonl``
         （既有 append-only sidecar，SOUL-BUG-010 已建好且讀取面已上線）；
      5. 回傳事件清單。

    Append-Only 紅線：**不寫** ``elevation_nodes.jsonl``、不寫任何節點欄位。
    錨點只存在於記憶體中的 ``ElevationNode`` 物件。

    失敗隔離：整體包 ``try/except Exception`` → warning（帶 traceback）
    並回傳 ``[]``，**絕不讓生命週期評估中斷任何既有主路徑**。
    """
    resolved_dir = _resolve_store_dir(store_dir)
    try:
        from .emergent_projection import load_elevation_nodes
        from .lifecycle_overlay import LIFECYCLE_EVENTS_FILENAME

        nodes = load_elevation_nodes(resolved_dir)
        edges = _load_edges(resolved_dir)
        if not nodes:
            return []

        anchor_by_node = _support_anchor_by_node(edges)
        agent_ids = sorted({n.get("agent_id", "default") for n in nodes})
        now_iso = now_ts or datetime.now(timezone.utc).isoformat()

        events: List[dict] = []
        for agent_id in agent_ids:
            engine = _rebuild_engine_for(
                resolved_dir,
                nodes,
                edges,
                agent_id=agent_id,
                llm=None,
            )
            injected = _inject_support_anchor(engine, anchor_by_node)
            before = {n.node_id: n.lifecycle_state for n in engine._nodes.values()}
            changed = engine.evaluate_lifecycle(now_ts=now_iso)
            for node in changed:
                anchor = None
                # 盡量記錄引擎實際採用的錨點（引擎私有方法，缺失時退回自算值）
                anchor_fn = getattr(engine, "_lifecycle_anchor", None)
                if callable(anchor_fn):
                    try:
                        anchor = anchor_fn(node, now_iso)
                    except Exception:  # noqa: BLE001 — 錨點取不到不影響轉變記錄
                        anchor = None
                if anchor is None:
                    anchor = anchor_by_node.get(node.node_id)
                events.append({
                    "ts": now_iso,
                    "node_id": node.node_id,
                    "event": "lifecycle_transition",
                    # 🔴 `lifecycle_state` 是 overlay **實際讀取**的欄位
                    # （`lifecycle_overlay._reduce_events` 只認這個 key；
                    #  少它則事件會被整個忽略，狀態永遠不生效）。
                    #  before/after 保留作審計用。
                    "lifecycle_state": node.lifecycle_state,
                    "lifecycle_state_before": before.get(node.node_id, "active"),
                    "lifecycle_state_after": node.lifecycle_state,
                    "anchor_ts": anchor,
                    "reason": "no_new_support_evidence",
                    "agent_id": agent_id,
                    "node_type": node.node_type,
                })
            logger.info(
                "[lifecycle] agent=%s injected_anchors=%d transitions=%d",
                agent_id, injected, len(changed),
            )

        if events:
            # `_append_jsonl` 自帶失敗隔離（OSError → warning → False），
            # 故這裡**主動檢查回傳值**：寫入失敗時明確記 warning，
            # 否則「評估成功但事件沒落地」會完全靜默。
            if not _append_jsonl(resolved_dir / LIFECYCLE_EVENTS_FILENAME, events):
                logger.warning(
                    "[lifecycle] %d 筆轉變寫入 %s 失敗（事件未落地）",
                    len(events), LIFECYCLE_EVENTS_FILENAME,
                )
            else:
                logger.info(
                    "[lifecycle] %d 筆轉變已 append 進 %s",
                    len(events), LIFECYCLE_EVENTS_FILENAME,
                )
        return events
    except Exception as exc:  # noqa: BLE001 — 失败隔离：不阻断任何主路径
        logger.warning(
            "evaluate_lifecycle_all failed (不影響主路徑): %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return []


__all__ = [
    "DEFAULT_ELEVATE_MIN_EVIDENCE",
    "ELEVATION_DIR_NAME",
    "ElevationInput",
    "ElevationNode",
    "ElevationObserver",
    "elevate_matured_patterns",
    "evaluate_lifecycle_all",
    "inner_life_event_to_input",
    "v1_memory_to_input",
    "sage_fact_to_input",
    "run_elevation",
]
