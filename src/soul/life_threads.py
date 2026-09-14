# src/soul/life_threads.py
# Soul OS — LIFE-THREAD-ENGINE M1: 生活線頭資料模型 ＋ 輕量存儲 ＋ 狀態機 ＋ 容量防線
"""
LIFE-THREAD-M1-1 — 模組 1（資料模型／輕量存儲／狀態機／容量防線）。

規格唯一來源：`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §2（`:70-225`）。
本模組**只做 M1**（§10.2）：
  - 0 LLM 呼叫、0 新定時器、0 新事件類型、0 新投遞通道。
  - 0 既有模組介接（**不得被任何既有生產路徑 import**；後續模組才接線）。
  - 0 Frozen Contract 變更（§12 九項全「無」）。

落盤（§2.1）：
  `data_root()/soul/<agent_id>/life_threads.jsonl`（per-agent 單檔、append-only）。
  路徑**必須**經 `src/paths.py` 的 `data_root()` 組出（不得硬編碼 `data/`）。
  UTF-8、**無 BOM**、LF 行尾、`ensure_ascii=False`。

per-agent 隔離（INV-4）：以「**路徑分割 ＋ 讀寫 API 只接受一個 `agent_id`**」自我保證，
**不依賴**身份防火牆（SI-2.1 防線 3）—— 該防線在生產未接線（§2.1）。

寫入紀律（§2.4）：append-only 事件列，永不就地改寫或重寫整檔。
  `created` / `updated` / `transitioned` / `dissolved` 四種事件類型共用同一
  `thread_id`，讀側（fold）取 `(updated_at, event_seq)` 字典序最大者為當前狀態。

狀態機（§2.5）：`active ⇄ dormant`、`active|dormant → completed|abandoned`，
  終態（`completed` / `abandoned`）**不可復活**。非法轉移＝不寫入 ＋ WARNING ＋
  回 `False`，**不得 raise、不得中斷呼叫端**。

容量防線（§2.6）：`LIFE_THREAD_ACTIVE_CAP_MIN/DEFAULT/HARD_MAX = 1/2/3`；
  強制點唯一 —— `create_thread()` 在寫入 `created` 事件**之前**。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.paths import data_root  # §2.1：路徑一律經 data_root() 組出

logger = logging.getLogger("soul_os.soul.life_threads")


# ──────────────────────────────────────────────────────────────
# §2.6.1 上限常數（三個，全部整數計數，**非評分**）
# ──────────────────────────────────────────────────────────────

LIFE_THREAD_ACTIVE_CAP_MIN = 1      # 下限（常態帶 1~3 的左端）
LIFE_THREAD_ACTIVE_CAP_DEFAULT = 2  # 未宣告時的 fail-closed 值
LIFE_THREAD_ACTIVE_CAP_HARD_MAX = 3  # 全域硬上限（VISION「常態維持 1~3 條」的右端）

#: §2.6.2 選用鍵名（落在 `configs/default.yaml` 的 `agents[]` 既有結構上）。
LIFE_THREAD_CAPACITY_KEY = "life_thread_capacity"


# ──────────────────────────────────────────────────────────────
# §2.3 / §2.2 值域
# ──────────────────────────────────────────────────────────────

#: §2.3 `origin_type` 4 值（**不發明第五個**；OQ-1 之第五項「零線頭」不落盤）。
ORIGIN_TYPES: Tuple[str, ...] = (
    "goal_driven",
    "necessity_driven",
    "whim_driven",
    "world_collision",
)

#: §2.2 `status` 4 值。
STATUS_VALUES: Tuple[str, ...] = ("active", "dormant", "completed", "abandoned")

#: §2.5 終態：不可復活、不再佔活躍額度。
TERMINAL_STATUSES: Tuple[str, ...] = ("completed", "abandoned")

#: §2.2 `share_target` 固定值域（`agent_<id>` 另以樣式判定）。
SHARE_TARGET_FIXED: Tuple[str, ...] = ("none", "lounge", "user_bryan")
SHARE_TARGET_PATTERN = re.compile(r"^agent_[A-Za-z0-9_-]{1,64}$")

#: §2.4 四種事件類型。
EVENT_CREATED = "created"
EVENT_UPDATED = "updated"
EVENT_TRANSITIONED = "transitioned"
EVENT_DISSOLVED = "dissolved"
EVENT_TYPES: Tuple[str, ...] = (
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_TRANSITIONED,
    EVENT_DISSOLVED,
)

#: §2.2 欄位長度界線。
TITLE_MIN_LEN = 1
TITLE_MAX_LEN = 40
NARRATIVE_MIN_LEN = 1
NARRATIVE_MAX_LEN = 600

#: §2.2 **禁用欄位**（VISION §2.4 No-Scoring 剛性邊界）。
FORBIDDEN_FIELDS = frozenset(
    {"score", "weight", "intensity", "urgency", "priority", "longing", "confidence"}
)

#: §2.2 「禁止佔位符」的判定集合（去首尾空白 ＋ 大小寫無關）。
_PLACEHOLDER_TOKENS = frozenset(
    {"", "-", "--", "---", "...", "…", "n/a", "na", "none", "null", "nil",
     "tbd", "todo", "xxx", "placeholder", "占位符", "待補", "無", "略"}
)

#: §2.5 允許的轉移圖（來源狀態 → 可轉移集合）。終態為空集合＝不可復活。
_TRANSITIONS: Dict[str, frozenset] = {
    "active": frozenset({"dormant", "completed", "abandoned"}),
    "dormant": frozenset({"active", "completed", "abandoned"}),
    "completed": frozenset(),
    "abandoned": frozenset(),
}

_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_AGENT_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: §2.1 檔名（單檔、append-only）。
LIFE_THREADS_FILENAME = "life_threads.jsonl"


# ──────────────────────────────────────────────────────────────
# §9 觀測格式（O5 / O6 / 非法轉移 WARNING 逐字格式）
# ──────────────────────────────────────────────────────────────

#: §2.5 非法轉移 WARNING **逐字格式**（不得改字）。
WARNING_ILLEGAL_TRANSITION = (
    "[LifeThread] 拒絕非法轉移 {thread_id}: {from_status} → {to_status} (ignored)"
)

#: §9 O5 狀態機觀測行。
LOG_TRANSITION = "[LifeThread] transition agent={agent_id} thread={thread_short} {from_status}→{to_status}"

#: §9 O6 容量防線觀測行（create rejected）。
LOG_CAPACITY_REJECTED = "[LifeThread] cap agent={agent_id} active={active} cap={cap} (create rejected)"


# ──────────────────────────────────────────────────────────────
# 時間／ID 工具
# ──────────────────────────────────────────────────────────────

def now_utc_iso() -> str:
    """當前 UTC 時刻（ISO 8601，帶時區）。沿用 `src/soul/motive.py:221` 形態。"""
    return datetime.now(timezone.utc).isoformat()


def new_thread_id() -> str:
    """新增線頭身分（UUID4，36 字元）。"""
    return str(uuid.uuid4())


def _validate_agent_id(agent_id: Any) -> str:
    if not isinstance(agent_id, str) or not _AGENT_ID_PATTERN.match(agent_id):
        raise ValueError(f"invalid agent_id: {agent_id!r}")
    if ".." in agent_id or "/" in agent_id or "\\" in agent_id:
        raise ValueError(f"invalid agent_id (path traversal): {agent_id!r}")
    return agent_id


def _validate_thread_id(thread_id: Any) -> str:
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError(f"invalid thread_id: {thread_id!r}")
    return thread_id


def _iso_or_none(value: Any) -> Optional[str]:
    """ISO 8601 字串｜None 的寬鬆驗證（回傳原值或 None）。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid timestamp: {value!r}")
    return value


