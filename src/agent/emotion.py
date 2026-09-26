"""
emotion.py
Soul OS — Phase 3: 情緒引擎（SQLite 持久化）

設計：
  - 共用 data/memory.db，加一張 agent_emotions 表
  - 各 agent 有自己的 mood_decay / response_boost 敏感度
  - mood clamp(-1.0, 1.0)，intimacy clamp(0.0, 100.0)
  - 提供 mood_description() 給 LLMProxy 注入到 system prompt

資料表：
  agent_emotions (
    agent_id   TEXT PRIMARY KEY,
    mood       REAL DEFAULT 0.0,
    intimacy   REAL DEFAULT 50.0,
    updated_at TEXT  (ISO timestamp)
  )
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("soul_os.emotion")

# P0.5 (Bry 派工 2026-08-09 19:48): use data_root() so test subprocess can
# redirect via SOUL_OS_DATA_DIR.
from src.paths import data_root

DB_PATH = data_root() / "memory.db"

# 各 agent 對情緒變化的敏感度
# - response_boost：每次 user_message 給的 mood 增量（正向）
# - mood_decay：每次自然 heartbeat tick 給的 mood 衰減（負向）
SENSITIVITY: dict[str, dict[str, float]] = {
    "agent_yua":   {"mood_decay": 0.015, "response_boost": 0.08},
    "agent_ruka":  {"mood_decay": 0.025, "response_boost": 0.12},  # 更敏感
    "agent_akane": {"mood_decay": 0.010, "response_boost": 0.06},
    # Phase 6.5 — Rem (Re:Zero · 昴重力場型)
    # 性格：能幹、穩定、壓抑情緒（罪惡感驅動）；不是會突然興奮型
    # mood_decay 設比 Yua 略低（更不容易波動）；response_boost 中等（會回應但不誇張）
    "agent_rem":   {"mood_decay": 0.012, "response_boost": 0.07},
    # Phase 7 — Ram (Re:Zero · 鬼族驕傲型)：沉默驅動,mood decay 最低（情緒最不外顯）
    "agent_ram":   {"mood_decay": 0.008, "response_boost": 0.05},
    # Phase 8 — Mahiru (Re:Zero · 生活感核心)：情緒穩定但會透過生活管理外顯
    "agent_mahiru": {"mood_decay": 0.010, "response_boost": 0.08},
    # Phase 9 — Anna (Bokuyaba · 食慾靠近者)：情緒會因為「被忽略 / 擔心添麻煩」波動
    # mood_decay 中等（不會長期低落,但會在「被禮貌對待卻沒被留下」時 mood 下降）
    # response_boost 中等偏高（日常親密型,被 Bryan 提食物或邀請會反應較快）
    "agent_anna":  {"mood_decay": 0.018, "response_boost": 0.10},
}


# ══════════════════════════════════════════════════════════════
# INTIMACY-GROWTH-2：24 小時靜默衰減 — 常數與旗標
# ══════════════════════════════════════════════════════════════

#: 衰減旗標環境變數名（**呼叫時即時讀取，不得快取**）。
#: 🔴 C4：**缺席即 OFF**。不是「預設 ON、值為 '0' 才 OFF」。
DECAY_ENABLED_ENV = "INTIMACY_DECAY_ENABLED"

#: 真值集合（與 LIFE_THREAD_* 三支旗標逐字同語意）。
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

#: 固定步長：滿 24 小時扣 0.5 點（§4）。
DECAY_STEP = 0.5

#: 衰減窗口（小時）。24h。
DECAY_WINDOW_HOURS = 24.0

#: 連線層級 busy_timeout（毫秒）。C2：顯式設定，不倚賴 Python 預設 5.0s。
BUSY_TIMEOUT_MS = 5000

#: 🔴 INTIMACY-GROWTH-2 closeout（**每角色啟用資格**）：只有列在本集合的 agent
#: 才會被 `touch_inbound()` / `try_apply_decay()` 實際扣減。
#:
#: **為何需要它**（Owner 裁定）：全域旗標 ON 只代表「衰減機制已啟用」，**不代表
#: 每一個角色都已經有可靠的 TOUCH 來源**。§2 的語意是「只有**通過驗證的真人
#: inbound** 才 TOUCH」；若某角色的語音互動（VC）尚未可靠地轉成 inbound TOUCH，
#: 而 TG 恰好對她發訊，那麼 TOUCH 路徑內的到期清算就會**扣她的 Delta**，
#: 形成「語音互動沒被計入、卻照樣被扣分」的不對稱。
#:
#: **為何用白名單**（frozenset）而非黑名單：與 `WORLD_LOG_BACKED_SOURCES` 同型。
#: 黑名單會讓**未來新增的角色靜默取得扣減資格**（新角色一加入就自動可被扣分，
#: 沒有人在 review 時會看到）；白名單則相反 —— 新角色預設**不合格**，
#: 要取得資格必須顯式改本常數，是一個看得見的決策點。
#:
#: 🔴 **覆蓋範圍的判準（closeout 修正 A）**：本集合 ＝ 所有「**非 VC** 且
#: **TG owner-whitelist 入口可達**」的角色。推導如下（機械確認，非推測）：
#:
#:   1. `.env` 有 10 個 TG bot token：yua / ruka / akane / rem / ram /
#:      mahiru / anna / mai / miku / aoi。
#:   2. `router.py` 的 owner whitelist（`TELEGRAM_OWNER_ID`）驗的是**發訊者
#:      身分**，不是「哪個 bot」；它對所有 10 個 bot 的 inbound 一視同仁。
#:   3. `router.py` `inbound()` 內 `_touch_intimacy_clock(full_agent_id, ...)`
#:      的 `full_agent_id` 是**收到訊息的那個 bot 自己**。
#:   ⇒ 10 個角色**全部**都可經 TG owner-whitelist 入口抵達 TOUCH 點。
#:
#:   本機 VC 服務恰好 3 個：`VC1_AkaneVoiceCompanion` / `VC1_MaiVoiceCompanion`
#:   / `VC1_RemVoiceCompanion` ⇒ `agent_akane` / `agent_mai` / `agent_rem`。
#:   ⇒ **10 − 3 = 7**。
#:
#: 🔴 **`agent_akane` / `agent_rem` / `agent_mai` 不得加入本集合**：
#: 三者的 VC 語音尚未可靠 TOUCH，加入等於讓她們在語音互動未被計入的前提下被扣分。
#: 此禁令由 `tests/test_intimacy_growth_2.py::TestPerAgentEligibilityConstant`
#: 的集合等值斷言與 `isdisjoint` 斷言鎖住 —— 未來若有人把她們加回來會直接變紅。
INTIMACY_DECAY_ELIGIBLE_AGENTS: frozenset = frozenset({
    "agent_yua",
    "agent_ruka",
    "agent_ram",
    "agent_mahiru",
    "agent_anna",
    "agent_miku",
    "agent_aoi",
})

#: 不合格角色的統一 reason（fail-safe：**不拋例外**，讓呼叫端零成本辨識）。
NOT_ELIGIBLE_REASON = "NOT_ELIGIBLE"


def is_decay_eligible(agent_id: object) -> bool:
    """該 agent 是否具備**每角色**的衰減扣減資格（白名單查詢）。

    非字串 / 空字串 / 未列名者一律 `False`（fail-safe 方向 ＝ 不扣減）。
    本函式**不讀 env**：資格是程式碼層級的決策，不是執行期可切換的旗標。
    """
    if not isinstance(agent_id, str):
        return False
    return agent_id in INTIMACY_DECAY_ELIGIBLE_AGENTS


#: 🔴 寫入互斥鎖（C1 的配套）。
#: `emotion_engine.conn` 是 `check_same_thread=False` 的**跨執行緒共享**連線
#: （模組層級 singleton，全 process 共用）。Python sqlite3 的 `in_transaction`
#: 是連線層級狀態，沒有這把鎖時「檢查 → BEGIN → commit」會與其他執行緒交錯，
#: 實測會拋 SystemError / "cannot commit - no transaction is active" /
#: "cannot start a transaction within a transaction"。
#: 用 RLock（可重入）讓同一條連線上的顯式交易彼此互斥；
#: 這**不是**第二條連線，仍是單寫者模型。
_WRITE_LOCK = threading.RLock()


def decay_enabled() -> bool:
    """衰減旗標是否為 ON（**每次呼叫都重新讀 `os.environ`**，讓測試能 monkeypatch）。

    真值語意（C4）：
      - 去首尾空白、不分大小寫，落在真值集合 `{"1","true","yes","on"}` 才為 `True`
      - **缺席**（`None`）／非字串／其餘值（含 `""` / `"0"` / `"off"` / `"false"`）
        ⇒ `False`（fail-safe 方向 ＝ 零 DDL、零寫入、逐位元維持原行為）
    """
    raw = os.environ.get(DECAY_ENABLED_ENV)
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() in TRUTHY_VALUES


def decay_evaluate_eligible_agents(now: Optional[float] = None) -> dict:
    """🔴 缺口 3 的接線點：對**所有合格角色**各評估**至多一次**衰減。

    **為何需要它**：`try_apply_decay()` 本身正確，但交付時**沒有任何執行接線**
    （`scripts/run_server.py` / `src/soul/scheduler.py` /
    `src/agent/consciousness.py` 全數 0 命中）。唯一會走到扣減的路徑是
    `touch_inbound()`（TG 被動）與 dormant VC 端點 ⇒ **安靜滿 24h 當下不會扣**，
    「24 小時靜默衰減」在實務上永遠不會自然發生（只有真人再說話時才補扣一次）。

    **本函式不發明排程**：它只是一個**有界的評估單元**，由呼叫端在**既有**的
    週期點上逐輪呼叫（見 `scripts/run_server.py` 的 SAGE flush 15s 週期任務）。
    不新建定時器、不新開 asyncio task、不新增 background loop。

    契約：
      - **旗標 OFF ⇒ no-op**（第一行就檢查）：回 `{"evaluated": 0,
        "reason": "FLAG_OFF"}`，**零 DDL、零寫入**（C4）。
      - **有界**：一輪只對合格角色各評估**至多一次**；`try_apply_decay()`
        內部已保證「至多一步」（bounded 1-step catch-up）。
      - **不得讓呼叫端的週期任務因本函式而死**：單一角色的例外被吞成 WARNING，
        繼續評估其餘角色。呼叫端**仍應**自行包 try/except（repo 既有慣例），
        本處的逐角色隔離只是第二層。

    Returns dict：`evaluated`（實際評估的角色數）/ `applied`（成功扣減的角色數）/
    `reason` / `results`（逐角色結果，便於測試與觀測）。
    """
    # 🔴 C4 契約：旗標 OFF ⇒ **第一行**就返回，零工作、零持久寫入。
    if not decay_enabled():
        return {"evaluated": 0, "applied": 0, "reason": "FLAG_OFF", "results": {}}

    now_ts = float(now) if now is not None else time.time()
    results: dict = {}
    applied_count = 0

    # `INTIMACY_DECAY_ELIGIBLE_AGENTS` 是 `frozenset` ⇒ 排序只為讓評估順序
    # 可重現（測試與 log 可對照），不影響語意。
    for agent_id in sorted(INTIMACY_DECAY_ELIGIBLE_AGENTS):
        try:
            result = emotion_engine.try_apply_decay(agent_id, now=now_ts)
        except Exception as e:  # noqa: BLE001 — 單一角色失敗不得中斷整輪
            logger.warning(
                "[INTIMACY-DECAY] periodic evaluation failed agent=%s: %s",
                agent_id, e,
            )
            results[agent_id] = {"applied": False, "reason": "ERROR"}
            continue
        results[agent_id] = result
        if result.get("applied"):
            applied_count += 1

    return {
        "evaluated": len(results),
        "applied": applied_count,
        "reason": "EVALUATED",
        "results": results,
    }


class EmotionEngine:
    """情緒引擎：管理各 agent 的 mood / intimacy（SQLite 持久化）"""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # INTIMACY-GROWTH-2 (C2)：**顯式**設 busy_timeout。
        # 這條連線是模組層級 singleton 的單一長生命週期連線（全 process 共用），
        # 上面沒有 timeout= 引數 ⇒ Python 預設 5.0s。本工單要求在連線上顯式
        # 發出 PRAGMA，讓「等待鎖」的預算是被寫下來的契約、而非直譯器預設值。
        # 只在**寫**連線上做；不開第二條連線（C1：SQLite 單寫者模型）。
        self.conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_emotions (
                agent_id   TEXT PRIMARY KEY,
                mood       REAL DEFAULT 0.0,
                intimacy   REAL DEFAULT 50.0,
                updated_at TEXT
            )
        """)
        # INTIMACY-GROWTH-1：惰性冪等遷移。
        # 舊庫只有 4 欄；新增 intimacy_delta 承載「動態親密度增量」。
        # 以 PRAGMA 檢查確保冪等（第二次呼叫不得重複 ALTER）。
        # 絕不刪除 / 不 UPDATE 既有 intimacy 欄位 —— 舊值逐位元保留。
        try:
            cols = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(agent_emotions)")
            }
            if "intimacy_delta" not in cols:
                self.conn.execute(
                    "ALTER TABLE agent_emotions ADD COLUMN intimacy_delta REAL DEFAULT 0.0"
                )
        except sqlite3.OperationalError as e:
            # 併發建立情境：另一連線搶先 ALTER 完成 -> 已是目標狀態，吞掉即可
            logger.warning(f"[Emotion] intimacy_delta migration skipped: {e}")
        self.conn.commit()

    # ── INTIMACY-GROWTH-2：惰性冪等 DDL（C3）─────────────────────
    # 🔴 硬性約束：**模組 import 時不得執行任何 DDL**。
    #   本函式只在 `touch_inbound()` / `try_apply_decay()` **確實要動 DB 時**
    #   才被呼叫（且 `ensure_decay_schema()` 是那兩條路徑唯一的 DDL 入口），
    #   並且**絕不**在 `__init__` / `_init_schema()` 裡被呼叫。
    #   測試 test_g2_no_ddl_at_import / test_g2_flag_off_zero_ddl 會把這條釘死。
    #
    # 兩欄 + 一張 ledger 表（冪等 event key 是「每次評估最多扣一次」的持久依據）：
    #   last_valid_inbound_at REAL(epoch seconds) NULL = UNKNOWN（§9 不衰減）
    #   next_decay_due_at     REAL(epoch seconds) NULL = UNKNOWN（§9 不衰減）
    #   intimacy_decay_ledger (event_key PK, agent_id, applied_at,
    #                          original_due_at, applied_amount)  ← 記**實扣量**
    #   以 PRAGMA table_info 檢查後才 ALTER ⇒ 重複執行不得報 duplicate column。

    def ensure_decay_schema(self) -> None:
        """惰性建立 INTIMACY-GROWTH-2 的兩欄 + ledger 表（冪等）。

        **唯一** 允許建立本票 schema 的入口。呼叫端必須先確認旗標 ON ——
        旗標 OFF 時本函式**不得**被呼叫（C4：零 DDL）。
        """
        try:
            with _WRITE_LOCK:
                cols = {
                    row[1]
                    for row in self.conn.execute("PRAGMA table_info(agent_emotions)")
                }
                if "last_valid_inbound_at" not in cols:
                    self.conn.execute(
                        "ALTER TABLE agent_emotions "
                        "ADD COLUMN last_valid_inbound_at REAL"
                    )
                if "next_decay_due_at" not in cols:
                    self.conn.execute(
                        "ALTER TABLE agent_emotions ADD COLUMN next_decay_due_at REAL"
                    )
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS intimacy_decay_ledger (
                        event_key        TEXT PRIMARY KEY,
                        agent_id         TEXT NOT NULL,
                        applied_at       REAL NOT NULL,
                        original_due_at  REAL,
                        applied_amount   REAL NOT NULL
                    )
                """)
                if self.conn.in_transaction:
                    self.conn.commit()
        except sqlite3.OperationalError as e:
            # 併發建立情境：另一路徑搶先 ALTER/CREATE 完成 -> 已是目標狀態，吞掉即可
            logger.warning(f"[Emotion] decay schema migration skipped: {e}")

    def get(self, agent_id: str) -> Tuple[float, float]:
        """讀出 (mood, intimacy)；沒有就回預設值 (0.0, 50.0)"""
        cur = self.conn.execute(
            "SELECT mood, intimacy FROM agent_emotions WHERE agent_id = ?",
            (agent_id,),
        )
        row = cur.fetchone()
        if row is None:
            return (0.0, 50.0)
        return (float(row[0]), float(row[1]))

    def update(
        self,
        agent_id: str,
        mood_delta: float = 0.0,
        intimacy_delta: float = 0.0,
    ) -> Tuple[float, float]:
        """clamp 後寫回，回傳更新後的 (mood, intimacy)"""
        cur_mood, cur_intimacy = self.get(agent_id)
        new_mood = max(-1.0, min(1.0, cur_mood + mood_delta))
        new_intimacy = max(0.0, min(100.0, cur_intimacy + intimacy_delta))
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT INTO agent_emotions (agent_id, mood, intimacy, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                mood = excluded.mood,
                intimacy = excluded.intimacy,
                updated_at = excluded.updated_at
        """, (agent_id, new_mood, new_intimacy, now))
        self.conn.commit()
        return (new_mood, new_intimacy)

    def reset(self, agent_id: str) -> None:
        """清回初始值（debug 用）"""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT INTO agent_emotions (agent_id, mood, intimacy, updated_at)
            VALUES (?, 0.0, 50.0, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                mood = 0.0,
                intimacy = 50.0,
                updated_at = excluded.updated_at
        """, (agent_id, now))
        self.conn.commit()
        logger.info(f"[Emotion] {agent_id} reset to defaults")

    # ── INTIMACY-GROWTH-1：動態親密度增量 API ──────────────────
    # 注意：update_delta 只動 intimacy_delta 欄，永不寫入舊 intimacy 欄位。
    # 舊 intimacy 欄位（config intimacy_level 的落腳處）維持唯讀。

    def get_delta(self, agent_id: str) -> float:
        """讀出動態親密度增量；無 row 或 NULL -> 0.0（fail-safe，不拋）。"""
        try:
            cur = self.conn.execute(
                "SELECT intimacy_delta FROM agent_emotions WHERE agent_id = ?",
                (agent_id,),
            )
            row = cur.fetchone()
        except sqlite3.OperationalError:
            # 極端情境（舊庫遷移競態）-> 視為尚未累積，不中斷呼叫端
            return 0.0
        if row is None or row[0] is None:
            return 0.0
        try:
            return float(row[0])
        except (TypeError, ValueError):
            return 0.0

    def update_delta(self, agent_id: str, delta_gain: float) -> float:
        """累加 intimacy_delta（clamp -100.0 ~ 100.0），回傳累加後的值。

        UPSERT 沿用 update() 的模式；intimacy / mood 欄位不在 UPSERT 目標內，
        因此對既有 row 而言兩者維持原值（唯讀語義）。

        🔴 INTIMACY-GROWTH-2 closeout（C1）：整段 read-modify-write 必須與
        `_write_tx()` 的顯式交易共用**同一把** `_WRITE_LOCK`。本連線是
        `check_same_thread=False` 的**跨執行緒共享**長生命週期連線，而
        `update_delta()` 是 1B 交付的既有成長路徑，未取鎖時會與
        `touch_inbound()` / `try_apply_decay()` 的 `BEGIN IMMEDIATE` 交錯：

          1B: get_delta() 讀 intimacy_delta  →（無鎖，另一執行緒插入顯式交易）
          1B: conn.execute(INSERT..UPSERT)   ← 此時 in_transaction 已被推成 True
          1B: conn.commit()                  ← 提交了**別人**的交易，
                                                隨後對方 commit ⇒
                                                "cannot commit - no transaction is active"
          或 `SystemError: error return without exception set`（commit 回 NULL 無例外）

        實測重現於 closeout 競態探針（4 執行緒 × 30 輪 ⇒ 5 次上述例外）。
        取鎖後 `update_delta()` 仍是**同一個 UPSERT、同一個算式**：本函式
        的成長語意（clamp 邊界、回傳值、只動 intimacy_delta 欄）逐位元不變，
        改的只是「寫入邊界納入同一互斥」。RLock 可重入，故不會與呼叫端
        既有的鎖持有互相死鎖。
        """
        with _WRITE_LOCK:
            current = self.get_delta(agent_id)
            new_delta = max(-100.0, min(100.0, current + float(delta_gain)))
            now = datetime.now(timezone.utc).isoformat()
            self.conn.execute("""
                INSERT INTO agent_emotions (agent_id, intimacy_delta, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    intimacy_delta = excluded.intimacy_delta,
                    updated_at = excluded.updated_at
            """, (agent_id, new_delta, now))
            self.conn.commit()
            return new_delta

    @staticmethod
    def mood_description(mood: float) -> str:
        """把 mood 數值翻成 LLM 看得到的中文描述"""
        if mood > 0.5:
            return "你現在心情很好，語氣輕鬆自然"
        if mood >= 0.0:
            return ""  # 0 ~ 0.5 不注入
        if mood >= -0.5:
            return "你有點悶，話比平時少一點"
        return "你心情很差，話變得更短、更冷"

    # ── INTIMACY-GROWTH-2：24 小時靜默衰減 ────────────────────────
    # 語意契約逐條落地（見模組尾註）。三個不變量先寫在前面：
    #   §1 每角色獨立計時 —— 時鐘存在 agent_emotions 的**該 agent 自己的 row**，
    #      沒有任何跨 agent 的共享時鐘；不存在的 row ⇒ 時鐘為 NULL（UNKNOWN）。
    #   §6 只動 intimacy_delta 欄；mood / 舊 intimacy / 既有 Base 一律不碰。
    #   §7 時鐘 / 下一次到期 / Delta 扣減 / ledger 事件紀錄 **同一交易**提交。

    def touch_inbound(
        self,
        agent_id: str,
        event_id: str,
        channel: str,
        now: Optional[float] = None,
    ) -> dict:
        """已驗證的真人 inbound 進來的 TOUCH 點。

        流程（§8：**先結算最多一次，再推進 TOUCH**）：
          1. 若時鐘已逾期 ⇒ 先 `_settle_once()` 結算**最多一步**
             （並把 TOUCH 目標推到 due + 24h ⇒ 時鐘**永遠不倒退**）
          2. 否則 TOUCH 目標 = max(last_valid_inbound_at, now)
          3. next_decay_due_at = 目標 + 24h

        逾期或重複 inbound 只會讓 due 由「now + 24h」往前推，永不倒退。

        旗標 OFF ⇒ 原行為、零新持久寫入（C4）。

        🔴 **每角色啟用資格**（closeout 修正 B）：`agent_id` 不在
        `INTIMACY_DECAY_ELIGIBLE_AGENTS` ⇒ **完全不 TOUCH** —— 不寫時鐘、
        不寫 ledger、不扣減，直接回 `NOT_ELIGIBLE`。此檢查必須在**扣減邊界**
        （本函式內、任何 DB 寫入之前）而非只在呼叫端，因為本函式是 §10 的
        唯一寫入路徑，呼叫端（TG / VC）無法保證已做過資格判斷。
        **不得拋例外**：TG 路徑對本函式包了 try/except，拋例外會被吞成靜默失敗。
        """
        if not decay_enabled():
            return {"applied": False, "touched": False, "reason": "FLAG_OFF"}

        # 🔴 扣減邊界閘門（必須在 ensure_decay_schema / 任何寫入之前）：
        #    不合格 ⇒ 連 DDL 都不做，逐位元維持「該角色未被本票觸及」。
        if not is_decay_eligible(agent_id):
            logger.info(
                "[INTIMACY-DECAY] touch skipped agent=%s channel=%s reason=%s",
                agent_id, channel, NOT_ELIGIBLE_REASON,
            )
            return {
                "applied": False,
                "touched": False,
                "reason": NOT_ELIGIBLE_REASON,
            }

        self.ensure_decay_schema()  # 惰性 DDL（C3）
        now_ts = float(now) if now is not None else time.time()
        window = DECAY_WINDOW_HOURS * 3600.0

        with self._write_tx():
            last_in, due, delta = self._read_clock_and_delta_locked(agent_id)
            # 兩個**各自獨立**的值 —— 這是缺口 1 的修正核心：
            #   `last_written` = 寫回 last_valid_inbound_at 的值
            #                    ＝ 真人**實際到場時間**（單調不倒退）
            #   `due_written`  = 寫回 next_decay_due_at 的值
            #                    ＝ 該到場時間所開的新窗口結束點
            # 舊實作把兩者綁在同一個 `target` 上（`target = max(now, due+window)`
            # 再 `due = target + window`），正是「真人準時到場卻白拿一天」的來源。
            last_written = now_ts
            due_written = now_ts + window

            if due is not None and now_ts >= due:
                # §8：逾期 inbound ⇒ 先結算最多一次（扣 0.5、ledger 記一筆）。
                self._settle_once_locked(
                    agent_id, now_ts, due, delta, origin="inbound_late"
                )
                # 🔴 缺口 1 修正：結算後 `last_valid_inbound_at` ＝ **真人實際
                #    到場時間**，取「本次 now_ts」與「結算前的 last_valid_inbound_at」
                #    的較大者 —— 這樣亂序抵達的舊 timestamp 不會讓時鐘倒退
                #    （既有「時鐘單調不倒退」契約），而準時到場者也不會被
                #    偽造成未來時刻。
                #
                #    反面教材（舊實作）：`target = max(now_ts, due + window)`
                #    後又拿 `target` 當 last_valid_inbound_at。真人恰在首次到期點
                #    `T+24h` 到場時 `now_ts == due`，該式給出 `target = T+48h`
                #    ⇒ last_valid_inbound_at 被寫成 T+48h、due 成 T+72h。真人
                #    明明準時出現，卻白拿一天（多一整個 24h 窗口不衰減）。
                #
                #    **不得**用結算後的 due 當 last_valid_inbound_at。
                last_written = now_ts
                if last_in is not None:
                    last_written = max(float(last_in), last_written)
                # 下一次 due ＝ 該次到期所屬窗口的結束（`due + window`），
                # **不是** now + window：逾期越久也只補一步（bounded 1-step，§5），
                # 後續窗口由週期評估（`try_apply_decay`）逐窗推進。
                # 單調性：due 只會被推進（new_due = now + window >= due + window）。
                due_written = due + window
                # 結算後的窗口若仍落在「真人到場時間」之前，該窗口即已作廢 ⇒
                # 直接把窗口推進到真人到場後的下一個結束點，避免寫出
                # due < last_written 的退化時鐘。
                if last_written >= due_written:
                    due_written = last_written + window
            elif last_in is not None:
                # 非逾期分支行為**不變**：維持既有 `max(last_in, now)` 語意。
                last_written = max(float(last_in), now_ts)
                due_written = last_written + window

            self._upsert_clock_locked(agent_id, last_written, due_written)

        logger.info(
            "[INTIMACY-DECAY] touch agent=%s channel=%s event=%s due_at=%.0f",
            agent_id, channel, event_id, due_written,
        )
        return {
            "applied": False,
            "touched": True,
            "reason": "TOUCHED",
            "last_valid_inbound_at": last_written,
            "next_decay_due_at": due_written,
        }

    def try_apply_decay(
        self, agent_id: str, now: Optional[float] = None
    ) -> dict:
        """評估一次衰減（heartbeat / 排程 tick 呼叫）。

        §4 首次滿 24h 扣 0.5；**每次評估最多扣一次**；成功後 due = now + 24h。
        §5 停服後恢復 **最多補扣一次**（bounded 1-step catch-up），不累積多步。
        §6 Delta 保底 0.0，不得為負。
        §9 時鐘 NULL（UNKNOWN 冷啟動）⇒ 不衰減、不清零既有 Delta。

        🔴 **每角色啟用資格**（closeout 修正 B）：`agent_id` 不在
        `INTIMACY_DECAY_ELIGIBLE_AGENTS` ⇒ **不扣減、不寫 ledger、不推進 due**，
        回 `NOT_ELIGIBLE`。此閘門與 `touch_inbound()` 內的是**同一道政策**，
        但必須**各自實作** —— 兩者是獨立的扣減邊界，只擋其中一條會留下另一條
        作為繞道（例如 heartbeat tick 直接呼叫本函式）。
        檢查置於 `ensure_decay_schema()` 之前 ⇒ 不合格角色連 DDL 都不觸發。

        Returns dict：`applied` / `reason` / `applied_amount` / `due_at`。
        """
        if not decay_enabled():
            return {"applied": False, "reason": "FLAG_OFF"}

        # 🔴 扣減邊界閘門（獨立於 touch_inbound 的那一道）。
        if not is_decay_eligible(agent_id):
            logger.info(
                "[INTIMACY-DECAY] tick skipped agent=%s reason=%s",
                agent_id, NOT_ELIGIBLE_REASON,
            )
            return {"applied": False, "reason": NOT_ELIGIBLE_REASON, "due_at": None}

        self.ensure_decay_schema()  # 惰性 DDL（C3）
        now_ts = float(now) if now is not None else time.time()

        with self._write_tx():
            _last_in, due, delta = self._read_clock_and_delta_locked(agent_id)
            if due is None:
                # §9：無有效時鐘 ⇒ UNKNOWN ⇒ 不衰減、不清零既有 Delta。
                return {"applied": False, "reason": "UNKNOWN", "due_at": None}
            if now_ts < due:
                # §4：未到期（含 23:59）⇒ 不扣。
                return {"applied": False, "reason": "NOT_DUE", "due_at": due}

            event_key = self._decay_event_key(agent_id, due)
            if self._ledger_has_locked(event_key):
                # 同一到期點已結算過（跨行程冪等）⇒ 只推進時鐘，不再扣。
                self._upsert_clock_only_locked(agent_id, now_ts + DECAY_WINDOW_HOURS * 3600.0)
                return {
                    "applied": False,
                    "reason": "DUPLICATE",
                    "due_at": now_ts + DECAY_WINDOW_HOURS * 3600.0,
                }

            applied = self._settle_once_locked(
                agent_id, now_ts, due, delta, origin="tick", event_key=event_key
            )
            return {
                "applied": True,
                "reason": "DECAYED",
                "applied_amount": applied,
                "due_at": now_ts + DECAY_WINDOW_HOURS * 3600.0,
            }

    # ── 內部：交易輔助（C1）─────────────────────────────────────
    # 🔴 全部走 `self.conn`（模組層級 singleton 的**同一條**長生命週期連線）。
    #    **禁止**對同一個 db 檔另開第二條 sqlite3.connect（單寫者模型）。
    #    發 BEGIN IMMEDIATE 前必須確認 `in_transaction` 為 False —— Python 的
    #    隱式交易管理下，在交易中再發 BEGIN 會撞
    #    「cannot start a transaction within a transaction」。

    @contextmanager
    def _write_tx(self):
        """`self.conn` 上的顯式交易邊界（C1）。

        🔴 兩條硬規則：
          1. **禁止**另開第二條連線：整個結算都在模組層級 singleton 的
             `self.conn` 上完成（SQLite 單寫者模型）。
          2. `BEGIN IMMEDIATE` 之前必須確認 `in_transaction` 為 False ——
             Python sqlite3 預設 isolation_level 會隱式管理交易，在隱式交易
             中再發 BEGIN 會撞 `cannot start a transaction within a transaction`。

        本連線是 `check_same_thread=False` 的**跨執行緒共享**連線（模組層級
        singleton，全 process 共用），所以「檢查 in_transaction → BEGIN →
        ... → commit」這串必須對其他寫者互斥；否則 `in_transaction` 會在檢查
        與 BEGIN 之間被另一個執行緒推成 True（實測：SystemError
        "error return without exception set" / DatabaseError
        "cannot commit - no transaction is active"）。用**同一把** RLock 串行化，
        並重用既有隱式交易而非疊一層 BEGIN。
        """
        with _WRITE_LOCK:
            conn = self.conn
            opened = False
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
                opened = True
            try:
                yield conn
            except BaseException:
                try:
                    if conn.in_transaction:
                        conn.rollback()
                except sqlite3.Error:
                    pass
                raise
            else:
                if conn.in_transaction:
                    conn.commit()

    def _read_clock_and_delta_locked(
        self, agent_id: str
    ) -> Tuple[Optional[float], Optional[float], float]:
        """讀 (last_valid_inbound_at, next_decay_due_at, intimacy_delta)。

        無 row ⇒ (None, None, 0.0)：時鐘 NULL ＝ UNKNOWN（§9）。
        """
        cur = self.conn.execute(
            "SELECT last_valid_inbound_at, next_decay_due_at, intimacy_delta "
            "FROM agent_emotions WHERE agent_id = ?",
            (agent_id,),
        )
        row = cur.fetchone()
        if row is None:
            return (None, None, 0.0)
        raw_last, raw_due, raw_delta = row

        def _as_float(value):
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        delta = _as_float(raw_delta)
        return (_as_float(raw_last), _as_float(raw_due), delta if delta is not None else 0.0)

    @staticmethod
    def _decay_event_key(agent_id: str, due: float) -> str:
        """唯一 event key（冪等鍵）：agent ＋ **原到期點**。

        以原到期點（而非 now）當鍵，是「同一到期點被評估多次只結算一次」的關鍵：
        重複 tick / 重啟後重評都會算出同一個 key。
        """
        return f"decay:{agent_id}:{int(round(float(due)))}"

    def _ledger_has_locked(self, event_key: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM intimacy_decay_ledger WHERE event_key = ?", (event_key,)
        )
        return cur.fetchone() is not None

    def _settle_once_locked(
        self,
        agent_id: str,
        now_ts: float,
        due: float,
        delta: float,
        *,
        origin: str,
        event_key: Optional[str] = None,
    ) -> float:
        """扣**一步** 0.5 並把 due 推到 now + 24h，全在同一交易內。

        §6：intimacy_delta 保底 0.0（實扣量 = min(0.5, max(delta, 0.0))）。
        §7：Delta 扣減 + ledger 事件 + 時鐘推進 同一交易提交。

        🔴 **缺口 2 修正：以 ledger INSERT 的實際寫入列數為閘門。**
        `INSERT ... ON CONFLICT(event_key) DO NOTHING` 的 `cursor.rowcount`：
          - `== 1` ⇒ 本次是**首次**寫入該到期點的 ledger（真實結算）⇒ 扣減 + 推進。
          - `== 0` ⇒ 該 event_key **已存在**（同一到期點被重放）⇒ 直接回
            `applied=0.0`，**不 UPDATE Delta、不推進 due**。

        為何要放在這裡（而非只靠呼叫端先查）：`try_apply_decay()` 有前置查
        `_ledger_has_locked()`，但 `touch_inbound()` 的逾期分支**直接呼叫本函式**
        並跳過該前置查。舊實作只有 INSERT 的 `DO NOTHING`，ledger 不增、卻仍
        繼續 UPDATE Delta ⇒ 憑空再扣一次。把閘門下移到「DB 層的真實寫入結果」
        後，兩條路徑共用同一道閘門，不依賴呼叫端記得先查。

        `try_apply_decay()` 的既有前置查**保留**（雙保險：讓 DUPLICATE 這條
        便宜路徑不必進到本函式）。
        """
        window = DECAY_WINDOW_HOURS * 3600.0
        applied = min(DECAY_STEP, max(0.0, float(delta)))  # §6 floor 0.0
        new_delta = max(0.0, float(delta) - DECAY_STEP)
        key = event_key or self._decay_event_key(agent_id, due)
        new_due = now_ts + window

        cur = self.conn.execute(
            "INSERT INTO intimacy_decay_ledger "
            "(event_key, agent_id, applied_at, original_due_at, applied_amount) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(event_key) DO NOTHING",
            (key, agent_id, now_ts, due, applied),
        )
        # 🔴 真實寫入結果（DB 層）才是閘門 —— 不是呼叫端的記憶。
        #    rowcount == 0 ⇒ 該到期點已結算過（ledger 不增）⇒ **零副作用**。
        try:
            inserted = cur.rowcount
        except (AttributeError, TypeError):  # pragma: no cover - 防禦性
            inserted = 1  # 無法判定時採「已插入」＝ 維持舊行為，不靜默漏扣
        if inserted == 0:
            logger.info(
                "[INTIMACY-DECAY] settle skipped (duplicate) agent=%s origin=%s "
                "original_due=%.0f key=%s",
                agent_id, origin, due, key,
            )
            return 0.0

        self.conn.execute(
            "UPDATE agent_emotions SET intimacy_delta = ?, next_decay_due_at = ? "
            "WHERE agent_id = ?",
            (new_delta, new_due, agent_id),
        )
        logger.info(
            "[INTIMACY-DECAY] settle agent=%s origin=%s applied=%.3f "
            "delta=%.3f->%.3f original_due=%.0f next_due=%.0f key=%s",
            agent_id, origin, applied, delta, new_delta, due, new_due, key,
        )
        return applied

    def _upsert_clock_locked(
        self, agent_id: str, last_inbound: float, due: float
    ) -> None:
        """UPSERT 時鐘；**不碰** mood / intimacy / intimacy_delta（§6）。"""
        self.conn.execute(
            """
            INSERT INTO agent_emotions
                (agent_id, last_valid_inbound_at, next_decay_due_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                last_valid_inbound_at = excluded.last_valid_inbound_at,
                next_decay_due_at     = excluded.next_decay_due_at,
                updated_at            = excluded.updated_at
            """,
            (agent_id, last_inbound, due, datetime.now(timezone.utc).isoformat()),
        )

    def _upsert_clock_only_locked(self, agent_id: str, due: float) -> None:
        self.conn.execute(
            "UPDATE agent_emotions SET next_decay_due_at = ? WHERE agent_id = ?",
            (due, agent_id),
        )


# ── INTIMACY-GROWTH-1：親密度成長模型 ──────────────────────────
# 每次 USER_MESSAGE 只累積「阻尼後的增量」到 intimacy_delta，
# 有效親密度 = config 基礎值 + 動態增量（兩者分離，基礎值唯讀）。

BASE_STEP = 0.5


def compute_effective_intimacy(base_intimacy: float, delta: float) -> float:
    """有效親密度 = 基礎值 + 動態增量，clamp 到 [0.0, 100.0]。"""
    return max(0.0, min(100.0, float(base_intimacy) + float(delta)))


def calculate_intimacy_gain(
    current_effective: float, base_step: float = BASE_STEP
) -> float:
    """阻尼增量：越接近 100 成長越慢（飽和曲線）。

    damping = 1 - (current_effective / 100)^2
    下限 0.01 —— 保證親密度永遠仍可微量成長（不得歸零）。
    """
    damping = 1.0 - ((float(current_effective) / 100.0) ** 2)
    return max(0.01, float(base_step) * damping)


def compute_longing(intimacy: float, silence_minutes: float) -> float:
    """
    M7-3 (Bry 拍板 2026-08-18): 想念 = 依戀(intimacy) × 沉默時長 (現算, 不持久化).

    決策 #3 落地: 想念不存成欄位, 觸發時用兩個既有事實現算:
      - attachment = intimacy / 100 (依戀強度, clamp 0-1)
      - silence_factor = silence_minutes / 1440 (24h 飽和, clamp 0-1)
      - longing = attachment × silence_factor

    Returns:
        float in [0.0, 1.0] — 0 = 完全不想念, 1 = 非常想念。
    """
    attachment = max(0.0, min(1.0, intimacy / 100.0))
    silence_factor = max(0.0, min(1.0, silence_minutes / 1440.0))
    return attachment * silence_factor


# 模組層級 singleton — 各處 `from src.agent.emotion import emotion_engine`
emotion_engine = EmotionEngine()
