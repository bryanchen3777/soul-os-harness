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
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx  # 使用 httpx 做非同步 HTTP，避免 requests 阻塞事件迴圈

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

logger = logging.getLogger("soul_os.llm_proxy")

# ── 對話歷史持久化 ──────────────────────────────────
CONV_DIR = Path("data/conversations")
CONV_DIR.mkdir(parents=True, exist_ok=True)
MAX_PERSIST = 20      # 每份 history 最大條數（20 輪 ≈ 40 條）
MAX_GROUP = 20        # 群聊 history 最大條數
MAX_PRIVATE = 20      # 私聊 history 最大條數
MAX_GROUP_SUMMARY = 10  # 私聊注入時的群聊摘要條數

_GROUP_FILE = CONV_DIR / "group_chat.json"


def _group_path(agent_id: str) -> Path:
    return CONV_DIR / f"{agent_id}_private.json"


def _load_group() -> List[Dict[str, Any]]:
    if _GROUP_FILE.exists():
        try:
            return json.loads(_GROUP_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_group(history: List[Dict[str, Any]]) -> None:
    trimmed = history[-MAX_GROUP:]
    _GROUP_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_private(agent_id: str) -> List[Dict[str, str]]:
    path = _group_path(agent_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_private(agent_id: str, history: List[Dict[str, str]]) -> None:
    path = _group_path(agent_id)
    trimmed = history[-MAX_PRIVATE:]
    path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_group(speaker: str, content: str, is_private: bool = False) -> None:
    """寫入群聊 history（所有 agent 共享）"""
    history = _load_group()
    history.append({"role": "assistant", "content": content,
                    "speaker": speaker, "is_private": is_private})
    _save_group(history)


def _append_group_user(speaker: str, content: str) -> None:
    """寫入群聊 user 訊息"""
    history = _load_group()
    history.append({"role": "user", "content": content, "speaker": speaker})
    _save_group(history)


def _session_key(agent_id: str) -> str:
    """固定 session key，確保重啟後 history 能正確對應同一個 agent"""
    return f"session_{agent_id}"


def _append_private_history(agent_id: str, role: str, content: str) -> None:
    """寫入私聊 history"""
    history = _load_private(agent_id)
    history.append({"role": role, "content": content})
    _save_private(agent_id, history)


def _build_messages_group(
    agent_id: str,
    soul: str,
    current_input: str,
    memory_context: str,
) -> List[Dict[str, str]]:
    """
    群聊模式的 messages 組裝：
    [system: SOUL] + [conversation_history: 群聊 20 條] + [user: 當前訊息]
    """
    group = _load_group()
    messages: List[Dict[str, str]] = []

    # system prompt
    system_parts = [soul.strip()]
    if memory_context.strip():
        system_parts.append(f"\n你記得以下這些事情：\n{memory_context.strip()}")
    messages.append({"role": "system", "content": "\n".join(system_parts)})

    # 群聊歷史（過濾 is_private）
    for m in group[-MAX_GROUP:]:
        if m.get("is_private"):
            continue
        if m["speaker"] == "bryan":
            messages.append({"role": "user", "content": m["content"]})
        elif m["speaker"] == agent_id:
            messages.append({"role": "assistant", "content": m["content"]})
        else:
            # 其他 Agent 的話，寫進 system 讓 LLM 知道上下文
            messages.append({
                "role": "system",
                "content": f"（{m['speaker']} 說：{m['content']}）"
            })

    if current_input:
        messages.append({"role": "user", "content": current_input})
    return messages


def _build_messages_private(
    agent_id: str,
    soul: str,
    current_input: str,
    memory_context: str,
) -> List[Dict[str, str]]:
    """
    私聊模式的 messages 組裝：
    [system: SOUL] + [system: 群聊摘要 10 條] + [私聊歷史 20 條] + [user: 當前訊息]
    """
    messages: List[Dict[str, str]] = []

    # system prompt（含記憶）
    system_parts = [soul.strip()]
    if memory_context.strip():
        system_parts.append(f"\n你記得以下這些事情：\n{memory_context.strip()}")
    messages.append({"role": "system", "content": "\n".join(system_parts)})

    # 群聊摘要（最近 10 條，排除 is_private）
    group = [m for m in _load_group()[-MAX_GROUP_SUMMARY * 2:] if not m.get("is_private")]
    if group:
        summary = "\n".join(f"{m['speaker']}: {m['content']}" for m in group[-MAX_GROUP_SUMMARY:])
        messages.append({
            "role": "system",
            "content": f"最近的群聊記錄：\n{summary}"
        })

    # 私聊歷史
    private = _load_private(agent_id)[-MAX_PRIVATE:]
    for m in private:
        messages.append({"role": m["role"], "content": m["content"]})

    if current_input:
        messages.append({"role": "user", "content": current_input})
    return messages


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
    chrono_context   : HeartbeatEngine 注入的時間感知區塊（Phase 3.5 填入）
    conversation_history : 近期對話紀錄（來自 session 快取）
    current_intent   : 當前要處理的意圖（來自 AGENT_INTENT payload）
    """
    system_prompt: str
    memory_context: str = ""          # ⭐ Phase 2 插槽：Memory Middleware 填入
    chrono_context: str = ""          # ⭐ Phase 3.5 插槽：HeartbeatEngine 填入
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
        │                             │
        │ [時間感知]（若有）            │  ← HeartbeatEngine 注入（Phase 3.5）
        │ [CHRONO_SOCIAL_CONTEXT v2.2]│
        │ ...                         │
        └─────────────────────────────┘
        """
        system_parts = [self.system_prompt.strip()]

        if self.memory_context.strip():
            system_parts.append(
                f"\n你記得以下這些事情：\n{self.memory_context.strip()}"
            )

        # Phase 3.5：chrono 時間感知區塊直接貼（render_temporal_block 輸出已是格式化字串）
        if self.chrono_context.strip():
            system_parts.append(f"\n{self.chrono_context.strip()}")

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
        async with httpx.AsyncClient(timeout=120.0) as client:
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

        async with httpx.AsyncClient(timeout=120.0) as client:
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
            # content 可能是 list [{type:"text",...}, {type:"thinking",...}] 或
            #         None（只有 thinking、沒 text 輸出，常見於 reasoning 預算吃滿）
            content_blocks = data.get("content") or []
            text = ""
            for block in content_blocks:
                if block.get("type") == "text" and block.get("text"):
                    text = block["text"].strip()
                    break
            if not text:
                # fallback：把第一個 thinking 內容當 reply（最差也比空好）
                for block in content_blocks:
                    if block.get("type") == "thinking" and block.get("thinking"):
                        logger.warning(f"[ClaudeBackend] 無 text block，回傳 thinking | stop_reason={data.get('stop_reason')}")
                        text = block["thinking"].strip()
                        break
                if not text:
                    logger.warning(f"[ClaudeBackend] 無任何 block，回傳空 | data keys={list(data.keys())} content={data.get('content')}")
                    return ""
            return text


# ─────────────────────────────────────────────
# 3. Agent 人格載入器（從 Hermes SOUL.md）
# ─────────────────────────────────────────────

import os
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

# Hermes profiles 目錄（從環境變數讀，方便部署到其他機器）
HERMES_PROFILES_DIR = os.getenv(
    "HERMES_PROFILES_DIR",
    r"C:\Users\bbfcc\AppData\Local\hermes\profiles"
)

# agent_id → profiles 目錄下的子資料夾名稱
AGENT_PROFILE_MAP: Dict[str, str] = {
    "agent_yua":    "yua",
    "agent_ruka":   "ruka",
    "agent_akane":  "akane",
    "agent_aoi":    "aoi",
    "agent_mahiru": "mahiru",
    "agent_mai":    "mai",
    "agent_miku":   "miku",
    "agent_ram":    "ram",
    "agent_rem":    "rem",
    "agent_yamada": "yamada",
}

# Soul OS 本地人格目錄（優先於 Hermes profiles）
# 路徑：soul-os-harness/src/llm/proxy.py → parent.parent.parent = soul-os-harness/
SOUL_OS_PERSONAS_DIR = Path(__file__).parent.parent.parent / "personas"

# Soul OS 環境覆蓋說明（附在人格內容尾，避免 Hermes tool 指令干扰）
SOUL_OS_OVERRIDE = """
---
[Soul OS 環境說明]
你現在運行於 Soul OS 框架。記憶由系統自動管理，不需要呼叫任何 tool。

重要規則：
1. 直接說話，不要描述動作（如「*看著你*」）
2. 對話歷史中，使用者說的話已經標記為「user」，你的回覆標記為「assistant」
3. 看到使用者的訊息時，直接以角色身份回應，不要假裝或扮演
4. 回覆保持簡短有力，不超過 2 句
5. 如果沒有具體問題或話題，保持沉默或給出簡短回應
---
"""

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

    優先順序：
    1. Soul OS 本地 personas/{agent_id}.md（專用於 Soul OS）
    2. DEFAULT_PERSONAS（簡單 fallback）

    不再讀取 Hermes profiles，因為那些包含 tool 指令不適用於 Soul OS。
    """
    # 🔴 優先：Soul OS 本地 personas/ 目錄
    local_persona = SOUL_OS_PERSONAS_DIR / f"{agent_id}.md"
    if local_persona.exists():
        try:
            content = local_persona.read_text(encoding="utf-8").strip()
            if content:
                logger.info(f"[Persona] {agent_id} 載入 {local_persona}")
                return content + SOUL_OS_OVERRIDE
        except Exception as e:
            logger.warning(f"[Persona] 讀取 {local_persona} 失敗：{e}")

    # Fallback 到 DEFAULT_PERSONAS
    logger.info(f"[Persona] {agent_id} 使用 DEFAULT_PERSONAS")
    persona = DEFAULT_PERSONAS.get(
        agent_id,
        f"你是 {agent_id}，一個有獨特個性的 AI 角色。"
    )
    return persona + SOUL_OS_OVERRIDE


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
        max_tokens: int = 3000,  # MiniMax-M2.7 reasoning 預算很重，3000 確保 text 一定生成
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
        self._history: Dict[str, List[Dict[str, Any]]] = {}

        # 啟動時從磁碟載入記憶
        self._group_history = _load_group()
        for agent_id in ("agent_yua", "agent_ruka", "agent_akane"):
            self._history[_session_key(agent_id)] = _load_private(agent_id)
        logger.info(
            f"[LLMProxy] 載入 group={len(self._group_history)} 條, "
            f"private histories loaded"
        )

    def register(self) -> None:
        """向 Event Bus 註冊，開始監聽 SPEAKER_TOKEN_GRANTED

        Phase 2.0：訂閱改為 AGENT_INTENT_ENRICHED。
        MemoryMiddleware 收到 AGENT_INTENT 後注入 memory_context，
        重新發布為 AGENT_INTENT_ENRICHED 給 LLMProxy。
        這避免 LLMProxy 跟 MemoryMiddleware 都收 AGENT_INTENT 時的
        re-publish 無限迴圈。

        Phase 4：訂閱再改為 SPEAKER_TOKEN_GRANTED。
        SpeakerTokenManager 收到 AGENT_INTENT_ENRICHED 後仲裁，
        授權後 re-publish 為 SPEAKER_TOKEN_GRANTED。
        LLMProxy 收到才真正生產，避免多 Agent 同時搶話。
        """
        self.bus.subscribe(
            subscriber_id="llm_proxy",
            handler=self.handle_event,
            event_filter={EventType.SPEAKER_TOKEN_GRANTED},
        )
        logger.info("[LLMProxy] 已掛載，監聽 SPEAKER_TOKEN_GRANTED ✓")

    def unregister(self) -> None:
        self.bus.unsubscribe("llm_proxy")

    async def handle_event(self, event: SoulEvent) -> None:
        """接收 SPEAKER_TOKEN_GRANTED，驅動完整的生成管線"""
        agent_id = event.payload.get("agent_id", event.source)
        reason = event.payload.get("reason", "unknown")
        draft = event.payload.get("draft", "")
        memory_context = event.payload.get("memory_context", "")

        # 從 event payload 取 mode（gateway 寫入的）
        mode = event.payload.get("mode", "group")
        user_message = draft if reason == "user_message" else ""

        logger.info(
            f"[LLMProxy] 收到意圖 | agent={agent_id} "
            f"mode={mode} reason={reason}"
        )

        # ── 寫入歷史（根據 mode）───────────────────────
        if mode == "group":
            if user_message:
                _append_group_user("bryan", user_message)
                self._group_history = _load_group()
            group_file_content = _load_group()  # 最新磁碟內容
        else:
            # 私聊：寫入自己的 private history
            if user_message:
                _append_private_history(agent_id, "user", user_message)
                self._history[_session_key(agent_id)] = _load_private(agent_id)
            # 同時在 group_chat 留一個啞標記
            if user_message:
                _append_group(
                    speaker=agent_id,
                    content=f"（{agent_id} 與 Bryan 私聊中）",
                    is_private=True,
                )
                self._group_history = _load_group()

        # ── 組裝 messages（根據 mode）─────────────────
        soul = load_persona(agent_id)
        if mode == "group":
            messages = _build_messages_group(agent_id, soul, user_message, memory_context)
        else:
            messages = _build_messages_private(agent_id, soul, user_message, memory_context)

        logger.info(f"[LLMProxy-DEBUG] agent={agent_id} mode={mode} messages={len(messages)}")
        for i, msg in enumerate(messages):
            logger.info(f"  [{i}] {msg.get('role')} {msg.get('content','')[:60]!r}...")

        # ── 呼叫 LLM ──────────────────────────────────
        generated_text = await self._complete_with_retry(
            messages=messages,
            agent_id=agent_id,
            correlation_id=event.event_id,
        )

        if generated_text is None:
            return

        # ── 寫入回應歷史 ──────────────────────────────
        if mode == "group":
            _append_group(speaker=agent_id, content=generated_text)
            self._group_history = _load_group()
        else:
            _append_private_history(agent_id, "assistant", generated_text)
            self._history[_session_key(agent_id)] = _load_private(agent_id)
            _append_group(speaker=agent_id, content=generated_text, is_private=True)
            self._group_history = _load_group()

        # ── 發布 AGENT_SPEAK ──────────────────────────
        speak_event = SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source=agent_id,
            target="broadcast",
            priority=EventPriority.NORMAL,
            session_id=_session_key(agent_id),
            correlation_id=event.correlation_id or event.event_id,
            payload={
                "text": generated_text,
                "agent_id": agent_id,
                "reason": reason,
                "mode": mode,
                "tts_enabled": True,
                "action_tags": [],
            },
        )
        await self.bus.publish(speak_event)
        logger.info(f"[LLMProxy] 生成完成 | agent={agent_id} text='{generated_text[:40]}...'")

    # ── 工具函數 ──────────────────────────────

    def _build_intent_text(self, reason: str, draft: str) -> str:
        """將意圖原因轉換為自然語言提示

        🔴 修復問題 1：確保 system prompt 結構不會混入 user content
        USER_MESSAGE 的 draft 是使用者輸入，必須明確標記為「使用者說的話」
        """
        prompts = {
            "silence_timeout": (
                "你已經好一段時間沒有說話了。現在是你主動開口的時機。"
                f"{'你可以從這個想法延伸：' + draft if draft else '說一句符合你個性的話。'}"
            ),
            "schedule": f"現在有一個預排的話題想聊。{draft}",
            "user_message": (
                # 🔴 直接給出使用者說的話，不加額外指令
                draft if draft else ""
            ),
        }
        return prompts.get(reason, draft or "請說一句符合你個性的話。")

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        """取得並截斷對話歷史（防 context 超長）"""
        history = self._history.get(session_id, [])
        # 保留最近 N 輪（每輪 = user + assistant，所以是 N*2 條訊息）
        max_msgs = self.max_history_turns * 2
        return history[-max_msgs:] if len(history) > max_msgs else history

    def _add_to_history(self, session_id: str, role: str, content: str) -> None:
        """將訊息加入歷史（自動截斷超過 MAX_HISTORY 的舊訊息），並持久化"""
        # session_id 格式是 "session_{agent_id}"
        agent_id = session_id.replace("session_", "")
        if session_id not in self._history:
            self._history[session_id] = []
        self._history[session_id].append({"role": role, "content": content})

        # 超過限制時，從最舊的開始刪（保留 system prompt，歷史不該有 system）
        max_msgs = self.max_history_turns * 2
        if len(self._history[session_id]) > max_msgs:
            self._history[session_id] = self._history[session_id][-max_msgs:]

        # 持久化到磁碟
        try:
            _save_history(agent_id, self._history[session_id])
        except Exception as e:
            logger.warning(f"[LLMProxy] 寫入歷史失敗：{e}")

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
