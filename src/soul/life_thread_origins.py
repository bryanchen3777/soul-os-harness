# 模組 4 — 起源 Prompt 注入範式（Origin Prompt Injection）｜票號 LIFE-THREAD-M4-1
#
# 註：本模組**刻意不在原始碼內容中留下自身檔名字面**——§10.1 的孤立性驗收
#     以 `git grep <本模組檔名根> -- src scripts configs` 的命中集合為準
#     （LIFE-THREAD-M5 接線後＝白名單 `scripts/run_server.py`（僅 `set_llm_proxy` 注入）
#     ＋ `src/soul/life_thread_orchestrator.py`（唯一生產 caller），外加既有的一行註解引用）；
#     任何自指的字面（含標頭註解、logger 名）都會讓該斷言偽陽性。
"""
Soul OS — 生活線頭引擎 **模組 4（起源 Prompt 注入範式）**。

規格唯一來源：`docs/LIFE-THREAD-ENGINE-CONTRACT.md`
  - **§5.1**（`:419-448`）喚醒輪次的 prompt 契約（共同骨架）
  - **§5.2**（`:450-486`）逐 `origin_type` 注入模板（四個：`goal_driven` /
    `necessity_driven` / `whim_driven` / `world_collision`）
  - **§5.3**（`:488-492`）「立體節奏」為**軟性驗收**（不由本模組強制）
  - **§8**（`:634-672`）成本模型：本模組是 §8.1 唯一兩條 LLM 路徑的實作端
  - **§9 O4**（`:685`）觀測行 `[LifeThread] created …`
  - **§10.1**（`:699-726`）M4 **只依賴 M1**；M4 只提供「可被呼叫的入口」
  - **§12**（`:761-781`）0 Frozen Contract 變更

🔴 **本模組已接線（LIFE-THREAD-M5）**：生產呼叫端**恰為**
   `src/soul/life_thread_orchestrator.py` —— 該介接層於既有 wake 的 `morning` / `night`
   slot 觸發窗內**生產呼叫**本模組；**唯一**喚醒動作 ＝ `run_origin_round(...)`；
   `scripts/run_server.py` 只做 `set_llm_proxy` 注入（0 新 provider／0 新通道）。
   **仍不得**被白名單外的任何生產路徑 import；「何時喚醒」是 M3 的職責，
   不是本模組的職責（§10.1）。

🔴 **資料寫入一律經 M1**（`src/soul/life_threads.py`）的公開 API：
   `create_thread()` / `append_updated()` / `append_transition()`。
   本模組**不自行開檔、不自行組列、不繞過容量防線**（§2.6.3「強制點只有一個」）。

🔴 **No-Scoring（VISION §2.4／契約 §2.2）**：本模組不引入任何數值評分／權重／排序；
   寫入 dict 的 key 集合不含 `score` / `weight` / `intensity` / `urgency` /
   `priority` / `longing` / `confidence`。

`world_collision` 種子文字來源
------------------------------
依 §5.2.4，種子文字取自 `data/world/perception_trace.jsonl` 記錄的
**`extra["summary"]`**（經**前置票 `WORLD-FACT-TEXT-PERSIST-1`** 修訂契約後的口徑；
該票負責把 fact text 寫進既有 trace 的 `extra["summary"]`，並 reconcile §5.2.4 ↔ A.4）。

🔴 **本模組不得等前置票**：既有歷史列（13,609 筆，改動前）**沒有** `extra["summary"]`。
   故讀取端**必須容忍缺欄**：缺欄／空字串／純空白 ⇒ **該事件不構成種子**，
   寫一行有界計數（`[LifeThreadOrigin] world_collision 無 fact text, skipped=N`）
   後**繼續**，**不得 raise、不得中斷**。
   **絕不以 `event_type` / `novelty_id` / `reason` 頂替、絕不捏造**（§5.2.4 禁止事項）。

成本（§8）
---------
路徑 A｜生活推進詮釋：每次放行 **恰好 1 次** LLM 呼叫（本模組 `run_origin_round`）。
路徑 B｜蔡戈尼溶解：`op == complete/abandon` 之後的第 2 次呼叫 —— **屬 M2**。
本模組只提供 `dissolve_hook` **接縫**（預設 `None` ＝ 不呼叫，0 成本）；
M2 落地後由呼叫端注入。故本模組單輪 LLM 呼叫數 ≤ 2（§8.1），
且**同一輪不得重複呼叫**（失敗 fail-silent，不中斷呼叫端）。
"""
from __future__ import annotations

import inspect
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT
from src.soul import life_threads as lt

# logger 名以 `__name__` 取得（**不在原始碼留下自身檔名字面** ⇒ §10.1 孤立性掃描不受污染）。
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 常數（全部具名，禁止裸字面量）
# ══════════════════════════════════════════════════════════════

#: §8.1／§8.4 路徑 A 硬預算（per-agent/per-day 計數，非評分）。
LIFE_THREAD_WAKE_MAX_PER_DAY = 2
#: §8.1／§8.4 路徑 B 硬預算（＝ `ACTIVE_CAP_HARD_MAX`）。
LIFE_THREAD_DISSOLVE_MAX_PER_DAY = 3
#: §8.1 單輪最壞呼叫數 ＝ 路徑 A(1) ＋ 路徑 B(1)。
MAX_LLM_CALLS_PER_ROUND = 2

#: §5.1 `actions` 長度上限（防單輪噴發；超過截斷至前 3 項）。
MAX_ACTIONS_PER_ROUND = 3

