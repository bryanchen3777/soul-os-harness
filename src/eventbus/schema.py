# soul_event_schema.py
# Soul OS — Phase 1: 標準事件格式定義
# 所有在 Event Bus 上流通的訊息都必須符合此 Schema

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import uuid


# ─────────────────────────────────────────────
# 1. 事件類型枚舉
# ─────────────────────────────────────────────

class EventType(str, Enum):
    # 外部觸發
    USER_MESSAGE    = "user_message"      # 來自使用者的訊息
    SYSTEM_TICK     = "system_tick"       # Heartbeat Engine 發出的時間脈衝

    # 內部流轉
    AGENT_INTENT          = "agent_intent"             # Agent 想發言的意圖（搶奪發言權）
    AGENT_INTENT_ENRICHED = "agent_intent_enriched"    # 記憶已注入的 intent（MemoryMiddleware → WorldPerceptionMiddleware）
    # M3 Phase 1: WorldPerceptionMiddleware 在 MemoryMiddleware 之後接管,
    # 算 top-N world_context 後 re-publish 為 AGENT_INTENT_PERCEIVED,
    # SpeakerTokenManager 訂閱此 type (取代 AGENT_INTENT_ENRICHED)。
    AGENT_INTENT_PERCEIVED = "agent_intent_perceived"  # 世界感知已注入的 intent（WorldPerceptionMiddleware → SpeakerTokenManager）
    # M3 Phase 1: 外部世界事件 source 發布, WorldPerceptionMiddleware 訂閱更新 WorldPerceptionState
    WORLD_EVENT           = "world_event"              # 外部世界事件 (weather / news / calendar / ...)
    SPEAKER_TOKEN_REQUEST = "speaker_token_request"   # 發言權申請（保留命名，現階段用 ENRICHED 觸發）
    SPEAKER_TOKEN_GRANTED = "speaker_token_granted"   # 發言權已授予（SpeakerTokenManager → LLMProxy）
    SPEAKER_TOKEN_RELEASED = "speaker_token_released"  # 發言權已釋放（給監聽者、debug 用）
    MEMORY_QUERY          = "memory_query"             # 向 Memory Middleware 發出查詢請求
    MEMORY_RETRIEVED      = "memory_retrieved"         # Memory Middleware 回傳的記憶結果
    LLM_REQUEST           = "llm_request"              # 向 LLM Proxy 發出生成請求
    LLM_RESPONSE          = "llm_response"             # LLM Proxy 回傳的生成結果

    # 輸出動作
    AGENT_SPEAK     = "agent_speak"       # Agent 正式輸出的文字（送往 I/O Gateway）
    AGENT_ACTION    = "agent_action"      # 實體動作指令（未來給機器人馬達、TTS 等）
    AGENT_AUDIO_READY = "agent_audio_ready"  # TTS mp3 已寫入磁碟,廣播給 channel 訂閱者（web/telegram）

    # 系統管理
    SYSTEM_ERROR    = "system_error"      # 任何模組拋出的錯誤，統一匯報
    AGENT_STATE_UPDATE = "agent_state_update"  # Agent 情緒/狀態變更通知
    SESSION_END     = "session_end"       # Heartbeat 偵測到 elapsed >= 30min，代表 session 自然結束

    # M5.2-G: Scheduler → Agency bridge trigger
    # Bry 拍板 2026-08-08 M5.2-F: 跟 AGENT_INTENT 語意分離
    #   - AGENT_INTENT = "Agent 想發言的意圖" (搶奪發言權, 舊路徑)
    #   - AGENCY_TRIGGER = "Scheduler 提議現在 act" (M5.2-G 新路徑)
    # 既有 event type 名稱 / payload / semantics 不變, 純 additive
    AGENCY_TRIGGER  = "agency_trigger"     # M5.2-G: Scheduler 發給 Agency 的 trigger (proactive_dm etc.)

    # SI-2.1 (Social Diffusion Contract, 2026-09-03): 靈魂間社交事件 (廣播擴散)
    # 既有 20 個枚舉值語意 0 變更, 純 additive。
    # 語意: 有行為主體 (actor_id = 靈魂 id) 的社交行為, 跟 WORLD_EVENT (客觀世界事實)
    # 平行不混用。感知路徑: WorldPerceptionMiddleware 平行訂閱 (防線 1 Ambient Path)。
    # 身份防污染: 防線 3 Identity Firewall 判定 actor_id != current_agent_id →
    # EXTERNAL_OTHER_ACTION, fail-closed 拒絕內化/昇華。
    SOCIAL_WORLD_EVENT = "social_world_event"   # SI-2.1: 靈魂間社交事件 (廣播擴散)