def _validate_title(title: Any) -> str:
    if not isinstance(title, str):
        raise ValueError("title must be a str")
    if not (TITLE_MIN_LEN <= len(title) <= TITLE_MAX_LEN):
        raise ValueError(
            f"title length must be {TITLE_MIN_LEN}..{TITLE_MAX_LEN}, got {len(title)}"
        )
    if not title.strip():
        raise ValueError("title must not be blank after strip")
    return title


def _validate_narrative(narrative: Any, title: str) -> str:
    """§2.2 `narrative_content`：1–600 字元、不得空、不得佔位符、不得等同 `title`。"""
    if not isinstance(narrative, str):
        raise ValueError("narrative_content must be a str")
    if not (NARRATIVE_MIN_LEN <= len(narrative) <= NARRATIVE_MAX_LEN):
        raise ValueError(
            f"narrative_content length must be {NARRATIVE_MIN_LEN}..{NARRATIVE_MAX_LEN}, "
            f"got {len(narrative)}"
        )
    if narrative.lower().strip() in _PLACEHOLDER_TOKENS:
        raise ValueError("narrative_content must not be a placeholder")
    if narrative.strip() == title.strip():
        raise ValueError("narrative_content must not equal title")
    return narrative


def _validate_origin_type(origin_type: Any) -> str:
    if origin_type not in ORIGIN_TYPES:
        raise ValueError(f"origin_type must be one of {ORIGIN_TYPES}, got {origin_type!r}")
    return origin_type


