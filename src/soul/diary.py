"""
src/soul/diary.py — Soul OS Stage 4.2 (Part 2)

角色日記 (Character Diary)

設計動機 (Bry 拍板 2026-07-18 18:24+):
- diary 是「Bry 從來沒上線過, 角色世界也活」的最直接證據
- 「Bry 是被打斷的觸發之一, 不是主題」: diary 內容 90% 跟 Bry 無關, 角色自己過日子
- 群組去標籤化: 即使同個觸發時間, 10 隻角色寫出來都不同
- 「殘留感」(Bry 觀察期驗收): diary 寫進 history, 後續對話角色會引用

最小可跑範圍 (Stage 4.2 第一刀):
- 每天每隻角色 morning + night 各寫一條
- 存成 jsonl: data/soul/{agent_id}/diary/YYYY-MM-DD.jsonl
- 內容格式: {ts, slot, content, source}
- 第一刀 source="llm" 或 source="placeholder" (LLM 失敗 fallback)
- LLM 呼叫: 用 minimax 拿 persona + 最近 5 條 v1 memory 當 context
- 失敗 fallback: 「今日天氣 + Bry 沒來」短句, 保證 diary 一定有東西

Bry 19:35+ 拍板 (對 4.1 觀察期): 0.7% 機率觸發
- 4.2 第一刀先 100% 觸發 + 100% 寫入, 觀察 1 天後 Bry 拍板要不要降機率

Bry 19:55+ 拍板: 「微調搞幾年 = 本末倒置」= 不要過度設計, 先驗證 1 天殘留感

約束 (沿用 4.1 紀律):
- 「拒絕問, 強制讀」: LLM 失敗 log warning + 寫 placeholder, 不 raise
- 「完成度標記要誠實」: 標清楚 source=llm 還是 source=placeholder
- 「Bry 拍板先設計再開工」: Stage 4.3 impression + 4.2 缺口 1 (夢境) 留到下一刀
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# P0.5 (Bry 派工 2026-08-09 19:48): use data_root() for test isolation
from src.paths import data_root

# Lesson 38: 與 dream_event.py 共用單一 semaphore 限流
# (避免 diary 5 + dream 5 疊加超出 provider 上限)
from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT

logger = logging.getLogger("soul_os.soul.diary")

# ───────────────────────────────────────────────────────────
# 常數
# ───────────────────────────────────────────────────────────

# diary 根目錄 (跟 relationships 同層)
# P0.5 (Bry 派工 2026-08-09 19:48): use data_root() for test isolation
DEFAULT_DIARY_ROOT = str(data_root() / "soul")

# LLM 生成 diary 參數
DIARY_MAX_TOKENS = 200
# M0.3 (2026-08-01 00:45 Perplexity 派工): clean 字符上限
# 對齊 Bry 7/18 拍板: diary 通用規格 1-2 句 < 50 字
# 超過 50 字視為 LLM 沒遵守, 跟 LLM 失敗一樣走 placeholder (Bry 19:55+ 兜底)
# M0.4 (2026-08-06 21:30 Bry 拍板): 50 → 80
# 動機: 8/6 Rem sim 跑出 6 條 placeholder, 全部是「超過 50 字」觸發 (非 LLM 失敗)
# 50 字對中文 LLM 太嚴格, 放寬到 80 給 LLM 自然表達空間
DIARY_MAX_CLEAN_CHARS = 80
DIARY_TEMPERATURE = 0.7
DIARY_RECENT_MEMORIES = 5  # 從 v1 mirror 抽最近 5 條當 context

# Fallback placeholder 模板 (Bry 在拍板日 18:24+ 強調「殘留感」要來自真實 LLM, 但失敗時也要有東西)
PLACEHOLDER_TEMPLATES = {
    "morning": "（{date} 早上）起牀了。窗外還沒什麼聲音。",
    "night": "（{date} 晚上）今天過完了。",
}


# ───────────────────────────────────────────────────────────
# LLM 呼叫 (minimax M2.7 via OpenAI endpoint, JP rollback 後)
# ───────────────────────────────────────────────────────────
# JP rollback (Bry 拍板 2026-07-22 20:59): 換回 M2.7 + OpenAI endpoint
# 之前 7/20 換 M3 + anthropic endpoint 是為了 disable thinking (M3 在 OpenAI 強制 thinking)
# 整套 JP 砍掉後, LLM 跑中文 persona 不需要 M3 強推理, 退回 M2.7 OpenAI 即可
# 備份: src/soul/_backup_m3_switch_20260720_201746/diary.py

async def _call_minimax_for_diary(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "minimax-M2.7",
    base_url: str = "https://api.minimax.io/v1/chat/completions",
    timeout: float = 20.0,
) -> Optional[str]:
    """
    直接呼叫 minimax Chat Completions API 拿 diary 文字。
    失敗 (timeout / HTTP error / 解析失敗) 回傳 None, 由 caller 走 placeholder。

    Lesson 38 (2026-07-30 Bry 拍板):
    改用 httpx.AsyncClient,避免阻塞 asyncio event loop。
    舊版用 requests.post 是同步 I/O,在 scheduler 22:00 觸發 night diary
    時 10 個 agent 連續凍結 event loop 5-30s,連帶 TG polling + WebSocket
    全部卡住 — 7/27 22:07 沉默死亡時間線吻合。
    """
    try:
        import httpx  # lazy import
    except ImportError:
        logger.warning("[Diary] httpx 套件未安裝, fallback placeholder")
        return None
    if not api_key:
        logger.warning("[Diary] MINIMAX_API_KEY 未設定, fallback placeholder")
        return None
    try:
        async with LLM_CONCURRENCY_LIMIT:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": DIARY_TEMPERATURE,
                        "max_tokens": DIARY_MAX_TOKENS,
                    },
                )
        if r.status_code != 200:
            logger.warning(f"[Diary] LLM API failed ({r.status_code}): {r.text[:200]}")
            return None
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"[Diary] LLM API error (fallback placeholder): {e}")
        return None


# ───────────────────────────────────────────────────────────
# Diary 寫入
# ───────────────────────────────────────────────────────────

class DiaryWriter:
    """
    寫日記到 data/soul/{agent_id}/diary/YYYY-MM-DD.jsonl

    用法:
        writer = DiaryWriter(data_dir=str(data_root() / "soul"))  # P0.5: data_root() for test isolation
        await writer.write_entry("agent_mahiru", "morning", content="おはよう", source="llm")
    """

    def __init__(
        self,
        data_dir: str = DEFAULT_DIARY_ROOT,
        api_key: Optional[str] = None,
    ):
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()  # 多 agent 並發寫入安全
        # API key 從 .env / 環境變數讀 (跟 translate.py 同樣 pattern)
        if api_key is None:
            api_key = os.environ.get("MINIMAX_API_KEY", "")
        self.api_key = api_key

    def _diary_path(self, agent_id: str, date_str: str) -> Path:
        return self.data_dir / agent_id / "diary" / f"{date_str}.jsonl"

    @staticmethod
    def _strip_think(content: str) -> str:
        """
        M0.4 (Bry 拍板 2026-08-06 21:30): 剝掉 <think>...</think> 區塊。

        M2.7 / M3 偶爾會在 content 內夾帶 think block 推理痕跡。
        修法前: write_entry 寫 raw, jsonl 內殘留 think + 沒實際 diary。
        修法後: write_entry 寫 clean (think 已被剝), jsonl 乾淨。

        返回 stripped content. 若原 content 沒有 think block, 原樣返回。
        """
        return re.sub(r"^<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

    def write_entry(
        self,
        agent_id: str,
        slot: str,  # "morning" | "night"
        content: str,
        source: str = "llm",  # "llm" | "placeholder"
        # M5.4-5.3 (Bry 派工 2026-08-09 21:06): canonical Inner Life event reference.
        # Optional: if provided (and non-empty), attach to the entry dict so the
        # diary row can be cross-referenced with the InnerLifeEvent.
        # Backward-compat: None/empty means the field is omitted from the entry
        # dict, so legacy readers that ignore unknown keys continue to work.
        inner_life_event_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        寫一條 diary entry 到今天的 jsonl。
        回傳寫入的路徑 (None 表示失敗)。

        M0.4 (Bry 拍板 2026-08-06 21:30):
        - 寫入前先剝 think block, 拿 clean 寫入 (jsonl 乾淨)
        - clean 空 → 拒絕寫入 (return None), 強迫 caller 走 placeholder
          修法前: 7 條 think_only (raw 200+ chars, clean 0) 被誤判成 source=llm 寫入
          修法後: write_entry 看到 clean 空直接擋掉, caller 必須改傳 placeholder

        M5.4-5.3 (Bry 派工 2026-08-09 21:06):
        - inner_life_event_id: optional canonical InnerLifeEvent.event_id reference
        """
        if slot not in ("morning", "night"):
            logger.warning(f"[Diary] {agent_id} 未知 slot={slot}, 跳過")
            return None
        # M0.4: 寫入前 strip think, 拿 clean 寫入
        clean = self._strip_think(content)
        if not clean:
            # think_only 情況: LLM 只回 think block 沒實際 diary
            # 修法前會被當 source=llm 寫入, 修法後直接擋掉
            logger.warning(
                f"[Diary] {agent_id} {slot} 拒絕寫入: clean 為空 "
                f"(raw {len(content)} chars, 可能 LLM 只回 think 沒實際 diary)"
            )
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        path = self._diary_path(agent_id, today)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "slot": slot,
            "content": clean,  # M0.4: 寫 clean (不是 raw), jsonl 內不會有 think block
            "source": source,
        }
        # M5.4-5.3: attach canonical Inner Life event reference if provided.
        # Empty string treated as None for safety (avoid corrupting jsonl with "" id).
        if inner_life_event_id:
            entry["inner_life_event_id"] = inner_life_event_id
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                logger.info(
                    f"[Diary] ✓ 寫入 {agent_id} {slot} → {path.name} "
                    f"({len(clean)} chars, source={source})"
                )
                return path
            except Exception as e:
                logger.exception(f"[Diary] ✗ 寫入失敗 {agent_id} {slot}: {e}")
                return None

    def read_entries(
        self, agent_id: str, date_str: str
    ) -> List[Dict]:
        """讀某天全部 entry, 給 v1 注入或 history 引用用."""
        path = self._diary_path(agent_id, date_str)
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def recent_entries(
        self, agent_id: str, days: int = 1
    ) -> List[Dict]:
        """讀最近 N 天全部 entry (供 history / 觀察用)."""
        from datetime import timedelta
        out = []
        for i in range(days):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            out.extend(self.read_entries(agent_id, d))
        return out