#: §5.1 `next_check_hours` → `check_after_ts` 的 clamp 範圍。
NEXT_CHECK_HOURS_MIN = 1
NEXT_CHECK_HOURS_MAX = 72

#: §5.2.1 注入的 goal 條數上限。
MAX_GOALS_IN_CONTEXT = 3
#: §5.2.2 近 N 日 diary。
DIARY_LOOKBACK_DAYS = 3
#: §5.2.2 diary 條目在 prompt 內的字元上限（輕量字串）。
DIARY_ENTRY_MAX_CHARS = 120

#: §4.2／§5.2.4 世界碰撞視窗（小時）。
WORLD_COLLISION_WINDOW_HOURS = 4
#: §4.2 `SOURCES_QUALIFYING`（算數的來源，窮舉）。
SOURCES_QUALIFYING: Tuple[str, ...] = (
    "weather",
    "news",
    "news_event",
    "calendar",
    "calendar_event",
)
#: `WORLD-FACT-TEXT-PERSIST-1` 的 trace fact text 截斷口徑（本側防禦性再截）。
MAX_FACT_CHARS = 200
#: trace 內承載 fact text 的鍵（唯一合法來源）。
FACT_TEXT_EXTRA_KEY = "summary"

#: `soul_context` 注入 prompt 的字元上限（第一人稱風格來源，§5.2.3）。
SOUL_CONTEXT_MAX_CHARS = 1500

#: §5.1 LLM 輸出上限（單一 JSON，含 actions 1~3）。
GENERATE_MAX_TOKENS = 900
GENERATE_TEMPERATURE = 0.8

#: §5.2.2 時段值域**只有兩個**（＝ §4.1 的兩個評估點；不新增第三個時段）。
SLOT_VALUES: Tuple[str, ...] = ("morning", "night")
#: morning 的起訖（含起、不含訖）；其餘一律 `night`。
MORNING_START_HOUR = 5
MORNING_END_HOUR = 12

#: §5.1 的 **op** 值域 ≠ §2.5 的 **status** 值域，必須顯式映射
#: （`op="complete"` ⇒ `status="completed"`；`op="abandon"` ⇒ `status="abandoned"`）。
#: 直接拿 op 當 status 傳給 M1 會被 §2.5 判為非法轉移並拒絕。
_OP_TO_STATUS: Dict[str, str] = {
    "complete": "completed",
    "abandon": "abandoned",
}

#: §5.2.3 `whim_driven` 的無處境通用錨（**不宣稱任何具體外部事件** ⇒ 不捏造）。
WHIM_NEUTRAL_ANCHOR = (
    "眼下生活裡那些沒得選的瑣事（該洗的、該修的、該吃的、身體的疲累）"
)

# ── 觀測行（§9）────────────────────────────────────────────
#: §9 O4：逐 `origin_type` 計數 → 可驗收 §5.3 起源多樣性。
LOG_CREATED = (
    "[LifeThread] created agent={agent_id} thread={thread_short} "
    "origin={origin_type} target={share_target}"
)
#: 前置票未生效／歷史列缺 `extra["summary"]` 時的有界計數（有界，非評分）。
LOG_WORLD_NO_FACT = (
    "[LifeThreadOrigin] world_collision 無 fact text, skipped={skipped}"
)
LOG_NO_PROMPT = (
    "[LifeThreadOrigin] prompt unavailable agent={agent_id} "
    "origin={origin_type} reason={reason}"
)
LOG_LLM_FAILED = (
    "[LifeThreadOrigin] LLM 失敗或非 JSON，本時段不落盤 "
    "agent={agent_id} origin={origin_type}"
)
LOG_ROUND = (
    "[LifeThreadOrigin] round agent={agent_id} origin={origin_type} "
    "llm_calls={llm_calls} created={created} advanced={advanced} "
    "transitioned={transitioned} skipped={skipped}"
)
LOG_NO_PROXY = (
    "[LifeThreadOrigin] 未注入 LLMProxy，fail-silent 不落盤 agent={agent_id}"
)


# ══════════════════════════════════════════════════════════════
# LLM 接縫（沿用既有慣例：`set_llm_proxy` / `_find_llm_proxy`）
# 與 `src/soul/diary.py:94-102`、`src/soul/dream_event.py:313-321` 同構。
# **0 新 provider／0 新通道**：只呼叫既有 `LLMProxy.generate_text`（§8.1）。
# ══════════════════════════════════════════════════════════════

_global_llm_proxy = None


def set_llm_proxy(llm_proxy) -> None:
    """設定 process-global LLMProxy reference（與 diary/dream_event 同慣例）。"""
    global _global_llm_proxy
    _global_llm_proxy = llm_proxy


def _find_llm_proxy():
    """回傳 process-global LLMProxy（無則 `None`）。"""
    return _global_llm_proxy


# ══════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════

def _short(thread_id: Any) -> str:
    """§9 觀測行的 uuid8。"""
    return str(thread_id or "")[:8]


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_ts(value: Any) -> Optional[datetime]:
    """ISO 8601 → aware datetime；失敗回 `None`（**不猜**，§4.2）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def current_slot(now: Optional[datetime] = None) -> str:
    """當前時段 ∈ {`morning`, `night`}（值域只有兩個，§4.1）。"""
    dt = now or datetime.now()
    if MORNING_START_HOUR <= dt.hour < MORNING_END_HOUR:
        return "morning"
    return "night"


def _local_time_text(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M")


def _now_utc(now: Optional[datetime] = None) -> datetime:
    """正規化為 aware UTC。"""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """只讀 jsonl：壞行跳過、讀取失敗回 `[]`（**0 raise**，沿用既有慣例）。"""
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError as e:
        logger.warning(f"[LifeThreadOrigin] jsonl 讀取失敗 (fail-silent): {e}")
        return []
    return out


def _extract_json_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """從 LLM 輸出提取 JSON dict（容錯 markdown 圍欄／前後雜訊；失敗回 `None`）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