class EventPriority(int, Enum):
    """
    數字越小 = 優先級越高。
    asyncio.PriorityQueue 按照 (priority, timestamp) 排序。

    設計原則：
    - CRITICAL 用於錯誤與緊急系統訊號，需立即處理
    - HIGH 是使用者真人輸入，不能讓心跳或內部流轉排在前面
    - NORMAL 是內部模組間的正常流轉
    - LOW 是背景任務（定時 Tick、狀態更新）
    """
    CRITICAL = 0   # 系統錯誤、緊急中斷
    HIGH     = 1   # 使用者訊息（USER_MESSAGE）
    NORMAL   = 2   # 內部流轉（AGENT_INTENT、MEMORY_*、LLM_*）
    LOW      = 3   # 背景任務（SYSTEM_TICK、AGENT_STATE_UPDATE）


# ─────────────────────────────────────────────
# 2. 核心事件格式
# ─────────────────────────────────────────────

class SoulEvent(BaseModel):
    # ── 身份識別 ──
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="唯一事件 ID，自動生成 UUID，用於 Debug 與去重"
    )
    event_type: EventType

    # ── 路由 ──
    source: str = Field(
        ...,
        description="發送者識別碼，例如: 'system', 'user_bryan', 'agent_ruka', 'heartbeat'"
    )
    target: str = Field(
        default="broadcast",
        description=(
            "'broadcast' = 所有訂閱者都收到；"
            "指定 agent_id 則僅送達該 Agent（私訊）"
        )
    )

    # ── 優先級與時效 ──
    priority: EventPriority = Field(
        default=EventPriority.NORMAL,
        description="優先級。PriorityQueue 依此排序，確保使用者訊息不被心跳插隊"
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description=(
            "事件過期時間（UTC）。消費前若已過期則丟棄。"
            "SYSTEM_TICK 建議設為 timestamp + 60s，避免積壓的過期 Tick 繼續觸發行動"
        )
    )

    # ── 內容 ──
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="事件的具體資料，依 event_type 各自定義結構（見下方 Payload 慣例）"
    )

    # ── 時間戳 ──
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="事件建立時間（UTC，帶時區）"
    )

    # ── 追蹤與上下文 ──
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "對話會話 ID。一個 session 代表一段連續的 context 視窗。"
            "Memory Middleware 用此決定是否繼承上下文。"
            "注意：與 correlation_id 不同——一個 session 可以包含多條 correlation chain"
        )
    )
    correlation_id: Optional[str] = Field(
        default=None,
        description=(
            "關聯的起始事件 ID。追蹤「哪個 User 訊息觸發了這條處理鏈」。"
            "例如：USER_MESSAGE → AGENT_INTENT → LLM_REQUEST → AGENT_SPEAK，"
            "這四個事件共享同一個 correlation_id = 原始 USER_MESSAGE 的 event_id"
        )
    )

    # ── Inner Life identity (M5.4-5.5 Bry 派工 2026-08-09 21:40) ──
    # Cross-reference 到 canonical InnerLifeEvent.event_id (M5.4-5.1 InnerLifeWriter)。
    # 跟 correlation_id / session_id 同性質: top-level Optional 跨事件 reference 欄位,
    # 預設 None (向後相容, 既有 producer 不需要改)。
    #
    # 跟 correlation_id 的差別:
    #   - correlation_id: 追蹤 event-chain causation (哪個 USER_MESSAGE 開始這條鏈)
    #   - inner_life_event_id: 指向 canonical InnerLifeEvent (哪個 lived-experience 事件)
    #
    # Producer (e.g. LLMProxy / AgencyTriggerHandler) 可以在發布 SoulEvent 時
    # 透過 InnerLifeWriter.create_event(...) 取得 event_id 後設到這欄。
    # Consumer (e.g. MemoryMiddleware / DiaryWriter) 可以讀 event.inner_life_event_id
    # 來跟 InnerLifeEvent registry 對應。
    inner_life_event_id: Optional[str] = Field(
        default=None,
        description=(
            "Cross-reference 到 canonical InnerLifeEvent.event_id (M5.4-5.1)。"
            "M5.4-5.5 開始: producer 可選填, consumer 可讀取。"
            "預設 None 表示此事件未跟特定 Inner Life event 綁定 (向後相容既有 producer)。"
        )
    )

    # ── Social identity (SI-2.1 Social Diffusion Contract, 2026-09-03) ──
    # 行為主體靈魂身份 (agent_id, e.g. 'agent_ruka')。
    # 跟 inner_life_event_id 同性質: top-level Optional 欄位, 預設 None (向後相容,
    # 既有 producer 不填不受影響)。
    #
    # 跟 source 的差別:
    #   - source: 管道發送者 (誰把事件放上 bus)
    #   - actor_id: 行為主體 (誰做了這個社交行為)
    #   對 SOCIAL_WORLD_EVENT 二者通常一致; 對系統轉發事件, source 可為系統而
    #   actor_id 指向原行為靈魂。
    #
    # 防線 3 Identity Firewall 的判定依據: actor_id != current_agent_id →
    # EXTERNAL_OTHER_ACTION 標籤, fail-closed 拒絕內化/昇華 (他者事件只能作背景感知)。
    actor_id: Optional[str] = Field(
        default=None,
        description=(
            "行為主體靈魂身份 (agent_id, e.g. 'agent_ruka')。"
            "SI-2.1 新增: 社交事件的行為主體; 系統級事件為 None。"
            "防線 3 Identity Firewall 的判定依據。"
            "預設 None 向後相容 (既有 producer 不填不受影響)。"
        )
    )

    # ── 版本相容性 ──
    schema_version: str = Field(
        default="1.0",
        description="Schema 版本號，供未來升級時的向後相容判斷使用"
    )

    class Config:
        use_enum_values = True

    def is_expired(self) -> bool:
        """檢查此事件是否已過期，過期事件應由 Bus 靜默丟棄"""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


