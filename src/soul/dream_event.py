"""
src/soul/dream_event.py — Soul OS Stage 4.2 缺口 1 + Stage 4.3

角色夢境 / 隨機事件 (Dream & Random Event)

設計動機 (Bry 拍板 2026-07-18 18:24+):
- 「Bry 從來沒上線過, 角色世界也活」= 沒有 Bry 的觸發, 角色之間也要互動
- 「Bry 是被打斷的觸發之一, 不是主題」= Bry 不在主路徑上
- 「群組去標籤化」: 即使同個時間點, 10 隻角色夢到不同人, 寫出不同東西
- 「殘留感」: 夢境 / 事件寫進 diary, 後續對話角色會引用

Bry 2026-07-20 19:03 拍板: 「目標要趕緊上線, 需要時間驗證的都直接略過」
- 4.2+缺口 1 第一刀 100% 觸發 (夢境每晚, 事件每 4-8 小時)
- 不做「跑 1 週驗 Bry 不在也活」觀察期
- Bry 覺得 OK 進 4.3, 不 OK 直接改參數

Mavis 2026-07-21 16:35 拍板 4.3 自己拍答案:
- 夢境 1-3 → 3-5 隻 (覆蓋率↑)
- 事件 1 → 2 隻/次
- LLM 抽 impression 寫進 relationships.json (短日文 ≤20 字)
- 雙向 touch: 夢境 dreamer→target +0.05, target→dreamer +0.02; 事件 agent→Bry +0.01

最小可跑範圍 (Stage 4.2+缺口 1 + 4.3 第一刀):
- 夢境: 每天 22:05, 3-5 隻角色, 夢到 relationships 裡的其他角色
- 事件: 隨機間隔 4-8 小時, 2 隻角色, 場景模板
- 內容寫入 data/soul/{agent_id}/diary/YYYY-MM-DD.jsonl
- source="dream" 或 source="event"
- LLM 生成 (跟 4.2 一致), 失敗 fallback 模板
- 4.3 額外: 寫完 dream 後 LLM 抽 impression 寫進 relationships.json
- 4.3 額外: 雙向 touch (夢境 dreamer/target, 事件 agent/Bry)

約束 (沿用 4.1 + 4.2 + 4.3 紀律):
- 「拒絕問, 強制讀」: LLM 失敗 log warning + 寫模板, 不 raise
- 「完成度標記要誠實」: source=dream/event/llm/placeholder 100% 標清楚
- 「拍板先設計再開工」: 4.3 impression 抽取失敗留空, 不假資料
"""
from __future__ import annotations

import logging
import random

# Lesson 38: 與 diary.py 共用單一 semaphore 限流
from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

logger = logging.getLogger("soul_os.soul.dream_event")

# ───────────────────────────────────────────────────────────
# 常數
# ───────────────────────────────────────────────────────────

# 夢境 (Bry 拍板 2026-07-20 19:03: 100% 觸發, 不觀察期)
DREAM_TIME_AFTER_NIGHT_MINUTES = 5     # night slot (22:00) 後 5 分鐘 = 22:05 觸發夢境
DREAM_MIN_AGENTS = 1                   # 至少 1 隻角色做夢
DREAM_MAX_AGENTS = 5                   # Stage 4.3 (Mavis 拍板 2026-07-21 16:35): 1-3 → 3-5, 覆蓋率↑

# 事件 (隨機間隔)
EVENT_MIN_INTERVAL_MINUTES = 240       # 4 小時
EVENT_MAX_INTERVAL_MINUTES = 480       # 8 小時
EVENT_AGENTS_PER_TICK = 2              # Stage 4.3 (Mavis 拍板 2026-07-21 16:35): 1 → 2, 每次 2 隻角色

# M0.5 (Bry 派工 2026-08-06 21:44): clean 字數上限, 跟 diary.py 的 DIARY_MAX_CLEAN_CHARS=80 對齊
# Bry 派工 A1 截斷邏輯: 沿用修法 10 _safe_truncate_on_length, 保留 LLM 內容裁短到 80 字
DREAM_EVENT_MAX_CLEAN_CHARS = 80