# ══════════════════════════════════════════════════════════════
# 種子蒐集（§5.2 逐 origin_type）
# ══════════════════════════════════════════════════════════════

def _goal_db_path(agent_id: str) -> Path:
    """per-agent `graph.sqlite`（對齊 §5.2.1 引用的 `src/goals/motive_provider.py:115-116`
    與 `src/soul/decision.py:438` 的同一路徑模式）。"""
    from src.paths import data_root

    return data_root() / "memory" / agent_id / "graph.sqlite"


def read_goals_readonly(agent_id: str) -> List[Dict[str, Any]]:
    """唯讀讀取該 agent 的 `goals` 表（**只讀該 agent**，§5.2.1 禁令）。

    以 sqlite `mode=ro` 開啟 ⇒ **不建目錄、不建檔、不寫任何資料**；
    檔案／表不存在或任何錯誤 ⇒ 回 `[]`（fail-silent，0 raise）。
    """
    db = _goal_db_path(agent_id)
    if not db.is_file():
        return []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT title, state FROM goals WHERE agent_id = ? ORDER BY created_at",
            (agent_id,),
        ).fetchall()
    except Exception as e:
        logger.warning(f"[LifeThreadOrigin] goals 讀取失敗 (fail-silent): {e}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return [{"title": r[0], "state": r[1]} for r in rows]


def collect_goal_seeds(
    agent_id: str,
    goals: Optional[Sequence[Any]] = None,
) -> List[Dict[str, str]]:
    """§5.2.1 `goal_driven` 種子：該 agent `ACTIVE`/`IN_PROGRESS` goal（≤3 條）。

    **唯讀**：`goals` 參數可注入（測試）；`None` 時走 `read_goals_readonly()`。
    **不修改 goal 狀態、不讀其他 agent 的 goals**。
    """
    from src.goals.models import GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS

    allowed = (GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS)
    src = read_goals_readonly(agent_id) if goals is None else list(goals)

    out: List[Dict[str, str]] = []
    for g in src:
        if isinstance(g, dict):
            state = _clean_str(g.get("state"))
            title = _clean_str(g.get("title"))
        else:
            state = _clean_str(getattr(g, "state", ""))
            title = _clean_str(getattr(g, "title", ""))
        if state not in allowed or not title:
            continue
        out.append({"title": title, "state": state})
        if len(out) >= MAX_GOALS_IN_CONTEXT:
            break
    return out


def _diary_path(agent_id: str, date_str: str) -> Path:
    from src.paths import data_root

    return data_root() / "soul" / agent_id / "diary" / f"{date_str}.jsonl"


def read_recent_diary_llm(
    agent_id: str,
    now: Optional[datetime] = None,
    days: int = DIARY_LOOKBACK_DAYS,
) -> List[Dict[str, str]]:
    """§5.2.2 近 `days` 日 diary 中 `source == "llm"` 的條目。

    過濾口徑沿用既有 `src/llm/proxy.py:301`（`source == "llm"`，排除 placeholder）；
    slot 白名單沿用 `src/llm/proxy.py:293`。壞行跳過、0 raise。
    """
    allowed_slots = ("morning", "night", "dream", "event")
    base = now or datetime.now()
    out: List[Dict[str, str]] = []
    for i in range(days):
        date_str = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        for rec in _read_jsonl(_diary_path(agent_id, date_str)):
            if _clean_str(rec.get("slot")) not in allowed_slots:
                continue
            if rec.get("source") != "llm":
                continue
            content = _clean_str(rec.get("content"))
            if not content:
                continue
            if len(content) > DIARY_ENTRY_MAX_CHARS:
                content = content[: DIARY_ENTRY_MAX_CHARS - 3] + "..."
            out.append(
                {"date": date_str, "slot": _clean_str(rec.get("slot")), "content": content}
            )
    return out


def collect_necessity_seeds(
    agent_id: str,
    now: Optional[datetime] = None,
    diary: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """§5.2.2 `necessity_driven` 種子：當前時段 ＋ 近 3 日 diary（`source=="llm"`）。

    `situation` ＝ 該角色**當下處境**：優先取最近一則 diary 內容；
    無 diary 時退回 `WHIM_NEUTRAL_ANCHOR`（**不宣稱任何具體外部事件** ⇒ 不捏造）。
    """
    dt = now or datetime.now()
    entries = list(diary) if diary is not None else read_recent_diary_llm(agent_id, dt)
    situation = ""
    for rec in entries[-1:]:  # 最近一則
        situation = _clean_str(rec.get("content") if isinstance(rec, dict) else "")
    if not situation:
        situation = WHIM_NEUTRAL_ANCHOR
    return {
        "slot": current_slot(dt),
        "local_time": _local_time_text(dt),
        "diary": entries,
        "situation": situation,
    }


def load_soul_context(agent_id: str, max_chars: int = SOUL_CONTEXT_MAX_CHARS) -> str:
    """§5.2.3 種子：角色個性（`load_persona(agent_id)` 的 `soul_context`）。

    失敗／空 ⇒ 回 `""`（fail-silent，0 raise）。
    """
    try:
        from src.llm.proxy import load_persona

        raw = load_persona(agent_id)
    except Exception as e:
        logger.warning(f"[LifeThreadOrigin] load_persona 失敗 (fail-silent): {e}")
        return ""
    text = _clean_str(raw)
    if not text:
        return ""
    return text[:max_chars]


def collect_whim_seeds(
    agent_id: str,
    soul_context: Optional[str] = None,
) -> Dict[str, Any]:
    """§5.2.3 `whim_driven` 種子：**只有** `soul_context`。

    🔴 此源必須**純內生**：本函式**不得**觸碰 Lived Context／外部事實／時間，
    否則與 `world_collision` 重疊（§5.2.3 禁止事項）。
    """
    ctx = soul_context if soul_context is not None else load_soul_context(agent_id)
    return {"soul_context": _clean_str(ctx)}


def _perception_trace_path() -> Path:
    from src.paths import data_root

    return data_root() / "world" / "perception_trace.jsonl"


def extract_fact_text(record: Any) -> str:
    """從 trace 記錄取出 fact text：**唯一合法來源** ＝ `extra["summary"]`。

    缺欄／空字串／純空白 ⇒ 回 `""`（**該事件不構成種子**）。
    🔴 **絕不以 `event_type` / `novelty_id` / `reason` 頂替、絕不捏造**（§5.2.4）。
    """
    if not isinstance(record, dict):
        return ""
    extra = record.get("extra")
    if not isinstance(extra, dict):
        return ""
    text = _clean_str(extra.get(FACT_TEXT_EXTRA_KEY))
    if not text:
        return ""
    return text[:MAX_FACT_CHARS]


def collect_world_seeds(
    agent_id: str,
    now: Optional[datetime] = None,
    records: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """§5.2.4 `world_collision` 種子：`accepted == True` 且在 4h 視窗內的
    合格來源（`SOURCES_QUALIFYING`）記錄，其 `extra["summary"]` 為**具體事實文字**。

    🔴 讀取端容忍前置票未生效：缺 `extra["summary"]` 者**不構成種子**，
    只累計有界計數並寫一行 `LOG_WORLD_NO_FACT`，**繼續**（0 raise）。
    **無命中 ⇒ `facts == []`** ⇒ 呼叫端**不得**組 prompt、**不得**捏造（§5.2.4）。
    """
    now_utc = _now_utc(now)
    cutoff = now_utc - timedelta(hours=WORLD_COLLISION_WINDOW_HOURS)
    recs = list(records) if records is not None else _read_jsonl(_perception_trace_path())

    facts: List[Dict[str, str]] = []
    skipped = 0
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        if rec.get("accepted") is not True:
            continue
        source = _clean_str(rec.get("source"))
        if source not in SOURCES_QUALIFYING:
            continue
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        fact = extract_fact_text(rec)
        if not fact:
            skipped += 1
            continue
        facts.append(
            {
                "summary": fact,
                "event_type": _clean_str(rec.get("event_type")),
                "source": source,
            }
        )

    if skipped:
        logger.info(LOG_WORLD_NO_FACT.format(skipped=skipped))

    return {
        "facts": facts,
        "local_time": _local_time_text(now),
        "skipped": skipped,
    }


# ══════════════════════════════════════════════════════════════
# §5.1 共同骨架 ＋ §5.2 逐 origin_type 模板
# ══════════════════════════════════════════════════════════════

_OUTPUT_CONTRACT = """[輸出契約 §5.1]
你的回覆必須是**單一 JSON 物件**，且**不得**有其他文字、不得使用 markdown 圍欄：
{"actions": [
  {"thread_id": "<既有 uuid 或 null>",
   "op": "advance|complete|abandon|create",
   "title": "<1~40 字元，op=create 時必填>",
   "narrative_content": "<1~600 字元>",
   "origin_type": "{origin_type}",
   "share_target": "none|lounge|agent_<id>|user_bryan",
   "next_check_hours": <整數 1~72>}
]}

[規則]
1. `actions` 1~3 項；**超過 3 項只取前 3 項**。
2. `narrative_content` 必須具備**完整生活語義**（1~600 字元）：不得為空、不得是
   佔位符、**不得等同 `title`**。這是本引擎的存在理由。
3. `op="create"` 時 `title` 必填（1~40 字元）、`thread_id` 為 `null`。
4. `op="advance"|"complete"|"abandon"` 時 `thread_id` 必須是**下方到期線頭**的 uuid。
5. `next_check_hours` 是**整數時距**（1~72，clamp），**不是**強度分數。
6. 🔴 **禁止任何數值評分／權重／強度欄位**（不得出現 score / weight / intensity /
   urgency / priority / longing / confidence 等鍵）。
7. 本輪你只能使用 `origin_type = "{origin_type}"`。
8. **沒有任何真正的新張力時，就讓 `actions` 只含對到期線頭的推進／結案**——
   留白是合法的，不要為了產出而編造。"""

_ORIGIN_BLOCKS: Dict[str, Dict[str, str]] = {
    "goal_driven": {
        "template": (
            "你的長期意向之一是「{goal_title}」（狀態：{goal_state}）。\n"
            "**這幾天你在這上面實際動手了嗎？** 如果沒有，那是因為什麼具體的事卡住了？"
            "如果有，進展到哪一步？"
        ),
        "forbidden": (
            "[禁止事項 §5.2.1]\n"
            "- **不得**修改 goal 狀態（goal 是唯讀種子）。\n"
            "- **不得**把 goal 直接複製成線頭 `title`：線頭必須是**具體生活張力**，"
            "不是意向複述（Goals 是長程意向燈塔，線頭是具體生活張力）。\n"
            "- **不得**談論其他角色的 goals。"
        ),
    },
    "necessity_driven": {
        "template": (
            "現在是{slot}（{local_time}）。有些事不是你能選要不要發生的——"
            "{situation}。\n**你打算怎麼處理它？**"
        ),
        "forbidden": (
            "[禁止事項 §5.2.2]\n"
            "- 🔴 **不得**宣稱「沒有選擇要不要開始」：需求驅動**沒有選擇要不要開始，"
            "只有選擇怎麼解決** ⇒ 你必須**預設事情已經存在**，直接回答**怎麼解決**，"
            "**不要**回答「要不要做」。\n"
            "- **不得**要求任何外部工具呼叫。"
        ),
    },
    "whim_driven": {
        "template": (
            "依照你的個性，有什麼**只有你才會**突然想做的事？不是為了誰，"
            "也不是因為發生了什麼——就是忽然想。描述你**當下正在做**的那個動作"
            "與心裡的念頭。"
        ),
        "forbidden": (
            "[禁止事項 §5.2.3]\n"
            "- 🔴 **不得**使用任何外部事實／世界感知／時事（此源必須**純內生**，"
            "否則與 world_collision 重疊）。\n"
            "- **不得**輸出通用雞湯：必須是**這個角色獨有**的偏好動作。"
        ),
    },
    "world_collision": {
        "template": (
            "剛剛世界發生了這件事：{fact_summary}\n"
            "它跟你**自己的生活**有什麼關係？你**當下**因此改變了什麼動作？"
        ),
        "forbidden": (
            "[禁止事項 §5.2.4]\n"
            "- 🔴 **不得**捏造天氣／新聞／日程：只能用上面那條**已提供的事實文字**。\n"
            "- **不得**把世界事實寫成「Bryan 對我說的話」（世界事件不是對話）。\n"
            "- **不得**跨角色洩漏（此線頭只屬於你自己）。"
        ),
    },
}


def build_origin_prompt(
    agent_id: str,
    origin_type: str,
    *,
    now: Optional[datetime] = None,
    seeds: Optional[Dict[str, Any]] = None,
    due_threads: Optional[Sequence[Any]] = None,
    soul_context: Optional[str] = None,
    goals: Optional[Sequence[Any]] = None,
    diary: Optional[Sequence[Any]] = None,
    world_records: Optional[Sequence[Any]] = None,
) -> Optional[Dict[str, str]]:
    """組出該 `origin_type` 的喚醒 prompt（§5.1 骨架 ＋ §5.2 模板）。

    回傳 `{"system": str, "user": str}`；**必填上下文不足時回 `None`**
    （fail-silent：呼叫端不呼叫 LLM、不落盤 —— 這正是 §5.2.4「杜絕幻覺」的執行點）。
    """
    if origin_type not in lt.ORIGIN_TYPES:
        logger.warning(f"[LifeThreadOrigin] 非法 origin_type: {origin_type!r}")
        return None

    ctx = _clean_str(soul_context) if soul_context is not None else load_soul_context(agent_id)
    seeds = dict(seeds) if seeds is not None else {}

    # ── 逐 origin_type 蒐集／取用種子（必填上下文不足 ⇒ None）──
    if origin_type == "goal_driven":
        if "goal_seeds" not in seeds:
            seeds["goal_seeds"] = collect_goal_seeds(agent_id, goals)
        if not seeds["goal_seeds"]:
            logger.info(LOG_NO_PROMPT.format(agent_id=agent_id, origin_type=origin_type, reason="no_active_goal"))
            return None
        if not ctx:
            logger.info(LOG_NO_PROMPT.format(agent_id=agent_id, origin_type=origin_type, reason="no_soul_context"))
            return None
        body = "\n\n".join(
            _ORIGIN_BLOCKS[origin_type]["template"].format(
                goal_title=g["title"], goal_state=g["state"]
            )
            for g in seeds["goal_seeds"]
        )
        context_block = _render_goal_context(seeds["goal_seeds"])
        fact_block = ""

    elif origin_type == "necessity_driven":
        if "necessity_seeds" not in seeds:
            seeds["necessity_seeds"] = collect_necessity_seeds(agent_id, now, diary)
        ns = seeds["necessity_seeds"]
        if _clean_str(ns.get("slot")) not in SLOT_VALUES:
            logger.info(LOG_NO_PROMPT.format(agent_id=agent_id, origin_type=origin_type, reason="no_slot"))
            return None
        body = _ORIGIN_BLOCKS[origin_type]["template"].format(
            slot=ns.get("slot"), local_time=ns.get("local_time"), situation=ns.get("situation")
        )
        context_block = _render_necessity_context(ns)
        fact_block = ""

    elif origin_type == "whim_driven":
        if "whim_seeds" not in seeds:
            seeds["whim_seeds"] = collect_whim_seeds(agent_id, ctx)
        if not _clean_str(seeds["whim_seeds"].get("soul_context")):
            logger.info(LOG_NO_PROMPT.format(agent_id=agent_id, origin_type=origin_type, reason="no_soul_context"))
            return None
        ctx = seeds["whim_seeds"]["soul_context"]  # 純內生：只此一源
        body = _ORIGIN_BLOCKS[origin_type]["template"]
        context_block = ""      # 🔴 純內生源：不注入時間／diary／世界事實
        fact_block = ""

    else:  # world_collision
        if "world_seeds" not in seeds:
            seeds["world_seeds"] = collect_world_seeds(agent_id, now, world_records)
        facts = seeds["world_seeds"].get("facts") or []
        if not facts:
            # 無命中 ⇒ 不組 prompt、不捏造（§5.2.4 執行點）
            logger.info(LOG_NO_PROMPT.format(agent_id=agent_id, origin_type=origin_type, reason="no_world_fact"))
            return None
        if not ctx:
            logger.info(LOG_NO_PROMPT.format(agent_id=agent_id, origin_type=origin_type, reason="no_soul_context"))
            return None
        primary = facts[0]
        body = _ORIGIN_BLOCKS[origin_type]["template"].format(
            fact_summary=primary["summary"]
        )
        fact_block = _render_world_facts(facts)
        context_block = f"[當前時間]\n{seeds['world_seeds'].get('local_time')}"

    due_block = _render_due_threads(agent_id, now, due_threads)
    cap_block = _render_capacity(agent_id)

    system = "\n\n".join(
        part
        for part in [
            f"你是 Soul OS 的角色 `{agent_id}`。以下是這一輪「生活推進」的完整指示。",
            (f"[你的個性語境]\n{ctx}" if ctx else ""),
            cap_block,
            # 注意：`_OUTPUT_CONTRACT` 內含 JSON 範例的**字面大括號**，
            # 故用 `replace` 而非 `format`（避免被當成 format field）。
            _OUTPUT_CONTRACT.replace("{origin_type}", origin_type),
            _ORIGIN_BLOCKS[origin_type]["forbidden"],
        ]
        if part
    )

    user = "\n\n".join(
        part
        for part in [due_block, fact_block, context_block, f"[本輪指示]\n{body}"]
        if part
    )

    return {"system": system, "user": user}


def _render_goal_context(goal_seeds: Sequence[Dict[str, str]]) -> str:
    lines = ["[你的長期意向（唯讀種子）]"]
    lines += [f"- 「{g['title']}」（狀態：{g['state']}）" for g in goal_seeds]
    return "\n".join(lines)


def _render_necessity_context(ns: Dict[str, Any]) -> str:
    lines = ["[當前處境]", f"- 當前時段：{ns.get('slot')}", f"- 當前時間：{ns.get('local_time')}"]
    entries = ns.get("diary") or []
    if entries:
        lines.append("- 你近幾日的內在生活片段：")
        for e in entries:
            if isinstance(e, dict):
                lines.append(f"  - [{e.get('date')} {e.get('slot')}] {e.get('content')}")
    return "\n".join(lines)


def _render_world_facts(facts: Sequence[Dict[str, str]]) -> str:
    lines = ["[Lived Context：剛才真實發生的世界事實]"]
    for f in facts:
        lines.append(f"- [{f.get('source')}/{f.get('event_type')}] {f.get('summary')}")
    return "\n".join(lines)


def _render_due_threads(
    agent_id: str,
    now: Optional[datetime],
    due_threads: Optional[Sequence[Any]],
) -> str:
    """§5.1 第 1 件事：對**到期**線頭推進敘事（或判定終態）。

    到期的判定：`status == "active"` 且 `check_after_ts <= now`（§4.2 判定 1 同口徑）。
    """
    if due_threads is None:
        try:
            active = lt.list_active(agent_id)
        except Exception as e:
            logger.warning(f"[LifeThreadOrigin] list_active 失敗 (fail-silent): {e}")
            active = []
        now_utc = _now_utc(now)
        due: List[Any] = []
        for t in active:
            if not isinstance(t, dict):
                continue
            ts = _parse_ts(t.get("check_after_ts"))
            if ts is not None and ts <= now_utc:
                due.append(t)
    else:
        due = [t for t in due_threads if isinstance(t, dict)]

    if not due:
        return "[到期線頭]\n（這一輪沒有到期的線頭。）"
    lines = ["[到期線頭]（值得重新檢視；可 advance／complete／abandon）"]
    for t in due:
        lines.append(
            f"- thread_id={t.get('thread_id')} title=「{t.get('title')}」"
            f" status={t.get('status')} check_after_ts={t.get('check_after_ts')}"
        )
        narrative = _clean_str(t.get("narrative_content"))
        if narrative:
            lines.append(f"  目前敘事：{narrative[:200]}")
    return "\n".join(lines)


def _render_capacity(agent_id: str) -> str:
    """容量提示（**計數**，非評分；§2.6.2／§5.1）。"""
    try:
        active = lt.active_count(agent_id)
        cap = lt.capacity(agent_id)
    except Exception as e:
        logger.warning(f"[LifeThreadOrigin] 容量讀取失敗 (fail-silent): {e}")
        return ""
    return (
        f"[容量]\n目前活躍線頭 {active} 條，上限 {cap} 條。"
        f"只有 `active < {cap}` 時才可以 `op=\"create\"`；否則只做推進／結案。"
    )


# ══════════════════════════════════════════════════════════════
# §5.1 action 套用（一律經 M1 公開 API；0 繞道、0 直接寫檔）
# ══════════════════════════════════════════════════════════════

def _clamp_hours(value: Any) -> Optional[int]:
    """§5.1：`next_check_hours` 為整數時距，clamp 至 [1, 72]。

    非數值／缺失 ⇒ `None`（**不發明預設值**；該 action 視為不合法 ⇒ 不落盤）。
    """
    if isinstance(value, bool):
        return None
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return None
    if hours < NEXT_CHECK_HOURS_MIN:
        return NEXT_CHECK_HOURS_MIN
    if hours > NEXT_CHECK_HOURS_MAX:
        return NEXT_CHECK_HOURS_MAX
    return hours


def _check_after_iso(now_utc: datetime, hours: Optional[int]) -> Optional[str]:
    if hours is None:
        return None
    return (now_utc + timedelta(hours=hours)).isoformat()


def _safe_share_target(value: Any) -> str:
    """非法 `share_target` ⇒ fail-closed 回 `"none"`（純內在）。"""
    text = _clean_str(value)
    if text in lt.SHARE_TARGET_FIXED:
        return text
    # 只用 M1 的**公開**常數驗證（不呼叫 M1 私有函式）。
    if lt.SHARE_TARGET_PATTERN.match(text):
        return text
    return "none"


def parse_actions(raw: Any) -> List[Dict[str, Any]]:
    """§5.1：解析 `{"actions": [...]}`，截斷至前 `MAX_ACTIONS_PER_ROUND` 項。

    LLM 失敗／非 JSON／`actions` 非 list ⇒ 回 `[]`（呼叫端據此**不落盤**）。
    """
    data = _extract_json_dict(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else None)
    if not isinstance(data, dict):
        return []
    actions = data.get("actions")
    if not isinstance(actions, list):
        return []
    return [a for a in actions if isinstance(a, dict)][:MAX_ACTIONS_PER_ROUND]


def apply_actions(
    agent_id: str,
    actions: Sequence[Dict[str, Any]],
    *,
    origin_type: str,
    now: Optional[datetime] = None,
    dissolve_hook: Optional[Callable[..., Any]] = None,
) -> Dict[str, List[str]]:
    """把 §5.1 的 actions 套用到 M1（**唯一寫入路徑**）。

    - `create`：`lt.create_thread()`。**容量防線由 M1 唯一強制**（§2.6.3）；
      超限時 M1 回 `None` ⇒ 本模組視為**丟棄該 action**（§5.1），**不繞道寫檔**。
    - `advance`：`lt.append_updated()`。
    - `complete` / `abandon`：`lt.append_transition()`；成功後才觸發 `dissolve_hook`（M2 接縫）。
    - 任何 M1 例外（含 `ValueError` 欄位驗證）一律**吞掉**：fail-silent、**不中斷呼叫端**。
    """
    now_utc = _now_utc(now)
    result: Dict[str, List[str]] = {
        "created": [],
        "advanced": [],
        "transitioned": [],
        "skipped": [],
    }

    for act in actions:
        op = _clean_str(act.get("op"))
        hours = _clamp_hours(act.get("next_check_hours"))
        check_after = _check_after_iso(now_utc, hours)
        narrative = _clean_str(act.get("narrative_content"))

        try:
            if op == "create":
                title = _clean_str(act.get("title"))
                act_origin = _clean_str(act.get("origin_type"))
                if act_origin not in lt.ORIGIN_TYPES:
                    act_origin = origin_type
                if not title or not narrative or hours is None:
                    result["skipped"].append("create:incomplete")
                    continue
                thread_id = lt.create_thread(
                    agent_id,
                    title=title,
                    narrative_content=narrative,
                    origin_type=act_origin,
                    share_target=_safe_share_target(act.get("share_target")),
                    check_after_ts=check_after,
                )
                if thread_id is None:
                    # 容量拒絕（M1 已寫 §9 O6 觀測行）⇒ 丟棄該 action（§5.1）
                    result["skipped"].append("create:rejected")
                    continue
                result["created"].append(thread_id)
                logger.info(
                    LOG_CREATED.format(
                        agent_id=agent_id,
                        thread_short=_short(thread_id),
                        origin_type=act_origin,
                        share_target=_safe_share_target(act.get("share_target")),
                    )
                )

            elif op == "advance":
                thread_id = _clean_str(act.get("thread_id"))
                if not thread_id or not narrative:
                    result["skipped"].append("advance:incomplete")
                    continue
                ok = lt.append_updated(
                    agent_id,
                    thread_id,
                    narrative_content=narrative,
                    check_after_ts=check_after,
                )
                if ok:
                    result["advanced"].append(thread_id)
                else:
                    result["skipped"].append("advance:rejected")

            elif op in _OP_TO_STATUS:
                to_status = _OP_TO_STATUS[op]
                thread_id = _clean_str(act.get("thread_id"))
                if not thread_id:
                    result["skipped"].append(f"{op}:incomplete")
                    continue
                ok = lt.append_transition(agent_id, thread_id, to_status)
                if not ok:
                    result["skipped"].append(f"{op}:rejected")
                    continue
                result["transitioned"].append(thread_id)
                _call_dissolve_hook(dissolve_hook, agent_id, thread_id, to_status)

            else:
                result["skipped"].append("unknown_op")

        except Exception as e:
            # §2.5 精神：不 raise、不中斷呼叫端
            logger.warning(
                f"[LifeThreadOrigin] action 套用失敗 (fail-silent) op={op!r}: "
                f"{type(e).__name__}: {e}"
            )
            result["skipped"].append(f"{op or 'unknown'}:error")

    return result


def _call_dissolve_hook(hook: Optional[Callable[..., Any]], agent_id: str, thread_id: str, status: str) -> None:
    """§5.1 溶解接縫（**M2 的職責**；未注入 ⇒ 不呼叫 ＝ §8 路徑 B 0 成本）。"""
    if hook is None:
        return
    try:
        out = hook(agent_id, thread_id, status)
        if inspect.isawaitable(out):
            logger.warning(
                "[LifeThreadOrigin] dissolve_hook 回傳 awaitable，請改注入同步 hook "
                "或在 run_origin_round 內 await"
            )
    except Exception as e:
        logger.warning(f"[LifeThreadOrigin] dissolve_hook 失敗 (fail-silent): {e}")


# ══════════════════════════════════════════════════════════════
# 對外入口：一輪喚醒（§5.1）
# ══════════════════════════════════════════════════════════════

async def _generate(prompt: Dict[str, str], agent_id: str, llm_caller: Optional[Callable[..., Any]]) -> Optional[str]:
    """呼叫**既有** LLM 通道（§8.1 路徑 A）。失敗一律回 `None`（fail-silent）。"""
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]},
    ]

    if llm_caller is not None:
        try:
            out = llm_caller(messages, agent_id)
            if inspect.isawaitable(out):
                out = await out
            return out if isinstance(out, str) else None
        except Exception as e:
            logger.warning(f"[LifeThreadOrigin] 注入 llm_caller 失敗 (fail-silent): {e}")
            return None

    proxy = _find_llm_proxy()
    if proxy is None:
        logger.warning(LOG_NO_PROXY.format(agent_id=agent_id))
        return None
    try:
        async with LLM_CONCURRENCY_LIMIT:
            return await proxy.generate_text(
                messages=messages,
                agent_id=agent_id,
                max_tokens=GENERATE_MAX_TOKENS,
                temperature=GENERATE_TEMPERATURE,
            )
    except Exception as e:
        logger.warning(f"[LifeThreadOrigin] generate_text 失敗 (fail-silent): {e}")
        return None