def _validate_status(status: Any) -> str:
    if status not in STATUS_VALUES:
        raise ValueError(f"status must be one of {STATUS_VALUES}, got {status!r}")
    return status


def _validate_share_target(share_target: Any) -> str:
    if share_target in SHARE_TARGET_FIXED:
        return share_target
    if isinstance(share_target, str) and SHARE_TARGET_PATTERN.match(share_target):
        return share_target
    raise ValueError(f"invalid share_target: {share_target!r}")


def _assert_no_forbidden_fields(entry: Dict[str, Any]) -> None:
    """§2.2 禁用欄位硬斷言：寫入 dict 的 key 集合不得含任何評分鍵。"""
    hit = set(entry.keys()) & FORBIDDEN_FIELDS
    if hit:
        raise ValueError(f"forbidden field(s) in life-thread entry: {sorted(hit)}")


# ──────────────────────────────────────────────────────────────
# §2.1 路徑（per-agent，經 data_root()）
# ──────────────────────────────────────────────────────────────

def life_threads_path(agent_id: str) -> Path:
    """`data_root()/soul/<agent_id>/life_threads.jsonl`（per-agent 單檔）。"""
    agent = _validate_agent_id(agent_id)
    if not _SAFE_AGENT_SEGMENT.match(agent):
        # 路徑分割的第一道自我保證：agent_id 必須是安全的路徑段。
        raise ValueError(f"agent_id is not a safe path segment: {agent_id!r}")
    return data_root() / "soul" / agent / LIFE_THREADS_FILENAME


# ──────────────────────────────────────────────────────────────
# §2.4 寫入紀律：append-only（永不就地改寫、永不重寫整檔）
# ──────────────────────────────────────────────────────────────

def _append_entry(agent_id: str, entry: Dict[str, Any]) -> bool:
    """單列 append。永不 raise、永不重寫整檔（§2.4 崩潰一致性）。"""
    try:
        _assert_no_forbidden_fields(entry)
        path = life_threads_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:  # fail-silent：只 warning，不中斷呼叫端
        logger.warning(f"[LifeThread] append failed ({agent_id}): {e}")
        return False


# ──────────────────────────────────────────────────────────────
# §2.4 fold 讀取語意（三條規則）
# ──────────────────────────────────────────────────────────────