# ─────────────────────────────────────────────
# 3. Payload 慣例文件（非強制，作為團隊契約）
# ─────────────────────────────────────────────
#
# EventType.USER_MESSAGE:
#   payload = {
#       "text": str,               # 使用者輸入的文字
#       "platform": str,           # "app" | "web" | "voice"
#       "attachments": list[dict]  # 選填，未來支援圖片、語音等
#   }
#
# EventType.SYSTEM_TICK:
#   payload = {
#       "tick_count": int,         # 從系統啟動累計的 Tick 次數
#       "elapsed_mins": float,     # 距離上次使用者互動的分鐘數
#   }
#
# EventType.AGENT_INTENT:
#   payload = {
#       "agent_id": str,           # 是哪個 Agent 想說話
#       "reason": str,             # 觸發意圖的原因（"silence_threshold" | "schedule" | ...）
#       "draft": str | None,       # 選填，Agent 預擬的訊息草稿（可被 LLM 覆寫）
#   }
#
# EventType.AGENT_INTENT_ENRICHED:
#   payload = AGENT_INTENT 全部欄位 +
#       "memory_context": str,    # MemoryMiddleware 注入的記憶摘要（可空字串）
#   用途：避免 AGENT_INTENT 被 re-publish 造成 MemoryMiddleware 無限迴圈。
#   LLMProxy 訂閱此類型，AGENT_INTENT 不再被 LLMProxy 直接消費。
#
# EventType.MEMORY_RETRIEVED:
#   payload = {
#       "query": str,              # 原始查詢語句
#       "results": list[dict],     # [{"content": str, "score": float, "created_at": str}]
#       "hit_count": int
#   }
#
# EventType.SPEAKER_TOKEN_GRANTED:
#   payload = AGENT_INTENT_ENRICHED 全部欄位（已含 memory_context、chrono_context）
#   用途：SpeakerTokenManager 仲裁通過後 re-publish，LLMProxy 訂閱此類型。
#   用新 event type 而非 reuse ENRICHED 是為了避免 re-publish 迴圈
#   （Manager 自己也訂閱 ENRICHED，reuse 會無限 loop）。
#
# EventType.SPEAKER_TOKEN_RELEASED:
#   payload = {
#       "agent_id": str,           # 釋放 token 的 agent
#       "reason": str,             # "spoke_done" | "timeout" | "queue_promotion"
#       "next_holder": str | None, # 下一個被授予的 agent（如果有 queue 等待者）
#   }
#   用途：純觀察事件，給監聽者 / debug 用，不影響業務邏輯。
#
# EventType.WORLD_EVENT (M3 Phase 1, Bry 拍板 2026-08-07 19:40):
#   payload = {
#       "source": str,             # "weather" | "news" | "calendar" | "social" | "synthetic"
#       "type": str,               # "rain_started" | "celebrity_news" | "calendar_event" | ...
#       "novelty_id": str,         # 同一事實的識別 (e.g. "weather_rain_2026-08-07"),
#                                  # 用途: novelty 去重, 同一 novelty_id 在 NOVELTY_WINDOW 內
#                                  # 多次觸發會降低 novelty_score
#       "ts": str,                 # 事件發生時間 (ISO 8601, UTC)
#       "summary": str,            # 一句話描述 (純客觀事實, 不含 user relevance)
#       "data": dict,              # 結構化 payload (隨 source/type 變動)
#   }
#   用途: 外部世界 source (Phase 1 = synthetic) 發布, WorldPerceptionMiddleware 訂閱
#   更新 WorldPerceptionState (in-memory, ephemeral, 不進 SAGE / 長期 memory)。
#   Invalid event → reject → trace → no context → no memory。
#
# EventType.AGENT_INTENT_PERCEIVED (M3 Phase 1, Bry 拍板 2026-08-07 19:40):
#   payload = AGENT_INTENT_ENRICHED 全部欄位 +
#       "world_context": str,      # WorldPerceptionMiddleware 注入的世界感知區塊
#                                  # (跟 inner_life / memory_context 同風格, 輕量字串)
#                                  # 沒有 world 事件 accept 時 = "" (注入 skip)
#       "world_perception_meta": dict,  # observability 用的 metadata
#                                  # {accepted_count, rejected_count, top_event_ids}
#   用途: WorldPerceptionMiddleware 接收 AGENT_INTENT_ENRICHED, 讀 WorldPerceptionState
#   算 top-N world_context, re-publish 為 AGENT_INTENT_PERCEIVED。
#   SpeakerTokenManager 訂閱此 type (取代 AGENT_INTENT_ENRICHED)。
#
# EventType.AGENT_SPEAK:
#   payload = {
#       "text": str,               # 最終輸出的文字
#       "action_tags": list[str],  # 給 I/O Gateway 的動作提示，例如 ["wave", "smile"]
#       "tts_enabled": bool        # 是否觸發語音合成
#   }
#
# EventType.AGENT_ACTION:
#   payload = {
#       "action": str,             # 動作名稱，例如 "servo_wave" | "led_pulse"
#       "params": dict,            # 動作參數，由具體 I/O 驅動解析
#   }
#
# EventType.SYSTEM_ERROR:
#   payload = {
#       "module": str,             # 出錯的模組名稱
#       "error_type": str,         # 錯誤類型
#       "message": str,            # 錯誤描述
#       "traceback": str | None    # 選填，完整 traceback
#   }
#
# EventType.AGENCY_TRIGGER (M5.2-G + M5.2-J Phase J-1 doc 補充, Bry 拍板 2026-08-08):
#   source = "soul_scheduler"
#   target = agent_id (private, 對該 agent 觸發)
#   payload = {
#       "trigger_type": str,         # "proactive_dm" (M5.2-G) |
#                                    # "event" (M5.2-H Phase 1) |
#                                    # "dream" (M5.2-H Phase 2) |
#                                    # "morning" | "night" (M5.2-H Phase 3)
#       "agent_id": str,            # 應該 act 的 agent
#       "reason": str,              # 觸發原因 (固定 "scheduler.{trigger_type}")
#       "elapsed_mins": float,      # 距上次同類 trigger 的分鐘數
#                                    # (從 _last_proactive_dm_time 算, production 對
#                                    #  non-proactive_dm 觸發通常 = 0.0)
#       "timestamp": str,           # ISO 8601 本地時間字串 (now_local().isoformat())
#       "extra": dict,              # trigger_type 特定 context 傳遞 (M5.2-H Phase 2 起)
#                                    # - dream: {"target_agent_id": str, "all_agents": list[str]}
#                                    #   target_agent_id 必填 (H2-I13 reject safely if missing)
#                                    #   all_agents 是 canonical agent list snapshot
#                                    # - proactive_dm / event / morning / night: {}
#   }
#   用途: Scheduler → Agency bridge (M5.2-F frozen TriggerEnvelope 語意, 跟 AGENT_INTENT 分離)。
#         4 個 handler 訂閱此 type, 各自過濾 trigger_type:
#           - AgencyTriggerHandler (M5.2-G)   → "proactive_dm"  → LLM executor
#           - EventHandler (M5.2-H Phase 1)  → "event"         → writer.write_event
#           - DreamHandler (M5.2-H Phase 2)  → "dream"         → writer.write_dream
#           - DiaryHandler (M5.2-H Phase 3)  → "morning"|"night" → diary_writer_executor
#   Schema 定義 (EventType.AGENCY_TRIGGER 枚舉值 + SoulEvent 結構) 自 M5.2-G 起 frozen;
#   J-1 只補 payload convention 文件, 不動 schema definition 本身。
#
# EventType.SOCIAL_WORLD_EVENT (SI-2.1 Social Diffusion Contract, 2026-09-03):
#   source = 管道發送者 (通常 = actor_id)
#   actor_id = 行為主體靈魂 id (SoulEvent.actor_id, 與 payload.actor_id 一致)
#   target = "broadcast" (防線 2 已把 private 攔截在廣播總線之外)
#   priority = EventPriority.LOW (低刺激度, 防線 1 Ambient 預設)
#   payload = {
#       "actor_id": str,        # 行為主體靈魂 id (與 SoulEvent.actor_id 一致, 冗餘便於獨立消費)
#       "space_id": str,        # 發生空間: "lounge" (客廳群聊) | "soul_wall" (靈魂牆)
#       "visibility": str,      # 可見性: "public" | "private" (到 bus 時必為 "public",
#                               #   防線 2 已攔截 private; "private" 出現在 bus 上 = 契約違例,
#                               #   訂閱端 fail-closed 丟棄)
#       "event_type": str,      # 社交行為細分類 (v1 白名單: greeting/share/reply/mood/activity)
#       "content": str,         # 簡明內容 (<= 200 chars)
#       "novelty_id": str,      # 去重 key ([a-z0-9_] 4-128, 復用 WorldEvent 規則)
#       "ts": str,              # 事件發生時間 (ISO 8601 UTC)
#       "summary": str,         # 客觀描述 (World Context 渲染用, <= 500 chars)
#       "data": dict,           # 結構化擴展 (選填, 預設 {})
#       "priority": int,        # 刺激度 hint (預設 0 = 低刺激度, 防線 1)
#   }
#   用途: 靈魂間社交事件廣播擴散。防線 1 (WorldPerceptionMiddleware 平行訂閱) →
#   world_context [社交感知] 區塊 (Ambient Perception, 不觸發 transmit);
#   防線 3 (Identity Firewall) 判定 actor_id != current_agent_id →
#   EXTERNAL_OTHER_ACTION, fail-closed 拒絕內化/昇華。
#   詳細契約見 docs/SOCIAL-DIFFUSION-CONTRACT.md。