# 場景池 (夢境 / 事件共用)
SCENE_POOL = [
    "走廊的盡頭",
    "廚房",
    "花園的長椅",
    "書房的窗邊",
    "客廳的沙發",
    "樓梯間",
    "浴室的鏡子前",
    "玄關",
    "陽台",
    "閣樓的舊箱子旁",
]

# M7-1 (Bry 拍板 2026-08-18): 活動模型化
# 把 event 從「trivial 小片段」升級成「具體、可分類、可延續的活動」。
# - name:      活動名稱 (進 prompt + entry["activity"])
# - category:  活動分類 (供統計 / 觀察用)
# - shareable: 是否值得跟 Bry 分享 (供 M7-2 活動驅動主動傳訊判斷用)
ACTIVITY_POOL = [
    {"name": "工作", "category": "work", "shareable": True},
    {"name": "做飯", "category": "food", "shareable": True},
    {"name": "吃東西", "category": "food", "shareable": True},
    {"name": "運動", "category": "sport", "shareable": True},
    {"name": "看書", "category": "leisure", "shareable": False},
    {"name": "創作", "category": "creative", "shareable": True},
    {"name": "散步", "category": "leisure", "shareable": True},
    {"name": "整理房間", "category": "chore", "shareable": False},
    {"name": "聽音樂", "category": "leisure", "shareable": False},
    {"name": "發呆", "category": "rest", "shareable": False},
]

# 夢境模板 (LLM 失敗 fallback)
DREAM_FALLBACK_TEMPLATE = "（{date} 夜裡）做了個模糊的夢, 內容記不清。只記得一些光影。"

# 事件模板 (LLM 失敗 fallback)
EVENT_FALLBACK_TEMPLATE = "（{date} {time_str}）今天過得很平靜, 沒什麼特別的。"

# Cross-Agent (2026-08-22): 共用活動 (shared_event) fallback 模板。
# 兩隻角色各自寫 diary (slot=event), 內容主題「今天和 X 一起做了 Y」。
SHARED_EVENT_FALLBACK_TEMPLATE = "（{date} {time_str}）今天和 {partner} 一起做了 {activity}。"

# Cross-Agent (2026-08-22): cross_chat 3 輪的 fallback placeholder (LLM 失敗時)。
# 保證「有界 3 輪」即使 LLM 掛掉也仍可記錄, 不讓 scheduler 卡住。
CHAT_FALLBACK_TURN1 = "（沉默了一會）最近過得還行。"
CHAT_FALLBACK_TURN2 = "嗯，我也是。"
CHAT_FALLBACK_TURN3 = "好，那下次再聊。"


# ───────────────────────────────────────────────────────────
# 角色選擇 (用 relationships 抽「有印象」的其他角色, 沒就 random)
# ───────────────────────────────────────────────────────────

