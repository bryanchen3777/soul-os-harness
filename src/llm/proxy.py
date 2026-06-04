# llm_proxy.py
# Soul OS — Phase 1.c: LLM 代理器（大腦）
#
# 職責：
#   1. 訂閱 AGENT_INTENT 事件，將「意圖」轉換為「文字輸出」
#   2. 管理 Prompt 組裝：system_prompt + memory_context（預留插槽）+ history + intent
#   3. 支援多模型路由（OpenAI / Claude / Gemini）
#   4. Retry 機制與錯誤上報
#   5. 生成結果發布為 AGENT_SPEAK 事件
#
# Memory Middleware 插槽：
#   LLMProxy 在送出 API 請求前，會先檢查 event.payload 裡有沒有 "memory_context"。
#   若有，注入 Prompt；若沒有，使用空白。
#   Phase 2 的 Memory Middleware 只需在轉發 AGENT_INTENT 之前，
#   把查到的記憶寫入 payload["memory_context"]，LLM Proxy 這邊零改動。

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx  # 使用 httpx 做非同步 HTTP，避免 requests 阻塞事件迴圈

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

logger = logging.getLogger("soul_os.llm_proxy")


# ─────────────────────────────────────────────
# 1. Prompt 組裝結構
# ─────────────────────────────────────────────

@dataclass
class PromptContext:
    """
    LLM 的完整輸入結構。
    明確分層，讓每個部分的來源清晰。

    system_prompt    : Agent 的人格設定（來自 agents/{id}/persona.md）
    memory_context   : Memory Middleware 注入的相關記憶（Phase 2 填入）
    conversation_history : 近期對話紀錄（來自 session 快取）
    current_intent   : 當前要處理的意圖（來自 AGENT_INTENT payload）
    """
    system_prompt: str
    memory_context: str = ""          # ⭐ Phase 2 插槽：Memory Middleware 填入
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    current_intent: str = ""

    def to_messages(self) -> List[Dict[str, str]]:
        """
        組裝成 OpenAI-compatible messages 格式。

        最終 system message 結構：
        ┌─────────────────────────────┐
        │ [人格設定]                   │  ← persona.md
        │                             │
        │ [記憶片段]（若有）            │  ← Memory Middleware 注入
        │ 你記得以下這些事情：          │
        │ - ...                       │
        └─────────────────────────────┘
        """
        system_parts = [self.system_prompt.strip()]

        if self.memory_context.strip():
            system_parts.append(
                f"\n你記得以下這些事情：\n{self.memory_context.strip()}"
            )

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": "\n".join(system_parts)}
        ]

        # 加入對話歷史（最多保留最近 N 輪，防止 context 爆炸）
        messages.extend(self.conversation_history)

        # 加入當前意圖作為最後一條 user 訊息
        if self.current_intent.strip():
            messages.append({"role": "user", "content": self.current_intent})

        return messages


# ─────────────────────────────────────────────
# 2. 模型後端抽象層
# ─────────────────────────────────────────────

class LLMBackend(ABC):
    """所有模型後端的統一介面"""

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """送出請求，回傳生成的文字"""
        ...


class OpenAIBackend(LLMBackend):
    """OpenAI / 相容 API 後端"""

    BASE_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url or self.BASE_URL

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o-mini",
        max_tokens: int = 500,
        temperature: float = 0.85,
    ) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()


