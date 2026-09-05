"""
src/soul/relationships.py — Soul OS Stage 4.1

角色靜態關係圖 (Static Relationships Graph)

設計動機 (Bry 拍板 2026-07-18 18:24+):
- 角色活起來 + 虛擬生活, 跟虛構真人一致
- 不是資料庫欄位思維: 真人對他人認知是模糊有偏見、不完整、不對稱
- Bry 是被打斷的觸發之一, 不是主題: Bry 作為 special entity 出現在 others
- 自己玩為主找 Bry 為輔: 角色之間也互相關係, 群組事後觀察
- 靜態關係要故意不完整不對稱: 認知 = impression (短日文片語) + feeling (粗略分類) + confidence (0.0-1.0)

初始 bias (Perplexity 拍板 + Bry 接受):
- 陌生人 / 沒接觸過: confidence = 0.3
- 已知身份但無互動: confidence = 0.5
- 同源/姐妹 (Rem+Ram): confidence = 0.7 (初始 bias, 不是寫死群組)

Confidence 衰減函數 (Bry 拍板 2026-07-18):
- 正向互動: +0.05 ~ +0.15 (依強度)
- 衝突/被忽略: -0.10 ~ -0.20
- 自然衰減: -0.02 / 天 (沒互動就疏遠)
- 上限 1.0 / 下限 0.0

Stage 4.1 第一刀範圍 (最小可驗收):
- 讀寫 data/soul/{agent_id}/relationships.json
- 自動 decay (每次 touch 時算 days_since_last_decay)
- USER_MESSAGE 觸發 confidence += 0.05 (Bry 來了)
- AGENT_SPEAK 觸發 interaction_count++ + 最後互動時間
- 還不做 LLM 抽 impression (那是 Stage 4.3 動態互動的範圍)
- 還不做 4.1 -> 4.2 diary 串接 (那是 Stage 4.2 開工時)

約束 (從 Bry 18:24 拍板 + 21:30 整理):
- 「拒絕問, 強制讀, 不准反問優先序」: 自動 update 失敗時 raise, 不靜默吞
- 「完成度標記要誠實, 有零件跟產品 work 是兩回事」: 寫到哪就是哪
- 排程器 (缺口 1) 跟 4.2 diary 是後續 stage, 不在這個檔案
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("soul_os.soul.relationships")

# ───────────────────────────────────────────────────────────
# 常數 (Bry 拍板)
# ───────────────────────────────────────────────────────────

# Bry 是一個 special entity (user_id), 出現在所有 agent 的 others 裡
# 但 Bry 不是 agent_id, 視為 "bryan" namespace
BRYAN_ENTITY_ID = "user_bryan"

# Confidence 初始 bias (Perplexity + Bry 拍板)
CONFIDENCE_DEFAULT_STRANGER = 0.3    # 完全陌生
CONFIDENCE_DEFAULT_KNOWN = 0.5       # 已知身份但無互動
CONFIDENCE_BIAS_SIBLINGS = 0.7      # 同源/姐妹初始 (Rem+Ram)
# 註: 初始 bias 不寫死進 schema, 是 create_relationship() 的 default arg

# Confidence 衰減 (Bry 拍板)
CONFIDENCE_DELTA_POSITIVE_LOW = 0.02   # 小正向 (Bry 簡短問候) — 跟 0.02/天衰減對稱, 30 觸發到 0.9
CONFIDENCE_DELTA_POSITIVE_HIGH = 0.15  # 大正向 (深度對話/被信任)
CONFIDENCE_DELTA_CONFLICT_LOW = 0.10   # 小衝突 (被忽略/小誤解)
CONFIDENCE_DELTA_CONFLICT_HIGH = 0.20  # 大衝突 (被拋下/嚴重誤解)
CONFIDENCE_DECAY_PER_DAY = 0.02        # 自然衰減 (沒互動就疏遠)
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0

# M5.13-5 (Bry 派工 2026-08-12 20:42): Untouched-entry decay grace period.
# 從未互動過的 entry (last_interaction_at is None) 在 grace period 內不 decay,
# 過了 grace period 才從 created_at 起算 decay。
# 預設 1 天 — 守 M5.13-2 strict 0.3 contract (ensure_relationship(0.3) 第一次 read
# 必須還是 0.3), 1 天 grace 確保新 entry 不會被立刻 decay。
UNTOUCHED_DECAY_GRACE_DAYS = 1.0


# ───────────────────────────────────────────────────────────
# Pydantic-style schema (用 dict 結構, 不引 pydantic 依賴)
# ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_relationship_entry(
    other_id: str,
    impression: str = "",
    feeling: str = "neutral",
    confidence: float = CONFIDENCE_DEFAULT_STRANGER,
    interaction_count: int = 0,
    last_interaction_at: Optional[str] = None,
    last_updated: Optional[str] = None,
) -> Dict:
    """建立一個新的 relationship entry。

    SG-2 (schema 4.2, 契约 SG-1 §2.2): additive 8 字段:
      - objective (客观观察分区): reply_exchanges / co_presence_sessions /
        dream_exchanges / last_signal_at — 全部整数计数, 仅沉淀层写入
      - impression_tags (主观投射): 质性印象标签, 0 数值权重
      - relational_band (主观投射): stranger|known|familiar|close 离散带
      - band_updated_at / last_relation_update_ref (幂等引用键)

    4.1 旧字段 (impression/feeling/confidence/interaction_count/
    last_interaction_at/last_updated/created_at) 全部保留读取;
    confidence 降级为只读遗留字段 (D4: 关系状态唯一真值 = band + impression_tags,
    新写入面 0 confidence 运算)。
    """
    now = _now_iso()
    return {
        "impression": impression,
        "feeling": feeling,
        "confidence": confidence,
        "interaction_count": interaction_count,
        "last_interaction_at": last_interaction_at,
        "last_updated": last_updated or now,
        "created_at": now,
        # ── SG-2 additive (schema 4.2) ─────────────────────
        "objective": {
            "reply_exchanges": 0,
            "co_presence_sessions": 0,
            "dream_exchanges": 0,
            "last_signal_at": None,
        },
        "impression_tags": [],
        "relational_band": "stranger",
        "band_updated_at": None,
        "last_relation_update_ref": None,
    }


def _new_relationships_file(agent_id: str) -> Dict:
    """建立一個新的 relationships 檔案結構。"""
    return {
        "agent_id": agent_id,
        "schema_version": "4.2",
        "created_at": _now_iso(),
        "last_decay_at": _now_iso(),
        "others": {},
    }


# ───────────────────────────────────────────────────────────
# RelationshipsStore — 單一 agent 的關係圖讀寫
# ───────────────────────────────────────────────────────────

class RelationshipsStore:
    """
    單一 agent 的 relationships.json 讀寫 + 自動衰減。

    Threading:
    - 內部用 threading.RLock 保護 _cache + 檔案寫入
    - 多個 agent 各有獨立 store instance
    - MemoryMiddleware 對每個 agent 持有自己的 store
    """

    def __init__(self, agent_id: str, data_dir: Path):
        self.agent_id = agent_id
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "relationships.json"
        self._lock = threading.RLock()
        self._cache: Optional[Dict] = None
        self._load_or_init()

    def _load_or_init(self) -> None:
        """讀檔, 不存在就建空檔。"""
        with self._lock:
            if self.file_path.exists():
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        self._cache = json.load(f)
                    # 跑一次衰減 (catches up if server was down)
                    self._decay_locked()
                    self._flush_locked()
                    return
                except (json.JSONDecodeError, OSError) as e:
                    # 「拒絕問, 強制讀」: 不要靜默吞壞檔, 但要備份
                    logger.warning(
                        f"[RelationshipsStore] {self.agent_id} 壞檔, 備份 + 重建: {e}"
                    )
                    backup_path = self.file_path.with_suffix(
                        f".corrupted.{int(datetime.now(timezone.utc).timestamp())}.json"
                    )
                    try:
                        self.file_path.rename(backup_path)
                        logger.info(
                            f"[RelationshipsStore] {self.agent_id} 壞檔備份到 {backup_path.name}"
                        )
                    except OSError:
                        pass  # 連備份都失敗, 跳過, 建新檔覆蓋
            self._cache = _new_relationships_file(self.agent_id)
            self._flush_locked()

    def _flush_locked(self) -> None:
        """寫回磁碟 (callers 必須持 lock)。"""
        if self._cache is None:
            return
        # 寫到 tmp 再 rename, 避免半寫狀態
        tmp_path = self.file_path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.file_path)
        except OSError as e:
            logger.error(
                f"[RelationshipsStore] {self.agent_id} 寫入失敗: {e}"
            )
            raise

    def _decay_locked(self) -> None:
        """自然衰減 (callers 必須持 lock)。

        M5.13-4.2 (Bry 拍板 2026-08-11 22:40):
        從 file-level `last_decay_at` 改為 per-entry anchor —
        每個 entry 從自己的 `last_interaction_at` 起算 decay。
        從未互動過的 entry (`last_interaction_at` is None) 跳過 decay,
        確保 `ensure_relationship(0.3)` 第一次 read 不會被 FP 噪訊推過 0.3 邊界
        (守 M5.13-2 strict contract: `confidence < 0.3 → 陌生人`, 沒 tolerance)。

        M5.13-5 (Bry 派工 2026-08-12 20:42):
        Untouched-entry decay — 從未互動過的 entry 若 `created_at` 超過
        `UNTOUCHED_DECAY_GRACE_DAYS` (預設 1.0 天) 則從 `created_at` 起算 decay。
        守 M5.13-2 strict 0.3 contract (grace 期間新 entry 不會被 decay)。
        - Touched entry: anchor = last_interaction_at, decay = days * 0.02
        - Untouched entry (last_interaction_at is None):
            - within grace (created_at 在 grace 內) → 跳過 (不 decay)
            - beyond grace (created_at 超過 grace) → anchor = created_at, decay
        - 缺 created_at / 壞 timestamp: 跳過 (legacy, no crash, deterministic)
        - days <= 0: 跳過 (anchor 比 now 還新, 不可能但 defensive)
        - last_decay_at 仍寫回當 backward-compat metadata (其他 code 不再讀來算 decay)
        """
        if self._cache is None:
            return
        now = datetime.now(timezone.utc)
        for entry in self._cache.get("others", {}).values():
            # M5.13-4.2: per-entry anchor, 從 last_interaction_at 起算
            anchor_iso = entry.get("last_interaction_at")
            if not anchor_iso:
                # M5.13-5: 從未互動過的 entry 嘗試從 created_at decay (with grace)
                # 這維持 M5.13-2 strict 0.3 contract: grace 期間新 entry 不會被 decay,
                # 過了 grace 才進入 decay path。
                created_iso = entry.get("created_at")
                if not created_iso:
                    # Legacy entry: 沒有 last_interaction_at 也沒有 created_at,
                    # 跳過 (deterministic, no crash, 不 silent-delete)。
                    continue
                try:
                    created_dt = datetime.fromisoformat(created_iso)
                except (ValueError, TypeError):
                    # 壞 created_at 跳過, 不 crash 整個 store
                    continue
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_days = (now - created_dt).total_seconds() / 86400.0
                if age_days < UNTOUCHED_DECAY_GRACE_DAYS:
                    # Grace period 內, 跳過 decay (M5.13-2 strict 0.3 contract)
                    continue
                # Grace 已過, 從 created_at 起算 decay
                anchor_iso = created_iso
            try:
                anchor_dt = datetime.fromisoformat(anchor_iso)
            except (ValueError, TypeError):
                # 壞 timestamp 跳過, 不 crash 整個 store
                continue
            if anchor_dt.tzinfo is None:
                anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)
            days = (now - anchor_dt).total_seconds() / 86400.0
            if days <= 0:
                continue
            decay = days * CONFIDENCE_DECAY_PER_DAY
            entry["confidence"] = max(
                CONFIDENCE_MIN,
                entry["confidence"] - decay,
            )
            # 衰減完也更新 last_updated
            entry["last_updated"] = now.isoformat()
        # last_decay_at 保留當 backward-compat metadata (其他 code 不再用它算 decay)
        self._cache["last_decay_at"] = now.isoformat()
        logger.debug(
            f"[RelationshipsStore] {self.agent_id} per-entry decay applied"
        )

    # ── 公開 API ─────────────────────────────────────────

    def get(self, other_id: str) -> Optional[Dict]:
        """讀某個 other 的 relationship entry, 不存在回 None。"""
        with self._lock:
            self._decay_locked()
            self._flush_locked()
            others = self._cache.get("others", {}) if self._cache else {}
            return others.get(other_id)

    def get_all(self) -> Dict[str, Dict]:
        """讀全部 others (snapshot)。"""
        with self._lock:
            self._decay_locked()
            self._flush_locked()
            return dict((self._cache or {}).get("others", {}))

    def ensure_relationship(
        self,
        other_id: str,
        initial_confidence: float = CONFIDENCE_DEFAULT_STRANGER,
        initial_impression: str = "",
        initial_feeling: str = "neutral",
    ) -> Dict:
        """確保 other_id 存在, 不存在就建, 回傳 entry。

        為什麼 ensure 而非 strict get:
        - 第一隻 agent 跟 Bry 對話時, Bry 還沒出現在這個 agent 的 others
        - 第一隻 agent 跟某 agent 同 session 時, 對方也還沒出現
        - 用 ensure 避免 caller 處理 None 的麻煩
        """
        with self._lock:
            self._decay_locked()
            others = self._cache.setdefault("others", {})
            if other_id not in others:
                others[other_id] = _new_relationship_entry(
                    other_id=other_id,
                    impression=initial_impression,
                    feeling=initial_feeling,
                    confidence=initial_confidence,
                )
                logger.debug(
                    f"[RelationshipsStore] {self.agent_id} 新增 relationship -> {other_id} "
                    f"(conf={initial_confidence})"
                )
            self._flush_locked()
            return others[other_id]

    def update_impression(
        self,
        other_id: str,
        impression_text: str,
        max_length: int = 20,
        impression_tags: Optional[List[str]] = None,
    ) -> Dict:
        """
        Stage 4.3: LLM 抽到的 impression 寫進 relationships (短日文片語, 預設 ≤20 字).

        - 確保 other 存在 (call ensure_relationship)
        - 長度 cap 避免 LLM 吐長句
        - 失敗 / 空字串 → 留空不寫 (「拒絕問, 強制讀」, 失敗不假資料)
        - impression 欄位是 4.1 schema 預留的, 4.3 第一次填
        - SG-2 (schema 4.2): 可選 impression_tags (List[str], 质性印象标签,
          additive 参数, 缺省不寫 → 0 破坏既有调用方)
        """
        with self._lock:
            self._decay_locked()
            entry = self.ensure_relationship(other_id)
            text = (impression_text or "").strip()
            if not text:
                return entry
            # 截斷: 短日文 ≤ 20 字 (20 chars ≈ 10 個日文單詞, 適合 impression 短語)
            if len(text) > max_length:
                text = text[:max_length].rstrip() + "…"
            entry["impression"] = text
            if impression_tags is not None:
                if not isinstance(impression_tags, list):
                    logger.warning(
                        f"[RelationshipsStore] impression_tags 非 list, 忽略: "
                        f"{type(impression_tags).__name__}"
                    )
                else:
                    entry["impression_tags"] = [
                        str(t).strip() for t in impression_tags if str(t).strip()
                    ]
            entry["last_updated"] = _now_iso()
            self._flush_locked()
            logger.debug(
                f"[RelationshipsStore] {self.agent_id} impression → {other_id}: {text!r}"
            )
            return entry

    def touch(
        self,
        other_id: str,
        confidence_delta: float = CONFIDENCE_DELTA_POSITIVE_LOW,
        feeling: Optional[str] = None,
    ) -> Dict:
        """
        互動發生時呼叫: 累加 interaction_count, 調 confidence, 更新 last_interaction_at。

        Args:
            other_id: 對方 entity (agent_id 或 BRYAN_ENTITY_ID)
            confidence_delta: 這次互動的 confidence 變化 (預設正向低)
            feeling: 選填, 若給就覆寫 feeling 欄位 (例: 'guarded' -> 'warming')
        """
        with self._lock:
            self._decay_locked()
            entry = self.ensure_relationship(other_id)
            entry["confidence"] = max(
                CONFIDENCE_MIN,
                min(
                    CONFIDENCE_MAX,
                    entry["confidence"] + confidence_delta,
                ),
            )
            entry["interaction_count"] = entry.get("interaction_count", 0) + 1
            entry["last_interaction_at"] = _now_iso()
            entry["last_updated"] = _now_iso()
            if feeling is not None:
                entry["feeling"] = feeling
            self._flush_locked()
            logger.debug(
                f"[RelationshipsStore] {self.agent_id} touch {other_id} "
                f"delta={confidence_delta:+.2f} conf={entry['confidence']:.2f} "
                f"count={entry['interaction_count']}"
            )
            return entry

    # ── SG-2 关系演化沉淀写入口（schema 4.2, 唯一写路径）────────────

    def apply_relation_evaluation(
        self,
        other_id: str,
        reply_exchanges_delta: int = 0,
        co_presence_sessions_delta: int = 0,
        dream_exchanges_delta: int = 0,
        ref: Optional[str] = None,
        now_iso: Optional[str] = None,
    ) -> Dict:
        """SG-2 沉淀层评估写入口: objective 整数计数增量 + 带状态机步进。

        契约约束 (SG-1 §2.2/§3/§4.2):
          - objective.* 仅沉淀层 24h 评估窗口写入（采集层 0 写）
          - 全整数计数（0 加权公式, 0 浮点）
          - band 由 src/social/relational_bands 纯函数判定（离散阶梯, 至多升 1 级/评估）
          - 幂等: ref (last_relation_update_ref = rel:<other_id>:<ts>) 重复 → 0 变更
          - 0 confidence 写入（D4: 关系域唯一真值 = band + impression_tags;
            本方法 0 处 confidence 运算, 只走 4.2 字段面）
          - 0 SAGE / 0 InnerLifeWriter / 0 SubmissionGate（写路径物理隔离）

        Args:
            other_id: 对方 entity（agent_id 或 BRYAN_ENTITY_ID）
            reply_exchanges_delta / co_presence_sessions_delta / dream_exchanges_delta:
                本 24h 窗口确定性聚合出的整数增量（≥0）
            ref: 幂等引用键 `rel:<other_id>:<ts>`（契约 §2.2）; None → 不写幂等键
            now_iso: 评估时刻（ISO 8601 UTC）; None → 当前时刻

        Returns:
            写入后的 entry（含 band 判定结果; 幂等命中时返回原 entry）
        """
        from src.social.relational_bands import (
            BAND_STRANGER,
            demote_band,
            evaluate_band,
            should_demote,
        )
        now_iso = now_iso or _now_iso()
        try:
            now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            now_dt = datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        deltas = {
            "reply_exchanges": int(reply_exchanges_delta),
            "co_presence_sessions": int(co_presence_sessions_delta),
            "dream_exchanges": int(dream_exchanges_delta),
        }
        for k, v in deltas.items():
            if v < 0:
                logger.warning(
                    f"[RelationshipsStore] 负增量拒绝 (fail-closed): {k}={v} "
                    f"other={other_id}"
                )
                deltas[k] = 0
        with self._lock:
            self._decay_locked()
            entry = self.ensure_relationship(other_id)
            # 幂等去重（契约 §4.2.4: 同一信号窗口不重复写）
            if ref and entry.get("last_relation_update_ref") == ref:
                return entry
            obj = entry.setdefault("objective", {})
            for k, v in deltas.items():
                obj[k] = int(obj.get(k, 0)) + v
            has_signal = any(v > 0 for v in deltas.values())
            if has_signal:
                obj["last_signal_at"] = now_iso
            # 带状态机步进（离散阶梯, 0 浮动）:
            # - 有信号 → 只升带（evaluate_band, 累计计数, 每评估至多升 1 级）
            # - 无信号 → 先查 30 天降带（stale → 降 1 带, 本轮不再升）;
            #   不降带则慢爬评估（计数满足更高门槛时顺带升带, 幂等;
            #   SG-2.1: 该慢爬仅限带≥known 的对子, 底带 stranger 无信号不回升
            #   — 慢爬回升要求窗口内有新信号, 详见下方分支注释）
            demoted = False
            if not has_signal and should_demote(
                obj.get("last_signal_at"),
                now_dt,
                fallback_ts=entry.get("last_interaction_at"),
            ):
                new_band = demote_band(entry.get("relational_band", "stranger"))
                if new_band != entry.get("relational_band", "stranger"):
                    entry["relational_band"] = new_band
                    entry["band_updated_at"] = now_iso
                    demoted = True
            if not demoted:
                # SG-2.1（工单 SG-2.1, TL-9 发现立案）: 慢爬评估要求窗口内有新信号。
                # 无信号时底带 stranger 不允许凭历史计数自动回升（离散遗忘语义:
                # 降带后关系淡了, 需要窗口内新证据才能重新建立; 消除
                # familiar→known→stranger 后下一结算又回升 known 的确定性振荡）。
                # 判定载体 = 本窗口 deltas 聚合出的 has_signal（已有窗口计数对象,
                # 0 新增 sidecar / 0 接口变更）:
                #   - 有信号 → 正常升级路径全保留（stranger→known 门槛
                #     reply≥1 OR co≥2 照旧, 每窗至多升 1 级）;
                #   - 无信号且带 ≥ known → 慢爬照旧（本修只封底带回升）。
                # 计数保留不清零, 降带逻辑 / 30 天阈值 / 其他带行为不变。
                # SG-2.2（4.1 兼容）: 老 4.1 entry 无 relational_band 键,
                # 且无信号 stranger 对子跳过下方升级分支 → 一旦写盘, 末尾日志
                # 直接索引会 KeyError。先 setdefault 兜底（语义 = get 默认值
                # stranger, 与读取路径严格一致; 已有键时 no-op, 升级/降带/慢爬
                # 分支逻辑 0 变更）。
                entry.setdefault("relational_band", BAND_STRANGER)
                current_band = entry["relational_band"]
                if has_signal or current_band != BAND_STRANGER:
                    new_band = evaluate_band(
                        current_band,
                        reply_exchanges=obj.get("reply_exchanges", 0),
                        co_presence_sessions=obj.get("co_presence_sessions", 0),
                        dream_exchanges=obj.get("dream_exchanges", 0),
                    )
                    if new_band != entry.get("relational_band", "stranger"):
                        entry["relational_band"] = new_band
                        entry["band_updated_at"] = now_iso
            if ref:
                entry["last_relation_update_ref"] = ref
            entry["last_updated"] = now_iso
            self._flush_locked()
            logger.debug(
                f"[RelationshipsStore] SG-2 关系演化沉淀: {self.agent_id}→{other_id} "
                f"deltas={deltas} ref={ref} band={entry.get('relational_band', BAND_STRANGER)}"
            )
            return entry


# ───────────────────────────────────────────────────────────
# MultiAgentRelationshipsManager — 跨 agent 統一管理
# ───────────────────────────────────────────────────────────

class MultiAgentRelationshipsManager:
    """
    包多個 RelationshipsStore, 提供 batch API。

    主要用在 MemoryMiddleware:
    - 收到 USER_MESSAGE for agent_x → store_x.touch(BRYAN_ENTITY_ID)
    - 收到 AGENT_SPEAK from agent_x → 對 session 內所有出現的 agent 互相 touch

    Stage 4.1 第一刀不包含 LLM 抽 impression (那是 4.3 範圍),
    所以「impression」欄位目前空著, 之後 4.3 開工時由 LLM 生成。
    """

    def __init__(self, data_dir: Optional[str] = None):
        # P0.5 (Bry 派工 2026-08-09 19:48): use data_root() for test isolation
        from src.paths import data_root
        if data_dir is None:
            data_dir = str(data_root() / "soul")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._stores: Dict[str, RelationshipsStore] = {}
        self._lock = threading.RLock()

    def _get_store(self, agent_id: str) -> RelationshipsStore:
        with self._lock:
            if agent_id not in self._stores:
                self._stores[agent_id] = RelationshipsStore(
                    agent_id=agent_id,
                    data_dir=self.data_dir / agent_id,
                )
            return self._stores[agent_id]

    def on_user_message(
        self, target_agent_id: str, user_id: str = BRYAN_ENTITY_ID
    ) -> None:
        """
        Stage 4.1 第一刀觸發點: Bry 對 target_agent 發 USER_MESSAGE。

        預設正向低 (+0.05), Bry 每次開口, 該 agent 對 Bry 信任就微升。
        後續 4.3 可以根據 LLM judge 出的 stance 給 -0.10 / +0.15 不同 delta。
        """
        store = self._get_store(target_agent_id)
        store.touch(user_id, confidence_delta=CONFIDENCE_DELTA_POSITIVE_LOW)

    def on_agent_speak(
        self,
        speaker_agent_id: str,
        session_agents: List[str],
    ) -> None:
        """
        Stage 4.1 第一刀觸發點: speaker 在某 session 對其他 agent 發 AGENT_SPEAK。

        對同 session 所有其他 agent:
        - speaker 對 other 的 confidence += +0.02 (有共同 context, 不陌生)
        - 互動計數 +1

        Args:
            speaker_agent_id: 說話的 agent
            session_agents: 這次 session 出現過的 agent list (含 speaker)
        """
        if not session_agents:
            return
        store = self._get_store(speaker_agent_id)
        for other_id in session_agents:
            if other_id == speaker_agent_id:
                continue
            store.touch(
                other_id,
                confidence_delta=CONFIDENCE_DELTA_POSITIVE_LOW * 0.4,  # 0.02
            )

    def on_dream(self, dreamer_id: str, target_id: str) -> None:
        """
        Stage 4.3 (Mavis 拍板 2026-07-21 16:35): 夢境觸發雙向 touch.

        - dreamer 對 target: +0.05 (主動夢到, confidence 升)
        - target 對 dreamer: +0.02 (被夢到, 反向低, 因 target 沒主動)
        - 場景限定: 只在 dream 觸發, USER_MESSAGE 觸發的對話仍是單向
        - 跟 on_agent_speak 區分: 夢境是 dreamer 自己的內在活動, 不是對話
        """
        if dreamer_id == target_id:
            return
        # dreamer → target (主動)
        self._get_store(dreamer_id).touch(
            target_id,
            confidence_delta=CONFIDENCE_DELTA_POSITIVE_LOW,  # 0.02
        )
        # target → dreamer (被動, 反向低)
        self._get_store(target_id).touch(
            dreamer_id,
            confidence_delta=CONFIDENCE_DELTA_POSITIVE_LOW * 0.4,  # 0.008 ≈ 0.01
        )

    def on_event(self, agent_id: str) -> None:
        """
        Stage 4.3 (Mavis 拍板 2026-07-21 16:35): 事件觸發對 Bry 微量 touch.

        - agent 對 Bry: +0.01 (Bry 不在場, 但事件觸發「想到 Bry」)
        - 為什麼對 Bry 而不是其他角色: 事件是 random, 沒特定對象
        - Bry 是 user_bryan 實體, 確保 Bry 永遠在角色世界的關係網裡
        """
        self._get_store(agent_id).touch(
            BRYAN_ENTITY_ID,
            confidence_delta=CONFIDENCE_DELTA_POSITIVE_LOW * 0.5,  # 0.01
        )

    def get_store(self, agent_id: str) -> RelationshipsStore:
        """
        對外提供單 store 讀取 (給 debug / 將來 4.2 diary 用)。

        M5.11-2 (Bry 派工 2026-08-11): Relationship read access 是 intentional boundary。
        Stage 4.1 只做 write (touch/ensure/update_impression), read API 是預留介面,
        為 Stage 4.2 (diary 串接) / Stage 4.3 (LLM impression) 預留。
        目前 0 個 production consumer 調用 read API — 這是設計決策, 不是缺失。
        Stage 4.2/4.3 範圍確定前不應主動實現 relationship read 邏輯。
        """
        return self._get_store(agent_id)

    def stats(self) -> Dict:
        """整體健康指標 (給 debug / log)。"""
        with self._lock:
            return {
                "data_dir": str(self.data_dir),
                "agents_loaded": list(self._stores.keys()),
                "agent_count": len(self._stores),
            }


# Module-level singleton (lazy init)
_manager_singleton: Optional[MultiAgentRelationshipsManager] = None
_manager_lock = threading.Lock()


def get_relationships_manager(
    # P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
    data_dir: Optional[str] = None,
) -> MultiAgentRelationshipsManager:
    """取得全域 MultiAgentRelationshipsManager (lazy singleton)。"""
    global _manager_singleton
    with _manager_lock:
        if _manager_singleton is None:
            _manager_singleton = MultiAgentRelationshipsManager(data_dir)
        return _manager_singleton
