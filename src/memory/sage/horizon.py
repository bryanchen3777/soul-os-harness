"""
src/memory/sage/horizon.py — Epistemic Horizon 讀側支援模組（EH-2）

對應契約: docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md（EH-1.1 修訂版）
  - §2 Horizon Gate 讀側投影的資料來源（Idiolect 檢索）
  - §3.2 雙維度 Schema 常數（origin x horizon_state）
  - §3.5 Idiolect 提取（assimilated + aware → 已理解的默契事物清單）
  - D3 靈魂原生分流（modern_earth 現代原生角色 → Gate fail-silent bypass）

本模組是純讀側 / 常數層：
  - 不持久化任何狀態（0 新狀態, 契約 §2.2「0 新狀態」）。
  - 不觸碰 SAGE 寫入主幹（frozen, 契約 §8.1）— 標記走 graph_store.set_fact_dimensions。
  - 所有讀取 fail-silent：SAGE 不存在 / 查詢失敗 → 空結果, 不 raise。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # 僅型別標注, 避免拉入 networkx 等重依賴鏈
    from .models import Fact

logger = logging.getLogger("soul_os.sage.horizon")

# ── 維度一: origin（資料源頭五類, 契約 §3.2）─────────────────────────

ORIGIN_NATIVE_COMMONS = "native_commons"      # 原生文明生活常識（固有常識, 不走三態）
ORIGIN_NATIVE_EPISODE = "native_episode"      # 原生世界歷史記憶（Seeded Memory, 不走三態）
ORIGIN_LIVED_EXPERIENCE = "lived_experience"  # 與主人的直接互動（已知, 不走三態)
ORIGIN_ASSIMILATED = "assimilated"            # 經解釋後內化的現代常識（走三態）
ORIGIN_EXTERNAL_WORLD = "external_world"      # 外部環境情報 / 真實世界事件（走三態）

# ── 維度二: horizon_state（認知三態, 僅 assimilated / external_world）──

HORIZON_UNKNOWN = "unknown"    # Gate 前 / 未觸發：不解讀、不引用、不強化
HORIZON_LEARNING = "learning"  # Gate 已觸發：首次遭遇, 阻力中
HORIZON_AWARE = "aware"        # Gate 放行：已內化, 唯一出口 = 顯式放行事件

# ── D3 靈魂原生分流（Owner 裁定, 契約 §2.3/§6.1）────────────────────
# 現代原生（native_commons == modern_earth）角色: Horizon Gate 直接返回 ""。
# Pilot 範圍僅黑川茜（agent_akane）+ 櫻島麻衣（agent_mai）;其餘角色預設非現代原生。
MODERN_NATIVE_AGENTS = frozenset({"agent_akane", "agent_mai"})


def is_modern_native(agent_id: str) -> bool:
    """文明基底分流判定（D3）: 現代原生角色 → True → Gate fail-silent bypass。"""
    return agent_id in MODERN_NATIVE_AGENTS


def retrieve_idiolect(agent_id: str, data_dir: str | None = None) -> List["Fact"]:
    """檢索該角色已內化的 Idiolect 條目（契約 §3.5）。

    - 來源: SAGE facts 中 ``origin == assimilated`` AND ``horizon_state == aware``。
    - 位置: ``{data_dir}/{agent_id}/graph.sqlite``（data_dir 缺省 data_root()/memory,
      對齊 MemoryMiddleware 佈局）。
    - 純讀（sqlite uri mode=ro, 0 寫、0 migration 副作用）;fail-silent:
      檔案不存在 / 無欄位（v8 舊庫）/ 任何異常 → 回空 list, 不 raise。
    """
    if not agent_id:
        return []
    try:
        if data_dir is None:
            from src.paths import data_root

            data_dir = str(data_root() / "memory")
        db_path = Path(data_dir) / agent_id / "graph.sqlite"
        if not db_path.exists():
            return []
        # 不能用 URI mode=ro: SAGE 走 WAL 模式 — sqlite 的唯讀連線無法讀 WAL
        # (SQLITE_READONLY_CANTLOCK), 會靜默失敗。一般連線配 SELECT-only
        # 即為讀側 (0 資料寫入; 僅可能產生 transient -shm)。
        conn = sqlite3.connect(str(db_path))
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM facts
                   WHERE origin = 'assimilated'
                     AND horizon_state = 'aware'
                     AND invalidated_at IS NULL
                   ORDER BY weight DESC, timestamp DESC""",
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return []
        from .models import Fact  # 僅在真正需要時才引入（輕量）

        # 注意: facts 表有 tags 欄位但 Fact dataclass 沒有 (graph_store._row_to_fact
        # 也是先 pop), 直接 Fact(**dict(row)) 會 TypeError → 必須 pop 後再轉。
        facts: List["Fact"] = []
        for r in rows:
            d = dict(r)
            d.pop("tags", None)
            facts.append(Fact.from_dict(d))
        return facts
    except Exception as exc:  # noqa: BLE001 — fail-silent: Idiolect 缺失不影響讀側
        logger.debug(f"[horizon] retrieve_idiolect 失敗: {type(exc).__name__}: {exc}")
        return []


__all__ = [
    "HORIZON_AWARE",
    "HORIZON_LEARNING",
    "HORIZON_UNKNOWN",
    "MODERN_NATIVE_AGENTS",
    "ORIGIN_ASSIMILATED",
    "ORIGIN_EXTERNAL_WORLD",
    "ORIGIN_LIVED_EXPERIENCE",
    "ORIGIN_NATIVE_COMMONS",
    "ORIGIN_NATIVE_EPISODE",
    "is_modern_native",
    "retrieve_idiolect",
]