async def run_origin_round(
    agent_id: str,
    origin_type: str,
    *,
    now: Optional[datetime] = None,
    seeds: Optional[Dict[str, Any]] = None,
    due_threads: Optional[Sequence[Any]] = None,
    llm_caller: Optional[Callable[..., Any]] = None,
    dissolve_hook: Optional[Callable[..., Any]] = None,
    build_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """**M4 的對外入口**：一輪「生活推進詮釋」（§5.1）。

    🔴 **已接線（LIFE-THREAD-M5）**：本函式由 `src/soul/life_thread_orchestrator.py`
    於 `morning` / `night` slot 觸發窗內**生產呼叫**（**唯一**喚醒動作）；
    白名單外的任何其他生產路徑**不得**呼叫本函式（§10.1）。

    行為：
      1. 依 `origin_type` 蒐集種子 → 組 prompt（§5.1 骨架 ＋ §5.2 模板）。
      2. 必填上下文不足 ⇒ **不呼叫 LLM、不落盤**（回 `prompt_available=False`）。
      3. 呼叫**恰好 1 次**既有 LLM 通道（路徑 A）；非 JSON／失敗 ⇒ **不落盤**。
      4. 解析 `actions`（截斷至 3 項）→ 經 M1 公開 API 寫入。
      5. 全程 fail-silent：**永不 raise、不中斷呼叫端**。

    回傳（key 集合**不含**任何禁用欄位）：
      `agent_id` / `origin_type` / `prompt_available` / `called` / `llm_calls` /
      `created` / `advanced` / `transitioned` / `skipped` / `dissolved`
    """
    result: Dict[str, Any] = {
        "agent_id": agent_id,
        "origin_type": origin_type,
        "prompt_available": False,
        "called": False,
        "llm_calls": 0,
        "created": [],
        "advanced": [],
        "transitioned": [],
        "skipped": [],
        "dissolved": [],
    }

    kwargs = dict(build_kwargs or {})
    kwargs.setdefault("now", now)
    kwargs.setdefault("seeds", seeds)
    kwargs.setdefault("due_threads", due_threads)

    prompt = build_origin_prompt(agent_id, origin_type, **kwargs)
    if prompt is None:
        return result  # 0 LLM 呼叫、0 落盤（fail-silent）

    result["prompt_available"] = True

    # 無任何既有 LLM 通道可用（無注入 caller 且無 process-global proxy）
    # ⇒ **0 呼叫**（不得打真網路）、不落盤、不 raise。
    if llm_caller is None and _find_llm_proxy() is None:
        logger.warning(LOG_NO_PROXY.format(agent_id=agent_id))
        return result

    raw = await _generate(prompt, agent_id, llm_caller)
    result["called"] = True
    result["llm_calls"] = 1

    actions = parse_actions(raw)
    if not actions:
        logger.warning(LOG_LLM_FAILED.format(agent_id=agent_id, origin_type=origin_type))
        return result  # 非 JSON／失敗 ⇒ 本時段不落盤任何事件（§5.1）

    applied = apply_actions(
        agent_id, actions, origin_type=origin_type, now=now, dissolve_hook=dissolve_hook
    )
    result["created"] = applied["created"]
    result["advanced"] = applied["advanced"]
    result["transitioned"] = applied["transitioned"]
    result["skipped"] = applied["skipped"]
    if dissolve_hook is not None and applied["transitioned"]:
        result["dissolved"] = list(applied["transitioned"])
        result["llm_calls"] += 1  # §8.1 路徑 B（由 DISSOLVE_MAX_PER_DAY 封頂）

    logger.info(
        LOG_ROUND.format(
            agent_id=agent_id,
            origin_type=origin_type,
            llm_calls=result["llm_calls"],
            created=len(result["created"]),
            advanced=len(result["advanced"]),
            transitioned=len(result["transitioned"]),
            skipped=len(result["skipped"]),
        )
    )
    return result