def read_entries(agent_id: str) -> List[Dict[str, Any]]:
    """逐列讀取**可用**的 JSON 物件列。

    fold 規則第 1 條：跳過空行與 `json.loads` 失敗的列（沿用
    `src/llm/proxy.py:288-291` 的「壞行跳過不 crash」慣例）；結構缺鍵的列同樣跳過。
    檔案不存在／不可讀 ⇒ 回空清單（fail-silent，永不 raise）。
    """
    try:
        path = life_threads_path(agent_id)
    except Exception as e:
        logger.warning(f"[LifeThread] resolve path failed ({agent_id}): {e}")
        return []
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue  # 空行跳過
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 壞行跳過不 crash
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if not _is_usable_entry(obj):
                    continue
                entries.append(obj)
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning(f"[LifeThread] read failed ({agent_id}): {e}")
        return []
    return entries


def _is_usable_entry(obj: Dict[str, Any]) -> bool:
    """fold 可用的最小結構：`thread_id` / `updated_at` / `event_seq` 齊備且型別正確。"""
    tid = obj.get("thread_id")
    if not isinstance(tid, str) or not tid:
        return False
    if not isinstance(obj.get("updated_at"), str) or not obj.get("updated_at"):
        return False
    seq = obj.get("event_seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return False
    return True


def _fold_sort_key(entry: Dict[str, Any]) -> Tuple[str, int]:
    return (entry.get("updated_at") or "", entry.get("event_seq") or 0)


def fold(agent_id: str) -> Dict[str, Dict[str, Any]]:
    """§2.4 fold：`thread_id` → 當前狀態。

    規則 2：每組取 `(updated_at, event_seq)` **字典序最大者**為當前狀態
            （同秒衝突由 `event_seq` 破除）。
    規則 3：`dissolved_at` / `sage_fact_id` 採「**最後一個非 null 值勝出**」
            （檔案順序掃描，避免 `updated` 事件把已溶解的憑據洗掉）。
    """
    states: Dict[str, Dict[str, Any]] = {}
    creds: Dict[str, Dict[str, Any]] = {}
    for entry in read_entries(agent_id):
        tid = entry["thread_id"]
        prev = states.get(tid)
        if prev is None or _fold_sort_key(entry) >= _fold_sort_key(prev):
            states[tid] = entry
        # 規則 3：最後一個非 null 值勝出（不隨 fold 勝者改變）
        slot = creds.setdefault(tid, {"dissolved_at": None, "sage_fact_id": None})
        for key in ("dissolved_at", "sage_fact_id"):
            value = entry.get(key)
            if value is not None:
                slot[key] = value
    for tid, state in states.items():
        merged = dict(state)
        merged.update(creds.get(tid, {}))
        states[tid] = merged
    return states


def get_state(agent_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
    """單一線頭的當前狀態（fold 後）；不存在回 `None`。"""
    return fold(agent_id).get(thread_id)


def next_event_seq(agent_id: str, thread_id: str) -> int:
    """該線頭下一個 `event_seq`（同檔單調遞增，≥1）。"""
    seqs = [
        e.get("event_seq", 0)
        for e in read_entries(agent_id)
        if e.get("thread_id") == thread_id
    ]
    highest = max([s for s in seqs if isinstance(s, int) and not isinstance(s, bool)] or [0])
    return highest + 1


def active_count(agent_id: str) -> int:
    """§2.6.3 計數口徑：fold 後 `status == "active"` 的線頭數。

    `dormant` 不佔額度；`completed` / `abandoned` 不佔額度（終態）。
    """
    return sum(1 for s in fold(agent_id).values() if s.get("status") == "active")


def terminal_count(agent_id: str) -> int:
    """fold 後 `status ∈ {completed, abandoned}` 的線頭數（觀測面 O11/O12）。"""
    return sum(
        1 for s in fold(agent_id).values() if s.get("status") in TERMINAL_STATUSES
    )


# ──────────────────────────────────────────────────────────────
# §2.6.2 容量（可判定規則，四步；fail-closed 回 2）
# ──────────────────────────────────────────────────────────────

#: `configs/default.yaml`（相對於本檔 `src/soul/`）。**不碰 personas／SOUL.md**。
_DEFAULT_CONFIG_RELPATH = ("configs", "default.yaml")


def _load_default_config() -> Dict[str, Any]:
    """載入 `configs/default.yaml`（既有 `configs/loader.py`；0 新載入器）。"""
    from configs.loader import load_config

    return load_config() or {}


def _find_agent_entry(cfg: Any, agent_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(cfg, dict):
        return None
    agents = cfg.get("agents")
    if not isinstance(agents, list):
        return None
    for entry in agents:
        if isinstance(entry, dict) and entry.get("id") == agent_id:
            return entry
    return None


def capacity(agent_id: str, config_path: Optional[str] = None) -> int:
    """§2.6.2 `capacity(agent_id)`：per-agent 生活線頭活躍上限（整數計數，非評分）。

    1. 讀 `configs/default.yaml` 的 `agents[]` 中 `id == agent_id` 的條目
    2. 取選用鍵 `life_thread_capacity`（int）
    3. 缺鍵／型別非 int／值不在 `[1,3]` ⇒ 回 `LIFE_THREAD_ACTIVE_CAP_DEFAULT`（=2，fail-closed）
    4. 否則回該值

    本函式**不引用任何人格檔（`personas/agent_*.md`）內容**（§2.6.2：以散文推導容量＝
    主觀打分＝違反 VISION §2.4「No-Scoring」）。
    """
    try:
        if config_path is None:
            cfg = _load_default_config()
        else:  # 測試／未來 per-agent 宣告面用的顯式覆寫（預設路徑不受影響）
            import yaml

            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        entry = _find_agent_entry(cfg, agent_id)
        if entry is None:
            return LIFE_THREAD_ACTIVE_CAP_DEFAULT
        raw = entry.get(LIFE_THREAD_CAPACITY_KEY)
        if isinstance(raw, bool) or not isinstance(raw, int):
            return LIFE_THREAD_ACTIVE_CAP_DEFAULT
        if raw < LIFE_THREAD_ACTIVE_CAP_MIN or raw > LIFE_THREAD_ACTIVE_CAP_HARD_MAX:
            return LIFE_THREAD_ACTIVE_CAP_DEFAULT
        return raw
    except Exception as e:  # fail-closed：任何例外一律回保守值
        logger.warning(f"[LifeThread] capacity lookup failed ({agent_id}): {e}")
        return LIFE_THREAD_ACTIVE_CAP_DEFAULT


# ──────────────────────────────────────────────────────────────
# §2.5 狀態機
# ──────────────────────────────────────────────────────────────

def allowed_transition(from_status: Any, to_status: Any) -> bool:
    """§2.5 轉移圖判定（真值表見契約）。

    - `active` → `dormant` / `completed` / `abandoned`
    - `dormant` → `active` / `completed` / `abandoned`
    - `completed` / `abandoned` → **無**（終態不可復活）：對所有 X 恆 `False`
    未定義的來源狀態／不可雜湊的值一律 `False`（永不 raise）。
    """
    try:
        return to_status in _TRANSITIONS.get(from_status, frozenset())
    except TypeError:  # unhashable to_status
        return False


def is_terminal(status: str) -> bool:
    """是否為終態（不可復活、不佔活躍額度）。"""
    return status in TERMINAL_STATUSES


# ──────────────────────────────────────────────────────────────
# 公開寫入 API
# ──────────────────────────────────────────────────────────────

def create_thread(
    agent_id: str,
    title: str,
    narrative_content: str,
    origin_type: str,
    share_target: str = "none",
    check_after_ts: Optional[str] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> Optional[str]:
    """建立新線頭（寫入 `created` 事件，`status="active"`）。

    §2.6.3 **容量強制點（唯一）**：在寫入 `created` 事件**之前**檢查
    `active_count(agent_id) >= capacity(agent_id)`；超限 ⇒ **回 `None`**、
    **檔案列數不變**、寫一行 §9 O6 觀測行。

    回傳：新 `thread_id`（成功）／`None`（容量拒絕或寫入失敗，fail-silent）。
    """
    agent = _validate_agent_id(agent_id)
    title = _validate_title(title)
    narrative_content = _validate_narrative(narrative_content, title)
    origin_type = _validate_origin_type(origin_type)
    share_target = _validate_share_target(share_target)
    check_after_ts = _iso_or_none(check_after_ts)

    # ── 容量強制點（在寫入之前）──────────────────────────────
    cap = capacity(agent)
    active = active_count(agent)
    if active >= cap:
        logger.info(
            LOG_CAPACITY_REJECTED.format(agent_id=agent, active=active, cap=cap)
        )
        return None

    ts = updated_at or created_at or now_utc_iso()
    entry: Dict[str, Any] = {
        "thread_id": new_thread_id(),
        "event_seq": 1,
        "event_type": EVENT_CREATED,
        "origin_type": origin_type,
        "status": "active",
        "title": title,
        "narrative_content": narrative_content,
        "created_at": created_at or ts,
        "updated_at": ts,
        "check_after_ts": check_after_ts,
        "share_target": share_target,
        "dissolved_at": None,
        "sage_fact_id": None,
    }
    if not _append_entry(agent, entry):
        return None
    return entry["thread_id"]


def append_updated(
    agent_id: str,
    thread_id: str,
    narrative_content: Optional[str] = None,
    check_after_ts: Optional[str] = None,
    share_target: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> bool:
    """追加 `updated` 事件（敘事推進／`check_after_ts` 改期／`share_target` 變更）。

    `status` 沿用當前狀態（不變）。線頭不存在 ⇒ 回 `False`（fail-silent）。
    帶 `narrative_content` 時逐條套用 §2.2 敘事規格（非空、非佔位符、不得等同 `title`）。
    """
    agent = _validate_agent_id(agent_id)
    thread_id = _validate_thread_id(thread_id)
    if share_target is not None:
        share_target = _validate_share_target(share_target)
    check_after_ts = _iso_or_none(check_after_ts)

    state = get_state(agent, thread_id)
    if state is None:
        logger.warning(f"[LifeThread] updated ignored (unknown thread): {thread_id}")
        return False
    if narrative_content is not None:
        narrative_content = _validate_narrative(
            narrative_content, str(state.get("title") or "")
        )
    if check_after_ts is not None and is_terminal(str(state.get("status"))):
        logger.warning(
            f"[LifeThread] updated ignored (terminal thread): {thread_id}"
        )
        return False
    entry: Dict[str, Any] = {
        "thread_id": thread_id,
        "event_seq": next_event_seq(agent, thread_id),
        "event_type": EVENT_UPDATED,
        "origin_type": state.get("origin_type"),
        "status": state.get("status"),
        "title": state.get("title"),
        "narrative_content": (
            narrative_content if narrative_content is not None
            else state.get("narrative_content")
        ),
        "created_at": state.get("created_at"),
        "updated_at": updated_at or now_utc_iso(),
        "check_after_ts": (
            check_after_ts if check_after_ts is not None
            else state.get("check_after_ts")
        ),
        "share_target": (
            share_target if share_target is not None
            else state.get("share_target")
        ),
        "dissolved_at": None,
        "sage_fact_id": None,
    }
    return _append_entry(agent, entry)


def append_transition(
    agent_id: str,
    thread_id: str,
    to_status: str,
    updated_at: Optional[str] = None,
) -> bool:
    """狀態機轉移（寫入 `transitioned` 事件，`status` = 轉移後的新狀態）。

    §2.5 非法轉移的拒絕行為＝**不寫入該事件列** ＋ 一行 WARNING（逐字格式
    `WARNING_ILLEGAL_TRANSITION`）＋ **回 `False`**；**不得 raise、不得中斷呼叫端**。

    轉入終態（`completed` / `abandoned`）時 `check_after_ts` 一併歸 `null`
    （§2.2：`null` = 無排定檢視點，僅允許終態）。
    """
    agent = _validate_agent_id(agent_id)
    thread_id = _validate_thread_id(thread_id)

    state = get_state(agent, thread_id)
    if state is None:
        logger.warning(
            WARNING_ILLEGAL_TRANSITION.format(
                thread_id=thread_id, from_status="(unknown)", to_status=to_status
            )
        )
        return False

    from_status = str(state.get("status"))
    if not allowed_transition(from_status, to_status):
        logger.warning(
            WARNING_ILLEGAL_TRANSITION.format(
                thread_id=thread_id, from_status=from_status, to_status=to_status
            )
        )
        return False

    entry: Dict[str, Any] = {
        "thread_id": thread_id,
        "event_seq": next_event_seq(agent, thread_id),
        "event_type": EVENT_TRANSITIONED,
        "origin_type": state.get("origin_type"),
        "status": to_status,
        "title": state.get("title"),
        "narrative_content": state.get("narrative_content"),
        "created_at": state.get("created_at"),
        "updated_at": updated_at or now_utc_iso(),
        "check_after_ts": None if is_terminal(to_status) else state.get("check_after_ts"),
        "share_target": state.get("share_target"),
        "dissolved_at": None,
        "sage_fact_id": None,
    }
    if not _append_entry(agent, entry):
        return False
    logger.info(
        LOG_TRANSITION.format(
            agent_id=agent,
            thread_short=thread_id[:8],
            from_status=from_status,
            to_status=to_status,
        )
    )
    return True


def append_dissolved(
    agent_id: str,
    thread_id: str,
    dissolved_at: Optional[str] = None,
    sage_fact_id: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> bool:
    """蔡戈尼溶解的**寫入路徑**（§2.4 `dissolved` 事件；`status` = 終態）。

    ⚠️ **M2 才是觸發者**（§10.2）：本票只提供寫入與讀取語意，**不做任何 LLM 呼叫、
    不寫 SAGE**。前置：fold 後 `status ∈ {completed, abandoned}`；非終態回 `False`。

    `dissolved_at` / `sage_fact_id` 回填後由 fold 規則 3（最後一個非 null 值勝出）
    持久可見，不受後續 `updated` 事件洗掉。
    """
    agent = _validate_agent_id(agent_id)
    thread_id = _validate_thread_id(thread_id)
    dissolved_at = _iso_or_none(dissolved_at) or now_utc_iso()
    if sage_fact_id is not None and not isinstance(sage_fact_id, str):
        raise ValueError("sage_fact_id must be a str or None")

    state = get_state(agent, thread_id)
    if state is None:
        logger.warning(f"[LifeThread] dissolved ignored (unknown thread): {thread_id}")
        return False
    status = str(state.get("status"))
    if not is_terminal(status):
        logger.warning(
            f"[LifeThread] dissolved ignored (non-terminal thread): {thread_id} status={status}"
        )
        return False

    entry: Dict[str, Any] = {
        "thread_id": thread_id,
        "event_seq": next_event_seq(agent, thread_id),
        "event_type": EVENT_DISSOLVED,
        "origin_type": state.get("origin_type"),
        "status": status,
        "title": state.get("title"),
        "narrative_content": state.get("narrative_content"),
        "created_at": state.get("created_at"),
        "updated_at": updated_at or now_utc_iso(),
        "check_after_ts": None,
        "share_target": state.get("share_target"),
        "dissolved_at": dissolved_at,
        "sage_fact_id": sage_fact_id,
    }
    return _append_entry(agent, entry)


def list_active(agent_id: str) -> List[Dict[str, Any]]:
    """fold 後所有 `status == "active"` 的線頭（唯讀觀測面）。"""
    return [s for s in fold(agent_id).values() if s.get("status") == "active"]


def list_threads(
    agent_id: str, statuses: Optional[Iterable[str]] = None
) -> List[Dict[str, Any]]:
    """fold 後線頭清單；`statuses` 給定時只回該些狀態（唯讀觀測面）。"""
    states = list(fold(agent_id).values())
    if statuses is None:
        return states
    wanted = set(statuses)
    return [s for s in states if s.get("status") in wanted]