# ───────────────────────────────────────────────────────────
# Diary 生成 (LLM + Fallback)
# ───────────────────────────────────────────────────────────

# 全域 DiaryWriter (跟 Scheduler 同樣 singleton 模式)
_diary_writer: Optional[DiaryWriter] = None


def get_diary_writer() -> DiaryWriter:
    global _diary_writer
    if _diary_writer is None:
        _diary_writer = DiaryWriter()
    return _diary_writer


async def generate_diary_entry(
    agent_id: str,
    slot: str,  # "morning" | "night"
    persona_prompt: str = "",
    recent_memories: Optional[List[str]] = None,
    writer: Optional[DiaryWriter] = None,
    # M5.4-5.3 (Bry 派工 2026-08-09 21:06): canonical Inner Life event reference
    # for the diary entry being generated. Optional passthrough to write_entry.
    inner_life_event_id: Optional[str] = None,
) -> Optional[Path]:
    """
    給一個 agent + slot 產生並寫入 diary entry。
    LLM 失敗走 placeholder。

    Args:
        agent_id: 角色 id
        slot: "morning" | "night"
        persona_prompt: 角色 persona 摘要 (供 LLM 當 system prompt 用)
        recent_memories: 最近 v1 memory 列表 (供 LLM 當 context)
        writer: DiaryWriter (預設用 singleton)
        inner_life_event_id: M5.4-5.3 — 對應 canonical InnerLifeEvent.event_id
    """
    if writer is None:
        writer = get_diary_writer()
    if recent_memories is None:
        recent_memories = []

    today = datetime.now().strftime("%Y-%m-%d")
    slot_zh = "早上記錄" if slot == "morning" else "晚上記錄"
    persona_section = persona_prompt or "(no persona provided)"
    memories_section = "\n".join(f"- {m}" for m in recent_memories[:DIARY_RECENT_MEMORIES]) or "(no recent memory)"
    placeholder = PLACEHOLDER_TEMPLATES[slot].format(date=today)

    # LLM 呼叫 (包成 sync, 走 requests)
    system = (
        f"你是 {agent_id}, 正在寫自己的 {slot_zh}。\n"
        f"{persona_section}\n"
        f"寫 1-2 句, 50 字以內, 日文為主。\n"
        f"你有自己的生活。Bry 是你生活裡一個重要的人——"
        f"日記主體仍是你自己的生活，但當你做了想跟他分享、或讓你想到他的事時，"
        f"可以自然流露想念。"
    )
    user = (
        f"日期: {today} ({slot})\n"
        f"最近的事:\n{memories_section}\n\n"
        f"寫一條今天的 {slot_zh}:"
    )
    # M0.5 A1+A2 (Bry 派工 2026-08-06 21:44): 截斷保留 LLM 內容 + think_only retry 一次
    # Bry 派工精神:
    #   - 截斷 (A1): 沿用修法 10 _safe_truncate_on_length, max_chars=80, 保留 LLM 真實產出
    #   - retry (A2): think_only 才 retry, 加輕量 hint (「請直接輸出最終內容」)
    #   - 不要做 D 那種嚴格收斂 prompt, 過度收斂會把好例子的空間壓沒
    from src.llm.proxy import _safe_truncate_on_length  # 沿用修法 10 的截斷 helper
    RETRY_HINT = "\n\n（請直接輸出最終內容，不要輸出思考過程。）"
    content = await _call_minimax_for_diary(system, user, writer.api_key)

    # M0.2 (2026-08-01 00:35 Perplexity 派工): 抽掉 think block 後檢查 clean
    # 修法動機: LLM 偶爾只回 <think>...</think> 沒實際 diary, raw content non-empty 但 clean empty.
    # 之前 `if content` 走 source=llm 寫入, 導致 0 chars empty 污染 diary.
    # 對齊 Bry 19:55+ 「拒絕問, 強制讀」+ 兜底原則: clean empty 跟 LLM 失敗一樣走 placeholder.
    # M0.4 (2026-08-06 21:30 Bry 拍板): 邏輯收斂到 write_entry 內做, 這裡只負責算 clean
    # M0.5 (2026-08-06 21:44 Bry 拍板): think_only 加 retry, 超長改截斷
    if content:
        clean = re.sub(r"^<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
        if not clean:
            # M0.5 A2: think_only retry 一次
            logger.warning(
                f"[Diary] {agent_id} {slot} LLM 只回 think 沒 diary "
                f"(raw {len(content)} chars, clean 0), retry 一次"
            )
            content = await _call_minimax_for_diary(system, user + RETRY_HINT, writer.api_key)
            if content:
                clean = re.sub(r"^<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
        if not clean:
            # retry 也失敗, 才走 placeholder
            logger.warning(
                f"[Diary] {agent_id} {slot} LLM retry 後仍 think_only / 失敗, 寫 placeholder"
            )
            return writer.write_entry(
                agent_id, slot, placeholder, source="placeholder",
                inner_life_event_id=inner_life_event_id,
            )
        # M0.5 A1: 超長改截斷, 不再整段丟棄
        # Bry 8/6 21:44 派工: 保留 LLM 真正寫出來的內容, 只是裁短, 不是整段作廢
        if len(clean) > DIARY_MAX_CLEAN_CHARS:
            truncated = _safe_truncate_on_length(clean, max_chars=DIARY_MAX_CLEAN_CHARS)
            logger.info(
                f"[Diary] {agent_id} {slot} LLM 輸出超長 "
                f"({len(clean)} chars > {DIARY_MAX_CLEAN_CHARS}), 截斷到 {len(truncated)} chars (修法 10 pattern)"
            )
            # 截斷後的內容還是要通過 write_entry 的 strip_think 檢查
            return writer.write_entry(
                agent_id, slot, truncated, source="llm",
                inner_life_event_id=inner_life_event_id,
            )
        return writer.write_entry(
            agent_id, slot, content, source="llm",
            inner_life_event_id=inner_life_event_id,
        )
    else:
        logger.warning(f"[Diary] {agent_id} {slot} LLM 失敗, 寫 placeholder")
        return writer.write_entry(
            agent_id, slot, placeholder, source="placeholder",
            inner_life_event_id=inner_life_event_id,
        )


# ───────────────────────────────────────────────────────────
# 跟 Middleware / V1 串接 (Stage 4.2 之後補)
# ───────────────────────────────────────────────────────────

async def diary_callback_factory(agent_id: str):
    """
    給 scheduler.register() 用的 callback factory。

    Bry 拍板 2026-07-20 18:58: 升級到 LLM 真生成 (Bry 5.5 小時觀察 Bry 決定升級)
    - 從 2026-07-20 13:12 重啟 placeholder 模式到 18:58 升級 = 5.5 小時
    - Bry 違反 Bry 自己 2026-07-18 23:50 拍的「看完 1 天」 (Bry 5.5 小時就升級)
    - Bry 拍板權最高, Bry 隨時可以改 Bry 自己拍的觀察期
    - 之後 Bry 觀察 1 天, Bry 覺得 OK 進 4.3, 不 OK 退回 placeholder

    第一刀 (Bry 拍板 2026-07-18 18:24+ 觀察期) Bry 已看完 5.5 小時, Bry 決定升級 LLM。
    第二刀 (Bry 拍板 2026-07-20 18:58) LLM 真生成:
    - 載入 persona prompt (從 personas/{agent_id}.md 讀前 500 字)
    - 抽最近 5 條 v1 mirror memory 當 context
    - 呼叫 generate_diary_entry() 走 minimax M2.7
    - LLM 失敗 fallback placeholder (「拒絕問, 強制讀」)
    - 「完成度標記要誠實」: source=llm / source=placeholder 100% 標清楚

    註冊方式:
        scheduler.register(agent_id, await diary_callback_factory(agent_id))
    """
    from pathlib import Path
    writer = get_diary_writer()

    # 1. 載入 persona prompt (Bry 拍板 2026-07-20 18:58: 升級版需要)
    persona_prompt = ""
    try:
        persona_path = Path("personas") / f"{agent_id}.md"
        if persona_path.is_file():
            # 取前 500 字 (system prompt 長度控制)
            persona_prompt = persona_path.read_text(encoding="utf-8")[:500]
    except Exception as _p_err:
        logger.warning(
            f"[Diary] {agent_id} persona 載入失敗, fallback 空: {_p_err}"
        )

    # 2. 抽最近 5 條 v1 mirror memory (Bry 拍板 2026-07-18 23:50 觀察期 → 升級版)
    recent_memories: List[str] = []
    try:
        from src.memory.v1.store import V1Store
        v1_store = V1Store(data_root() / "soul", agent_id)
        all_memories = v1_store.all()
        # 取最後 5 條 (時間序最新的)
        for m in all_memories[-DIARY_RECENT_MEMORIES:]:
            content = getattr(m, "content", None) or (
                m.get("content") if isinstance(m, dict) else str(m)
            )
            if content:
                # 限制長度避免 LLM context 爆
                recent_memories.append(content[:100])
    except Exception as _v_err:
        logger.warning(
            f"[Diary] {agent_id} v1 memory 載入失敗, fallback 空: {_v_err}"
        )

    async def cb(agent_id_inner: str, slot: str, inner_life_event_id: Optional[str] = None):
        # Bry 拍板 2026-07-20 18:58: 升級 LLM 真生成
        # Bry 5.5 小時 Bry 拍板升級 (Bry 違反 7/18 「看完 1 天」Bry 拍板, Bry 拍板權最高)
        # LLM 失敗走 placeholder fallback (「拒絕問, 強制讀」Bry 拍板)
        # source 標清楚: source=llm 或 source=placeholder (「完成度標記要誠實」Bry 拍板)
        #
        # M5.4-6.1 (Bry 派工 2026-08-10): executor-level inner_life_event_id passthrough
        # Range: optional kwarg, default None (preserves M5.2-H Phase 3 既有 diary_callbacks_real 呼叫契約)
        # 後向相容: 不傳 inner_life_event_id 的 caller 仍可正常運作 (跟 M5.4-5.3 一致,
        #          write_entry 收到 None 就不加 inner_life_event_id 進 entry dict)
        await generate_diary_entry(
            agent_id=agent_id_inner,
            slot=slot,
            persona_prompt=persona_prompt,
            recent_memories=recent_memories,
            writer=writer,
            inner_life_event_id=inner_life_event_id,
        )

    return cb
