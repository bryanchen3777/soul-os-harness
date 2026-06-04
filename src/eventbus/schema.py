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
    AGENT_INTENT          = "agent_intent"           # Agent 想發言的意圖（搶奪發言權）
    AGENT_INTENT_ENRICHED = "agent_intent_enriched"  # 記憶已注入的 intent（MemoryMiddleware → LLMProxy）
    MEMORY_QUERY          = "memory_query"           # 向 Memory Middleware 發出查詢請求
    MEMORY_RETRIEVED      = "memory_retrieved"       # Memory Middleware 回傳的記憶結果
    LLM_REQUEST           = "llm_request"            # 向 LLM Proxy 發出生成請求
    LLM_RESPONSE          = "llm_response"           # LLM Proxy 回傳的生成結果

    # 輸出動作
    AGENT_SPEAK     = "agent_speak"       # Agent 正式輸出的文字（送往 I/O Gateway）
    AGENT_ACTION    = "agent_action"      # 實體動作指令（未來給機器人馬達、TTS 等）

    # 系統管理
    SYSTEM_ERROR    = "system_error"      # 任何模組拋出的錯誤，統一匯報
    AGENT_STATE_UPDATE = "agent_state_update"  # Agent 情緒/狀態變更通知


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