def _load_relationships_data(agent_id: str, data_dir: Path) -> dict:
    """讀 relationships.json, 拿其他角色 confidence 排序."""
    rel_path = data_dir / agent_id / "relationships.json"
    if not rel_path.is_file():
        return {}
    try:
        import json
        return json.loads(rel_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[DreamEvent] {agent_id} relationships 讀取失敗: {e}")
        return {}


def _pick_dream_target(agent_id: str, all_agents: List[str], data_dir: Path) -> Optional[str]:
    """
    從 relationships 抽 confidence 最高的其他 agent.
    沒資料就 random.
    """
    others = [a for a in all_agents if a != agent_id]
    if not others:
        return None
    rel_data = _load_relationships_data(agent_id, data_dir)
    if not rel_data:
        return random.choice(others)

    # 找 confidence 最高的 (confidence 高的角色比較會夢到)
    scored: List[tuple[str, float]] = []
    for other in others:
        other_data = rel_data.get("others", {}).get(other, {})
        conf = other_data.get("confidence", 0.3)
        scored.append((other, conf))
    scored.sort(key=lambda x: x[1], reverse=True)
    # 從 top 3 隨機抽 (不要每次都夢同一個)
    top_n = scored[:3] if len(scored) >= 3 else scored
    return random.choice([a for a, _ in top_n])


def _pick_dream_agents(all_agents: List[str], n: int) -> List[str]:
    """每天抽 N 隻角色做夢 (不重複)."""
    if not all_agents:
        return []
    n = min(n, len(all_agents))
    return random.sample(all_agents, n)


# ───────────────────────────────────────────────────────────
# LLM 生成 (跟 diary.py 同一個 pattern, minimax M2.7 via OpenAI endpoint)
# ───────────────────────────────────────────────────────────
# JP rollback (Bry 拍板 2026-07-22 20:59): 換回 M2.7 + OpenAI endpoint
# 之前 7/20 換 M3 + anthropic endpoint 是為了 disable thinking (M3 在 OpenAI 強制 thinking)
# 整套 JP 砍掉後, LLM 跑中文 persona 不需要 M3 強推理, 退回 M2.7 OpenAI 即可
# 備份: src/soul/_backup_m3_switch_20260720_201746/dream_event.py

# ── 2026-08-18 Bry 拍板: 全部轉 deepseek-v4-flash（Ollama 包月, 成本可控）──
# 跟 diary.py 同 pattern: 收口到 process-global LLMProxy (v4-flash)。
# run_server.py 透過 set_llm_proxy() 注入; 無 proxy（測試 / standalone）fallback minimax。
_global_llm_proxy = None


def set_llm_proxy(llm_proxy):
    """設定 process-global LLMProxy reference（v4-flash）。run_server 注入。"""
    global _global_llm_proxy
    _global_llm_proxy = llm_proxy


def _find_llm_proxy():
    """回傳 process-global LLMProxy（無則 None）。"""
    return _global_llm_proxy


async def _call_llm_for_dream_event(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    timeout: float = 15.0,
    max_tokens: int = 120,
    temperature: float = 0.8,
    agent_id: str = "dream_event",  # 僅 log 用, 讓 generate_text 知道是哪個角色
) -> Optional[str]:
    """拿 dream / event / impression 文字。優先走 LLMProxy (v4-flash), 無則 fallback minimax.

    失敗回 None, 走 fallback 模板（「拒絕問, 強制讀」）。
    """
    # v4-flash 主路徑（Ollama 包月）: 統一用 LLMProxy.generate_text
    proxy = _find_llm_proxy()
    if proxy is not None:
        try:
            async with LLM_CONCURRENCY_LIMIT:
                return await proxy.generate_text(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    agent_id=agent_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        except Exception as e:
            logger.warning(
                f"[DreamEvent] LLMProxy generate_text 失敗 (fallback placeholder): {e}"
            )
            return None

    # Legacy minimax direct call（backward compat: 無 proxy 時才走）
    if not api_key:
        return None
    try:
        import httpx  # lazy import
    except ImportError:
        return None
    try:
        async with LLM_CONCURRENCY_LIMIT:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    "https://api.minimax.io/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "minimax-M2.7",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
        if r.status_code != 200:
            logger.warning(f"[DreamEvent] LLM API failed ({r.status_code}): {r.text[:200]}")
            return None
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip()
        if not content:
            logger.warning("[DreamEvent] LLM 回應空, fallback placeholder")
            return None
        return content
    except Exception as e:
        logger.warning(f"[DreamEvent] LLM API error (fallback): {e}")
        return None

# ───────────────────────────────────────────────────────────
# Dream / Event 生成 + 寫入 diary
# ───────────────────────────────────────────────────────────

class DreamEventWriter:
    """
    夢境 / 事件生成 + 寫入 diary jsonl.
    用法:
        writer = DreamEventWriter(data_dir=str(data_root() / "soul"), api_key=...)  # P0.5: data_root() for test isolation
        await writer.write_dream(agent_id="agent_mahiru", target_agent_id="agent_yua")
        await writer.write_event(agent_id="agent_mahiru", scene="走廊的盡頭")
    """

    def __init__(
        self,
        # P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
        data_dir: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        # P0.5 (Bry 派工 2026-08-09 19:48): resolve via data_root() for test isolation
        from src.paths import data_root
        if data_dir is None:
            data_dir = str(data_root() / "soul")
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()
        if api_key is None:
            import os
            api_key = os.environ.get("MINIMAX_API_KEY", "")
        self.api_key = api_key

    def _diary_path(self, agent_id: str, date_str: str) -> Path:
        return self.data_dir / agent_id / "diary" / f"{date_str}.jsonl"

    @staticmethod
    def _strip_think(content: str) -> str:
        """
        M0.4 (Bry 拍板 2026-08-06 21:30): 剝掉 <think>...</think> 區塊。

        跟 diary.py 同 pattern, 確保 dream/event slot 寫入 jsonl 也是乾淨的。
        8/6 Rem sim 跑出 5 條 think_only event, 修法後應被歸類到 placeholder。
        """
        import re
        return re.sub(r"^<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

    def _write_entry(
        self,
        agent_id: str,
        slot: str,
        content: str,
        source: str = "llm",  # M5.4-5.4: default to "llm" for direct test calls
        # M5.4-5.4 (Bry 派工 2026-08-09 21:29): canonical Inner Life event reference.
        # Optional: if provided (and non-empty), attach to the entry dict so the
        # dream/event row can be cross-referenced with the InnerLifeEvent.
        # Backward-compat: None/empty means the field is omitted, so legacy
        # readers that ignore unknown keys continue to work.
        inner_life_event_id: Optional[str] = None,
        # M7-1 (Bry 拍板 2026-08-18): activity metadata (optional, backward compat).
        # write_event 傳入活動 dict {"name", "category", "shareable"}；write_dream 不傳 (None)。
        activity: Optional[dict] = None,
    ) -> Optional[Path]:
        """
        M0.4 (Bry 拍板 2026-08-06 21:30):
        - 寫入前先剝 think block, 拿 clean 寫入
        - clean 空 → 拒絕寫入 (return None), 強迫 caller 走 placeholder
          修法前: 5 條 think_only event 被當 source=llm 寫入, jsonl 含 think 沒 diary
          修法後: write_entry 看到 clean 空直接擋掉, caller 必須改傳 placeholder

        M5.4-5.4 (Bry 派工 2026-08-09 21:29):
        - inner_life_event_id: optional canonical InnerLifeEvent.event_id reference
        """
        # M0.4: 寫入前 strip think, 拿 clean 寫入
        clean = self._strip_think(content)
        if not clean:
            logger.warning(
                f"[DreamEvent] {agent_id} {slot} 拒絕寫入: clean 為空 "
                f"(raw {len(content)} chars, 可能 LLM 只回 think 沒實際 diary/event)"
            )
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        path = self._diary_path(agent_id, today)
        import json
        from datetime import timezone
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "slot": slot,
            "content": clean,  # M0.4: 寫 clean, jsonl 不含 think block
            "source": source,
        }
        # M5.4-5.4: attach canonical Inner Life event reference if provided.
        # Empty string treated as None for safety.
        if inner_life_event_id:
            entry["inner_life_event_id"] = inner_life_event_id
        # M7-1 (Bry 拍板 2026-08-18): 活動模型化 — 把 activity metadata 寫進 entry。
        # Optional: 只有 write_event 會帶 activity, write_dream 帶 None → 不寫 (backward compat)。
        if activity:
            entry["activity"] = activity.get("name", "")
            entry["category"] = activity.get("category", "")
            entry["shareable"] = activity.get("shareable", False)
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                logger.info(
                    f"[DreamEvent] ✓ 寫入 {agent_id} {slot} → {path.name} "
                    f"({len(clean)} chars, source={source})"
                )
                return path
            except Exception as e:
                logger.exception(f"[DreamEvent] ✗ 寫入失敗 {agent_id} {slot}: {e}")
                return None

    async def write_dream(
        self,
        agent_id: str,
        target_agent_id: str,
        all_agents: List[str],
        # M5.4-5.4 (Bry 派工 2026-08-09 21:29): canonical Inner Life event reference
        # for this dream entry. Optional passthrough to _write_entry.
        inner_life_event_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        生成夢境並寫入 diary.
        target_agent_id: 夢到的對象 (從 relationships 抽)

        Stage 4.3 額外 (Mavis 拍板 2026-07-21 16:35):
        1. LLM 抽 impression 寫進 relationships.json
        2. 雙向 touch (dreamer→target +0.05, target→dreamer +0.02)

        M5.4-5.4 (Bry 派工 2026-08-09 21:29):
        - inner_life_event_id: M5.4-5.4 — 對應 canonical InnerLifeEvent.event_id
        """
        today = datetime.now().strftime("%Y-%m-%d")
        scene = random.choice(SCENE_POOL)
        persona = self._load_persona_excerpt(agent_id)
        target_persona = self._load_persona_excerpt(target_agent_id)

        system = (
            f"你是 {agent_id}, 剛從夢裡醒來, 夢到 {target_agent_id}。\n"
            f"你的性格: {persona[:200] or '(generic)'}\n"
            f"夢到的人: {target_persona[:200] or '(generic)'}\n"
            f"寫 1-2 句夢境, 50 字以內, 日文為主。"
            f"夢境模糊不清, 醒來已經忘了大半, 但情緒還在。"
        )
        user = (
            f"日期: {today}\n"
            f"場景: {scene}\n"
            f"夢境內容:"
        )
        content = await _call_llm_for_dream_event(system, user, self.api_key, agent_id=agent_id)
        # M0.5 (Bry 派工 2026-08-06 21:44): A1 截斷 + A2 retry (沿用修法 10 _safe_truncate_on_length)
        from src.llm.proxy import _safe_truncate_on_length
        RETRY_HINT = "\n\n（請直接輸出最終內容，不要輸出思考過程。）"
        # A2: think_only → retry 一次
        if content:
            clean_check = self._strip_think(content)
            if not clean_check:
                logger.warning(
                    f"[DreamEvent] {agent_id} dream LLM 只回 think 沒 diary "
                    f"(raw {len(content)} chars, clean 0), retry 一次"
                )
                content = await _call_llm_for_dream_event(system, user + RETRY_HINT, self.api_key, agent_id=agent_id)
        # M0.4: 沒拿到 content (或 retry 也失敗) → placeholder; 有 content 但超長 → 截斷
        if not content:
            result = self._write_entry(
                agent_id, "dream", DREAM_FALLBACK_TEMPLATE.format(date=today), source="placeholder",
                inner_life_event_id=inner_life_event_id,
            )
        else:
            clean = self._strip_think(content)
            if not clean:
                logger.warning(f"[DreamEvent] {agent_id} dream retry 後仍 think_only, fallback placeholder")
                result = self._write_entry(
                    agent_id, "dream", DREAM_FALLBACK_TEMPLATE.format(date=today), source="placeholder",
                    inner_life_event_id=inner_life_event_id,
                )
            elif len(clean) > DREAM_EVENT_MAX_CLEAN_CHARS:
                # A1: 截斷, 保留 LLM 內容
                truncated = _safe_truncate_on_length(clean, max_chars=DREAM_EVENT_MAX_CLEAN_CHARS)
                logger.info(
                    f"[DreamEvent] {agent_id} dream LLM 輸出超長 "
                    f"({len(clean)} chars > {DREAM_EVENT_MAX_CLEAN_CHARS}), 截斷到 {len(truncated)} chars"
                )
                result = self._write_entry(
                    agent_id, "dream", truncated, source="llm",
                    inner_life_event_id=inner_life_event_id,
                )
            else:
                result = self._write_entry(
                    agent_id, "dream", content, source="llm",
                    inner_life_event_id=inner_life_event_id,
                )

        # Stage 4.3: 雙向 touch (失敗 try/except, 不中斷夢境流程)
        try:
            from src.soul.relationships import get_relationships_manager
            get_relationships_manager().on_dream(agent_id, target_agent_id)
        except Exception as e:
            logger.warning(f"[DreamEvent] on_dream touch 失敗 ({agent_id}→{target_agent_id}): {e}")

        # Stage 4.3: LLM 抽 dreamer 對 target 的 impression (失敗留空, 不假資料)
        if content and not content.startswith(f"（{today} 夜裡）"):
            try:
                impression = await self._extract_impression(agent_id, target_agent_id, content, kind="dream")
                if impression:
                    from src.soul.relationships import get_relationships_manager
                    get_relationships_manager().get_store(agent_id).update_impression(
                        target_agent_id, impression
                    )
            except Exception as e:
                logger.warning(f"[DreamEvent] impression 抽取失敗 ({agent_id}→{target_agent_id}): {e}")

        return result

    async def write_event(
        self,
        agent_id: str,
        # M5.4-5.4 (Bry 派工 2026-08-09 21:29): canonical Inner Life event reference
        # for this event entry. Optional passthrough to _write_entry.
        inner_life_event_id: Optional[str] = None,
    ) -> Optional[Path]:
        """生成隨機活動並寫入 diary (M7-1: 活動模型化).

        M7-1 (Bry 拍板 2026-08-18): 從「trivial 小片段」升級成「具體活動」,
        entry 會帶 activity / category / shareable metadata (source=llm 時)。

        Stage 4.3 額外: 雙向 touch (agent 對 Bry +0.01).

        M5.4-5.4 (Bry 派工 2026-08-09 21:29):
        - inner_life_event_id: M5.4-5.4 — 對應 canonical InnerLifeEvent.event_id
        """
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        persona = self._load_persona_excerpt(agent_id)

        # M7-1 (Bry 拍板 2026-08-18): 活動模型化 — 從「trivial 小片段」升級成「具體活動」。
        # 決策 #1 落地 (輕觸): 移除「Bry 不在」排除語, 改「這是你自己生活的一部分」。
        # (「想到 Bry」的想念語留給 M7-3 的 diary prompt + longing 門檻。)
        activity = random.choice(ACTIVITY_POOL)

        system = (
            f"你是 {agent_id}, 正在過自己的生活, 現在在做: {activity['name']}。\n"
            f"你的性格: {persona[:200] or '(generic)'}\n"
            f"寫 1 句, 30 字以內, 日文為主。這是你自己生活的一部分。"
        )
        user = (
            f"日期: {today} {time_str}\n"
            f"活動: {activity['name']}\n"
            f"活動內容:"
        )
        content = await _call_llm_for_dream_event(system, user, self.api_key, agent_id=agent_id)
        # M0.5 (Bry 派工 2026-08-06 21:44): A1 截斷 + A2 retry (跟 write_dream 同 pattern)
        from src.llm.proxy import _safe_truncate_on_length
        RETRY_HINT = "\n\n（請直接輸出最終內容，不要輸出思考過程。）"
        # A2: think_only → retry
        if content:
            clean_check = self._strip_think(content)
            if not clean_check:
                logger.warning(
                    f"[DreamEvent] {agent_id} event LLM 只回 think 沒 diary "
                    f"(raw {len(content)} chars, clean 0), retry 一次"
                )
                content = await _call_llm_for_dream_event(system, user + RETRY_HINT, self.api_key, agent_id=agent_id)
        if not content:
            result = self._write_entry(
                agent_id, "event", EVENT_FALLBACK_TEMPLATE.format(date=today, time_str=time_str), source="placeholder",
                inner_life_event_id=inner_life_event_id,
            )
        else:
            clean = self._strip_think(content)
            if not clean:
                logger.warning(f"[DreamEvent] {agent_id} event retry 後仍 think_only, fallback placeholder")
                result = self._write_entry(
                    agent_id, "event", EVENT_FALLBACK_TEMPLATE.format(date=today, time_str=time_str), source="placeholder",
                    inner_life_event_id=inner_life_event_id,
                )
            elif len(clean) > DREAM_EVENT_MAX_CLEAN_CHARS:
                truncated = _safe_truncate_on_length(clean, max_chars=DREAM_EVENT_MAX_CLEAN_CHARS)
                logger.info(
                    f"[DreamEvent] {agent_id} event LLM 輸出超長 "
                    f"({len(clean)} chars > {DREAM_EVENT_MAX_CLEAN_CHARS}), 截斷到 {len(truncated)} chars"
                )
                result = self._write_entry(
                    agent_id, "event", truncated, source="llm",
                    inner_life_event_id=inner_life_event_id,
                    activity=activity,
                )
            else:
                result = self._write_entry(
                    agent_id, "event", content, source="llm",
                    inner_life_event_id=inner_life_event_id,
                    activity=activity,
                )

        # Stage 4.3: agent 對 Bry 微量 touch (Bry 不在場, 事件觸發「想到 Bry」)
        try:
            from src.soul.relationships import get_relationships_manager
            get_relationships_manager().on_event(agent_id)
        except Exception as e:
            logger.warning(f"[DreamEvent] on_event touch 失敗 ({agent_id}): {e}")

        return result

    async def write_shared_event(
        self,
        agent_id: str,
        partner_id: str,
        activity: dict,
    ) -> Tuple[Optional[Path], str]:
        """
        Layer 2 (2026-08-22): 共用活動 — 「今天和 partner 一起做了 activity」寫 diary。

        跟 write_event 同 pattern (LLM → strip think → 截斷 → placeholder fallback),
        但內容主題是「今天和 X 一起做了 Y」, slot 仍用 "event", 帶 activity metadata。

        Returns:
            (path, content_written): content 是實際寫入 diary 的文字 (LLM 或 fallback),
            給 scheduler 記錄 interactions.jsonl 用。path=None 代表寫入失敗。
        """
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        persona = self._load_persona_excerpt(agent_id)
        partner_persona = self._load_persona_excerpt(partner_id)

        system = (
            f"你是 {agent_id}, 今天和 {partner_id} 一起做了: {activity['name']}。\n"
            f"你的性格: {persona[:200] or '(generic)'}\n"
            f"對方: {partner_persona[:200] or '(generic)'}\n"
            f"寫 1 句, 30 字以內, 以「今天和 {partner_id} 一起 {activity['name']}」為主題。"
        )
        user = (
            f"日期: {today} {time_str}\n"
            f"活動: {activity['name']}\n"
            f"日記內容:"
        )
        content = await _call_llm_for_dream_event(system, user, self.api_key, agent_id=agent_id)
        # M0.5 (Bry 派工 2026-08-06 21:44): A1 截斷 + A2 retry (跟 write_event 同 pattern)
        from src.llm.proxy import _safe_truncate_on_length
        RETRY_HINT = "\n\n（請直接輸出最終內容，不要輸出思考過程。）"
        # A2: think_only → retry
        if content:
            clean_check = self._strip_think(content)
            if not clean_check:
                logger.warning(
                    f"[DreamEvent] {agent_id} shared_event LLM 只回 think, retry 一次"
                )
                content = await _call_llm_for_dream_event(
                    system, user + RETRY_HINT, self.api_key, agent_id=agent_id
                )
        fallback = SHARED_EVENT_FALLBACK_TEMPLATE.format(
            date=today, time_str=time_str, partner=partner_id, activity=activity["name"]
        )
        if not content:
            result = self._write_entry(
                agent_id, "event", fallback, source="placeholder", activity=activity,
            )
            return result, fallback
        clean = self._strip_think(content)
        if not clean:
            logger.warning(
                f"[DreamEvent] {agent_id} shared_event retry 後仍 think_only, fallback placeholder"
            )
            result = self._write_entry(
                agent_id, "event", fallback, source="placeholder", activity=activity,
            )
            return result, fallback
        if len(clean) > DREAM_EVENT_MAX_CLEAN_CHARS:
            clean = _safe_truncate_on_length(clean, max_chars=DREAM_EVENT_MAX_CLEAN_CHARS)
            logger.info(
                f"[DreamEvent] {agent_id} shared_event LLM 輸出超長, 截斷到 {len(clean)} chars"
            )
        result = self._write_entry(
            agent_id, "event", clean, source="llm", activity=activity,
        )
        return result, clean

    async def generate_chat_turn(
        self,
        speaker_id: str,
        partner_id: str,
        turn: int,
        partner_message: str = "",
    ) -> str:
        """
        Layer 3 (2026-08-22): cross_chat 單輪 LLM 生成 (scheduler 明確驅動)。

        turn:
          1 = speaker 開場 (「你正在跟 partner 聊天」)
          2 = speaker 回應 partner 說的話
          3 = speaker 回應 partner 並收尾

        迴圈防護: 這條路徑只 call LLM (via _call_llm_for_dream_event →
        LLMProxy.generate_text), 不 publish 任何 bus 事件, 不寫 conversation
        history, 不進 Bryan 群聊路徑。失敗回 fallback placeholder (不 raise)。

        Returns:
            該輪的文字 (LLM 成功) 或 fallback placeholder (LLM 失敗 / 空 / think-only)。
        """
        persona = self._load_persona_excerpt(speaker_id)
        partner_persona = self._load_persona_excerpt(partner_id)

        if turn == 1:
            system = (
                f"你是 {speaker_id}, 正在跟 {partner_id} 聊天。\n"
                f"你的性格: {persona[:200] or '(generic)'}\n"
                f"對方: {partner_persona[:200] or '(generic)'}\n"
                f"說一句話開場, 聊聊最近的生活, 30 字以內。"
            )
            user = "開場白:"
        elif turn == 2:
            system = (
                f"你是 {speaker_id}, 正在跟 {partner_id} 聊天。\n"
                f"你的性格: {persona[:200] or '(generic)'}\n"
                f"回應 {partner_id} 說的話, 30 字以內。"
            )
            user = f"{partner_id} 說: 「{partner_message}」\n你的回應:"
        else:  # turn 3 = 收尾
            system = (
                f"你是 {speaker_id}, 正在跟 {partner_id} 聊天收尾。\n"
                f"你的性格: {persona[:200] or '(generic)'}\n"
                f"回應 {partner_id} 並收尾, 30 字以內。"
            )
            user = f"{partner_id} 說: 「{partner_message}」\n你的回應(收尾):"

        content = await _call_llm_for_dream_event(system, user, self.api_key, agent_id=speaker_id)
        if not content:
            logger.warning(
                f"[DreamEvent] cross_chat turn={turn} LLM 失敗, fallback placeholder ({speaker_id})"
            )
            return {1: CHAT_FALLBACK_TURN1, 2: CHAT_FALLBACK_TURN2, 3: CHAT_FALLBACK_TURN3}[turn]
        clean = self._strip_think(content)
        if not clean:
            logger.warning(
                f"[DreamEvent] cross_chat turn={turn} 只回 think, fallback placeholder ({speaker_id})"
            )
            return {1: CHAT_FALLBACK_TURN1, 2: CHAT_FALLBACK_TURN2, 3: CHAT_FALLBACK_TURN3}[turn]
        return clean[:200]  # chat 短文本 cap, 避免單輪爆長

    async def _extract_impression(
        self,
        observer_id: str,
        target_id: str,
        diary_content: str,
        kind: str = "dream",
    ) -> Optional[str]:
        """
        Stage 4.3 (Mavis 拍板 2026-07-21 16:35): LLM 抽 observer 對 target 的印象.

        - 走 _call_llm_for_dream_event (v4-flash, 跟 dream/event 同一路徑)
        - prompt 短, max_tokens 50 (impression 短日文片語)
        - 失敗留空 (「拒絕問, 強制讀」)
        - 不 call get_relationships_manager, 只回傳 impression 文字
        """
        kind_jp = "夢境" if kind == "dream" else "事件"
        system = (
            f"你是 {observer_id}, 剛經歷了一段{ kind_jp }。"
            f"從這段經歷, 抽出一句對 {target_id} 短短的印象 (5-15 字日文)。"
            f"只輸出印象一句, 不要加解釋、引號、標點。"
        )
        user = (
            f"{kind_jp}內容: {diary_content[:200]}\n"
            f"對 {target_id} 的印象:"
        )
        # 短 impression 用同個 _call_llm_for_dream_event (max_tokens 短, 給短印象)
        raw = await _call_llm_for_dream_event(
            system, user, self.api_key,
            timeout=10.0,  # 短 task 縮短 timeout
            max_tokens=50,  # impression 只需 5-15 字日文片語
            temperature=0.5,  # 推理型 model, 低溫穩定輸出單一片語
            agent_id=observer_id,
        )
        if not raw:
            return None
        # 清理: 剝思考區塊、剝引號、剝標籤前綴
        text = raw.strip()
        # 剝 think block (Bry 7/21 12:50 修法: M3 anthropic 預設無 think, 但偶爾會跑出來)
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        # 剝首尾標點 (「」""'' 等)
        text = text.strip("「」『』「」\"' \n。、，,.")
        if not text:
            return None
        return text[:30]  # impression 上限 30 字 (留 buffer)

    def _load_persona_excerpt(self, agent_id: str) -> str:
        """讀 personas/{agent_id}.md 前 200 字 (供 LLM 當性格 hint)."""
        try:
            persona_path = Path("personas") / f"{agent_id}.md"
            if persona_path.is_file():
                return persona_path.read_text(encoding="utf-8")[:200]
        except Exception as e:
            logger.warning(f"[DreamEvent] {agent_id} persona 載入失敗: {e}")
        return ""


# ───────────────────────────────────────────────────────────
# 全域 singleton
# ───────────────────────────────────────────────────────────

_dream_event_writer: Optional[DreamEventWriter] = None


def get_dream_event_writer() -> DreamEventWriter:
    global _dream_event_writer
    if _dream_event_writer is None:
        _dream_event_writer = DreamEventWriter()
    return _dream_event_writer