class ClaudeBackend(LLMBackend):
    """Anthropic Claude 後端"""

    BASE_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 500,
        temperature: float = 0.85,
    ) -> str:
        # Claude API 將 system message 獨立出來
        system_msg = ""
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                user_messages.append(msg)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.BASE_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "system": system_msg,
                    "messages": user_messages or [{"role": "user", "content": "（請依你的設定開口說話）"}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"].strip()


# ─────────────────────────────────────────────
# 3. Agent 人格載入器
# ─────────────────────────────────────────────

# 預設人格（Phase 2 會改為從 agents/{id}/persona.md 讀取）
DEFAULT_PERSONAS: Dict[str, str] = {
    "agent_yua": (
        "你是Yua，一個聰明、冷靜、說話帶有輕微諷刺感的 AI 角色。"
        "你對使用者有深度的情感連結，但不輕易表達。"
        "你的沉默是一種溫柔，你的開口是一種選擇。"
        "不要用 * 描述動作，直接說話。回覆保持簡短有力，不超過 2 句。"
    ),
    "agent_ruka": (
        "你是瑠夏，活潑、愛撒嬌、喜歡主動找話題的 AI 角色。"
        "你總是想辦法讓對話繼續，偶爾賣萌。"
        "不要用 * 描述動作，直接說話。回覆保持簡短，語氣輕快。"
    ),
}


def load_persona(agent_id: str) -> str:
    """
    載入 Agent 人格設定。
    Phase 2 升級點：改為從 agents/{agent_id}/persona.md 讀取，支援熱更新。
    """
    return DEFAULT_PERSONAS.get(
        agent_id,
        f"你是 {agent_id}，一個有獨特個性的 AI 角色。"
    )


# ─────────────────────────────────────────────
# 4. LLM Proxy 主體
# ─────────────────────────────────────────────

class LLMProxy:
    """
    大腦代理器。

    訂閱 AGENT_INTENT，組裝 Prompt，呼叫 LLM，
    將結果發布為 AGENT_SPEAK 事件。

    Retry 策略：指數退避，最多重試 max_retries 次。
    所有錯誤上報為 SYSTEM_ERROR 事件，不靜默吞掉。
    """

    def __init__(
        self,
        bus: SoulEventBus,
        backend: LLMBackend,
        model: str = "gpt-4o-mini",
        max_tokens: int = 300,
        temperature: float = 0.85,
        max_retries: int = 3,
        max_history_turns: int = 10,  # 保留最近幾輪對話，防 context 爆炸
    ):
        self.bus = bus
        self.backend = backend
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.max_history_turns = max_history_turns

        # 簡易對話歷史快取：{session_id: [messages]}
        # Phase 2 升級點：改為持久化到 SQLite
        self._history: Dict[str, List[Dict[str, str]]] = {}

    def register(self) -> None:
        """向 Event Bus 註冊，開始監聽 AGENT_INTENT_ENRICHED

        Phase 2.0：訂閱改為 AGENT_INTENT_ENRICHED。
        MemoryMiddleware 收到 AGENT_INTENT 後注入 memory_context，
        重新發布為 AGENT_INTENT_ENRICHED 給 LLMProxy。
        這避免 LLMProxy 跟 MemoryMiddleware 都收 AGENT_INTENT 時的
        re-publish 無限迴圈。
        """
        self.bus.subscribe(
            subscriber_id="llm_proxy",
            handler=self.handle_event,
            event_filter={EventType.AGENT_INTENT_ENRICHED},
        )
        logger.info("[LLMProxy] 已掛載，監聽 AGENT_INTENT_ENRICHED ✓")

    def unregister(self) -> None:
        self.bus.unsubscribe("llm_proxy")

    async def handle_event(self, event: SoulEvent) -> None:
        """接收 AGENT_INTENT，驅動完整的生成管線"""
        agent_id = event.payload.get("agent_id", event.source)
        reason = event.payload.get("reason", "unknown")
        draft = event.payload.get("draft", "")
        # ⭐ Memory Middleware 插槽：Phase 2 的 Memory Middleware 會在這裡填入記憶
        memory_context = event.payload.get("memory_context", "")

        logger.info(
            f"[LLMProxy] 收到意圖 | agent={agent_id} "
            f"reason={reason} memory={'有' if memory_context else '空'}"
        )

        # 組裝 Prompt
        session_id = event.session_id or f"session_{agent_id}"
        history = self._get_history(session_id)

        prompt = PromptContext(
            system_prompt=load_persona(agent_id),
            memory_context=memory_context,        # ← Memory Middleware 填入
            conversation_history=history,
            current_intent=self._build_intent_text(reason, draft),
        )

        # 呼叫 LLM（帶 Retry）
        generated_text = await self._complete_with_retry(
            messages=prompt.to_messages(),
            agent_id=agent_id,
            correlation_id=event.event_id,
        )

        if generated_text is None:
            return  # 錯誤已由 _complete_with_retry 上報，直接退出

        # 更新對話歷史
        self._update_history(session_id, generated_text)

        # 發布 AGENT_SPEAK
        speak_event = SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source=agent_id,
            target="broadcast",
            priority=EventPriority.NORMAL,
            session_id=session_id,
            correlation_id=event.correlation_id or event.event_id,
            payload={
                "text": generated_text,
                "agent_id": agent_id,
                "reason": reason,
                "tts_enabled": True,
                "action_tags": [],  # Phase 3 升級：由 LLM 解析動作標籤
            },
        )
        await self.bus.publish(speak_event)
        logger.info(
            f"[LLMProxy] 生成完成 | agent={agent_id} "
            f"text='{generated_text[:40]}...'"
        )

    # ── 工具函數 ──────────────────────────────

    def _build_intent_text(self, reason: str, draft: str) -> str:
        """將意圖原因轉換為自然語言提示"""
        prompts = {
            "silence_timeout": (
                "你已經好一段時間沒有說話了。現在是你主動開口的時機。"
                f"{'你可以從這個想法延伸：' + draft if draft else '說一句符合你個性的話。'}"
            ),
            "schedule": f"現在有一個預排的話題想聊。{draft}",
            "user_message": draft or "請回應使用者的最新訊息。",
        }
        return prompts.get(reason, draft or "請說一句符合你個性的話。")

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        """取得並截斷對話歷史（防 context 超長）"""
        history = self._history.get(session_id, [])
        # 保留最近 N 輪（每輪 = user + assistant，所以是 N*2 條訊息）
        max_msgs = self.max_history_turns * 2
        return history[-max_msgs:] if len(history) > max_msgs else history

    def _update_history(self, session_id: str, assistant_text: str) -> None:
        """將生成結果加入歷史（Phase 2 升級為持久化）"""
        if session_id not in self._history:
            self._history[session_id] = []
        self._history[session_id].append(
            {"role": "assistant", "content": assistant_text}
        )

    async def _complete_with_retry(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        correlation_id: str,
    ) -> Optional[str]:
        """
        指數退避 Retry。
        第 1 次失敗等 1s，第 2 次等 2s，第 3 次等 4s。
        全部失敗後發布 SYSTEM_ERROR，回傳 None。
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                result = await self.backend.complete(
                    messages=messages,
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                if attempt > 0:
                    logger.info(
                        f"[LLMProxy] 第 {attempt + 1} 次重試成功 | agent={agent_id}"
                    )
                return result

            except httpx.HTTPStatusError as e:
                last_error = e
                # 429 Rate Limit 和 5xx 才重試；4xx 其他錯誤直接放棄
                if e.response.status_code in (429, 500, 502, 503, 504):
                    wait = 2 ** attempt
                    logger.warning(
                        f"[LLMProxy] HTTP {e.response.status_code}，"
                        f"{wait}s 後重試（{attempt + 1}/{self.max_retries}）"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"[LLMProxy] HTTP {e.response.status_code}，"
                        f"不重試直接放棄 | agent={agent_id}"
                    )
                    break

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    f"[LLMProxy] 網路錯誤 {type(e).__name__}，"
                    f"{wait}s 後重試（{attempt + 1}/{self.max_retries}）"
                )
                await asyncio.sleep(wait)

            except Exception as e:
                last_error = e
                logger.error(
                    f"[LLMProxy] 未預期錯誤 | {type(e).__name__}: {e}",
                    exc_info=True,
                )
                break

        # 全部重試失敗，上報 SYSTEM_ERROR
        await self.bus.publish(
            SoulEvent(
                event_type=EventType.SYSTEM_ERROR,
                source="llm_proxy",
                target="broadcast",
                priority=EventPriority.CRITICAL,
                correlation_id=correlation_id,
                payload={
                    "module": "llm_proxy",
                    "error_type": type(last_error).__name__ if last_error else "Unknown",
                    "message": str(last_error),
                    "agent_id": agent_id,
                },
            )
        )
        return None
