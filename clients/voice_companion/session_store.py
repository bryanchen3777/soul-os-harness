"""VC-2.5 跨介面統一短期會話流（SessionStore）。

以 (agent_id, user_bryan) 為核心的共享短期對話層，打通 Telegram 與
Voice Companion：TG 回一句 → VC 立刻接著聊；VC 聊完 → TG 歷史同步。

- 四階現象學時差模型：MICRO / SHORT / CIRCADIAN_NIGHT / LONG_NEW_DAY
- 動態邊界：≤600 tokens 語音延遲預算裁切；隔夜對話自動淡出工作記憶
- 原子寫：臨時檔 + os.replace（stdlib，Windows 同卷原子），0 新依賴
- 純 stdlib，可被 clients/voice_companion 與 src/ 兩側 import（0 循環依賴）
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

# 語音延遲 Token 預算（保障 TTFA 首字出聲速度）
TOKEN_BUDGET = 600
# 單檔最大輪數（FIFO 滾動，防無限增長）
MAX_TURNS = 200

# 四階時差閾值（秒）
MICRO_MAX = 300      # < 5 分鐘
SHORT_MAX = 7200      # 5 分鐘 ~ 2 小時
LONG_MIN = 28800      # > 8 小時


@dataclass
class DialogueTurn:
    role: str            # "user" 或 "assistant"
    content: str
    channel: str         # "telegram" 或 "voice"
    timestamp: float     # time.time()


_PHASE_GUIDANCE = {
    "MICRO": (
        "Bryan 剛在其他介面與你對話（同一即時現場）。請直接延續該話題，"
        "嚴禁任何開場問候廢話，代名詞直接對齊剛才的話題主體。"
    ),
    "SHORT": (
        "距離上次對話已過去短暫時間。話題仍存於心中，請自然過渡續接，"
        "帶有事情告一段落的平靜回首感。"
    ),
    "CIRCADIAN_NIGHT": (
        "目前已進入深夜作息時段。語調請更輕柔安靜，多用短句，主動關心休息，"
        "嚴禁展開複雜繁重的嚴肅話題。"
    ),
    "LONG_NEW_DAY": (
        "距離上次對話已隔夜，昨天的對話已經過夜間沉澱為記憶。"
        "新的一天請自然致意重聚，嚴禁生硬倒帶昨天的逐字話語；"
        "若有未決承諾可自然提及。"
    ),
}


def _repo_root() -> Path:
    """推算 repo 根（clients/voice_companion/session_store.py → 上三層）。"""
    return Path(__file__).resolve().parents[2]


def _estimate_tokens(turns: List[DialogueTurn]) -> int:
    """粗略 token 估算：CJK 為主內容約 1 字 ≈ 1 token，保守取 len//2。"""
    return sum(max(1, len(t.content) // 2) for t in turns)


class SessionStore:
    """共享短期對話儲存（per (agent_id, user_id) 一個 JSON 檔，原子寫）。"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = (
            Path(data_dir) if data_dir is not None
            else _repo_root() / "data" / "sessions"
        )

    # ── 路徑 ──────────────────────────────────────────────
    def _path(self, agent_id: str, user_id: str) -> Path:
        return self.data_dir / f"{agent_id}_{user_id}.json"

    # ── 讀寫 ──────────────────────────────────────────────
    def _load_turns(self, agent_id: str, user_id: str) -> List[DialogueTurn]:
        path = self._path(agent_id, user_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            turns = [DialogueTurn(**t) for t in raw.get("turns", [])]
            return [t for t in turns if isinstance(t, DialogueTurn)]
        except Exception:
            return []  # 缺檔/損壞 → fail-silent 空列表

    def _save_turns(self, agent_id: str, user_id: str,
                    turns: List[DialogueTurn]) -> None:
        path = self._path(agent_id, user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"turns": [asdict(t) for t in turns]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)  # 原子替換（Windows 同卷）

    def append_turn(self, agent_id: str, user_id: str, role: str,
                    content: str, channel: str) -> None:
        """讀檔 → 追加 → 原子寫回（FIFO 滾動上限 MAX_TURNS）。"""
        try:
            turns = self._load_turns(agent_id, user_id)
            turns.append(DialogueTurn(
                role=role, content=content,
                channel=channel, timestamp=time.time(),
            ))
            if len(turns) > MAX_TURNS:
                turns = turns[-MAX_TURNS:]
            self._save_turns(agent_id, user_id, turns)
        except Exception:
            pass  # fail-silent：0 影響主流程

    # ── 四階時差 ──────────────────────────────────────────
    def _evaluate_temporal_phase(self, turns: List[DialogueTurn],
                                  now: float) -> tuple:
        """回傳 (phase, guidance)。判定順序：LONG → CIRCADIAN → SHORT → MICRO。"""
        if not turns:
            return "NO_TURNS", ""
        last = turns[-1]
        delta = now - last.timestamp
        now_dt = time.localtime(now)
        last_dt = time.localtime(last.timestamp)
        # 1. LONG_NEW_DAY：>8h 或跨日
        if delta > LONG_MIN or (now_dt.tm_yday != last_dt.tm_yday):
            return "LONG_NEW_DAY", _PHASE_GUIDANCE["LONG_NEW_DAY"]
        # 2. CIRCADIAN_NIGHT：now 在夜間窗（>=22 或 <7）且 last_turn 不在夜間窗
        now_night = now_dt.tm_hour >= 22 or now_dt.tm_hour < 7
        last_night = last_dt.tm_hour >= 22 or last_dt.tm_hour < 7
        if now_night and not last_night:
            return "CIRCADIAN_NIGHT", _PHASE_GUIDANCE["CIRCADIAN_NIGHT"]
        # 3. SHORT：300~7200（含 7200~28800 白天空窗補位）
        if delta >= MICRO_MAX:
            return "SHORT", _PHASE_GUIDANCE["SHORT"]
        # 4. MICRO
        return "MICRO", _PHASE_GUIDANCE["MICRO"]

    # ── 動態裁切 ──────────────────────────────────────────
    def _trim_to_budget(self, turns: List[DialogueTurn]) -> List[DialogueTurn]:
        """從最舊丟棄直到估算 ≤ TOKEN_BUDGET；至少保留 1 筆。"""
        trimmed = list(turns)
        while len(trimmed) > 1 and _estimate_tokens(trimmed) > TOKEN_BUDGET:
            trimmed.pop(0)
        return trimmed

    def get_active_context(self, agent_id: str, user_id: str,
                           now: Optional[float] = None) -> dict:
        """回傳 {"phase", "guidance", "history", "anchor"}。"""
        now = time.time() if now is None else now
        turns = self._load_turns(agent_id, user_id)
        phase, guidance = self._evaluate_temporal_phase(turns, now)
        if phase == "LONG_NEW_DAY":
            # 隔夜：不回傳昨天 raw 對話（防 LLM 複誦舊話），僅靠 anchor 引導
            history: List[dict] = []
        else:
            trimmed = self._trim_to_budget(turns)
            history = [{"role": t.role, "content": t.content} for t in trimmed]
        anchor = (
            f"[TEMPORAL CONVERSATION ANCHOR]\n"
            f"- 時差相位：{phase}\n"
            f"- 引導：{guidance}"
        )
        return {
            "phase": phase,
            "guidance": guidance,
            "history": history,
            "anchor": anchor,
        }
