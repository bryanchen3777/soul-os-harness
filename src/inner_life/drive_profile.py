"""src/inner_life/drive_profile.py — persona「三個不可變驅動」的**唯讀**解析。

CAMPAIGN-B-P1 §2.1（工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md）

為什麼需要這個模組
------------------
驅動（D1／D2／D3）目前**只存在於 persona 散文**裡（實測：全庫 grep ``^\\*\\*D[123] ``
命中 4 處，全部在 ``personas/agent_akane.md``）⇒ ``src/`` 內**沒有任何驅動解析能力**。
本模組把散文裡的驅動**解析成可稽核的結構**，讓「張力因角色而異」有第一個可觀測的
資料面。

解析規則（工單 §2.1，固定）
---------------------------
1. 讀 ``personas/<agent_id>.md``（**唯讀**；``Path.read_text``，**不得**寫入）。
2. 找到標題行 ``### 三個不可變驅動``；其後到下一個 ``###`` 或 ``***`` 之前的區段
   即驅動區段。
3. 區段內以 ``**<code> — <label>**``（code ∈ {D1,D2,D3}）開頭的行為一個驅動標題。
4. 每個標題之後的**第一個非空行**＝``trigger_phrase``；**第二個非空行**＝
   ``effect_phrase``。
5. 標題不存在 ⇒ 回傳 ``[]``；區段不存在 ⇒ 回傳 ``[]``；標題存在但缺觸發行或效應行
   ⇒ **該驅動不納入**（fail-closed，不補、不猜、不例外）。
6. ``load_drive_profile(agent_id)`` ＝ 解析結果的封裝；解析失敗或非空前置條件不滿
   ⇒ 回 ``None``。
7. **禁止**任何 fallback、模板生成或預設值；**禁止**把 non-akane agent 硬塞成
   akane 的內容。
8. 本票**只**對 ``agent_akane`` 有可解析結果；其他 agent 一律 ``[]``／``None``
   （**預期行為**，由 ``tests/inner_life/test_drive_profile.py`` 釘死）。

紀律
----
- **唯讀**：本模組**只**讀 persona；**不**寫、不改、不刪任何檔案（紅線：
  ``personas/**`` 不可動）。
- **fail-closed**：解析不出來就是解析不出來；**不猜、不補、不模板化**。
- **0 LLM、0 網路、0 寫入**：純解析。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("soul_os.inner_life.drive_profile")

#: repo 根（persona 是**靜態資源**，不是執行期資料根 ⇒ 絕不走 ``src.paths.data_root()``）。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: persona 檔案目錄（唯讀）。
PERSONAS_DIR = REPO_ROOT / "personas"

#: 驅動區段標題（逐字）。
SECTION_HEADING = "### 三個不可變驅動"

#: 驅動代碼的**固定序**（D1 → D2 → D3）。
DRIVE_CODES: Tuple[str, ...] = ("D1", "D2", "D3")

#: 驅動標題樣式：``**<code> — <label>**``（code ∈ {D1,D2,D3}）。
#:
#: 破折號容忍 em dash（—）／en dash（–）／hyphen（-）；persona 原文用 em dash。
_DRIVE_HEADING_RE = re.compile(r"^\*\*(D[123])\s*[—–\-]\s*(.+?)\*\*\s*$")


# ── 資料模型 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DriveSpec:
    """一條不可變驅動（逐字取自 persona 原文）。"""

    code: str
    label: str
    trigger_phrase: str
    effect_phrase: str
    source_persona: str
    source_line_span: Tuple[int, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "trigger_phrase": self.trigger_phrase,
            "effect_phrase": self.effect_phrase,
            "source_persona": self.source_persona,
            "source_line_span": list(self.source_line_span),
        }


@dataclass(frozen=True)
class DriveProfile:
    """某 agent 的驅動剖面（``drives`` 依 D1→D2→D3 固定序）。"""

    agent_id: str
    drives: List[DriveSpec]

    def codes(self) -> Tuple[str, ...]:
        return tuple(d.code for d in self.drives)

    def get(self, code: str) -> Optional[DriveSpec]:
        for drive in self.drives:
            if drive.code == code:
                return drive
        return None


# ── 解析 ─────────────────────────────────────────────────────────────────
def persona_path(agent_id: str) -> Path:
    """該 agent 的 persona 路徑（唯讀）。"""
    return PERSONAS_DIR / f"{agent_id}.md"


def _section_bounds(lines: List[str]) -> Optional[Tuple[int, int]]:
    """驅動區段的行號區間（0-based, [start, end)）；找不到標題 ⇒ ``None``。"""
    start: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.strip() == SECTION_HEADING:
            start = idx
            break
    if start is None:
        return None
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        # 下一個 `###`（含更深層級）或 `***` 分隔線 ⇒ 驅動區段結束。
        if stripped.startswith("###") or stripped.startswith("***"):
            end = idx
            break
    return (start, end)


def parse_drives(agent_id: str) -> List[DriveSpec]:
    """解析某 agent 的驅動（**唯讀**；解析不出來 ⇒ ``[]``）。

    規則見模組 docstring §2.1 第 1～5 條。**無 fallback、無模板、無預設值。**
    """
    if not isinstance(agent_id, str) or not agent_id.strip():
        return []
    path = persona_path(agent_id)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(
            "[drive-profile] persona 讀取失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return []
    try:
        lines = text.splitlines()
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.debug(
            "[drive-profile] persona 切行失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return []

    bounds = _section_bounds(lines)
    if bounds is None:
        # 區段不存在 ⇒ []（不是 None；None 只在封裝層表示「前置條件不滿」）。
        return []
    start, end = bounds

    source_persona = f"personas/{agent_id}.md"
    drives: List[DriveSpec] = []
    current_code: Optional[str] = None
    current_label: str = ""
    current_head_line: int = 0
    trigger: Optional[str] = None
    effect: Optional[str] = None
    effect_line: int = 0

    def _close_current() -> None:
        """收束目前這條驅動（缺觸發行或效應行 ⇒ 不納入；fail-closed）。"""
        nonlocal current_code, current_label, current_head_line, trigger, effect, effect_line
        if current_code is None:
            return
        if trigger is not None and effect is not None:
            drives.append(
                DriveSpec(
                    code=current_code,
                    label=current_label,
                    trigger_phrase=trigger,
                    effect_phrase=effect,
                    source_persona=source_persona,
                    source_line_span=(current_head_line, effect_line),
                )
            )
        current_code = None
        current_label = ""
        current_head_line = 0
        trigger = None
        effect = None
        effect_line = 0

    for idx in range(start + 1, end):
        raw = lines[idx]
        line_no = idx + 1  # 1-based 行號
        match = _DRIVE_HEADING_RE.match(raw.strip())
        if match:
            _close_current()
            current_code = match.group(1)
            current_label = match.group(2).strip()
            current_head_line = line_no
            continue
        if current_code is None:
            continue
        text_line = raw.strip()
        if not text_line:
            continue
        if trigger is None:
            trigger = text_line
            continue
        if effect is None:
            effect = text_line
            effect_line = line_no
    _close_current()

    # 固定序（D1 → D2 → D3）；非 D1/D2/D3 的代碼不會進來（regex 已限制）。
    order = {code: i for i, code in enumerate(DRIVE_CODES)}
    drives.sort(key=lambda d: order.get(d.code, len(order)))
    return drives


def load_drive_profile(agent_id: str) -> Optional[DriveProfile]:
    """驅動剖面封裝（**非空前置條件不滿 ⇒ ``None``**）。

    - 解析失敗（ persona 讀不到／非 dict 等）⇒ ``None``。
    - 解析出 0 條驅動 ⇒ ``None``（工單 §2.1 第 6 條）。
    """
    try:
        drives = parse_drives(agent_id)
    except Exception as exc:  # noqa: BLE001 — fail-closed（不猜、不補）
        logger.debug(
            "[drive-profile] 驅動解析失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return None
    if not drives:
        return None
    return DriveProfile(agent_id=agent_id, drives=drives)


__all__ = [
    "DRIVE_CODES",
    "PERSONAS_DIR",
    "REPO_ROOT",
    "SECTION_HEADING",
    "DriveProfile",
    "DriveSpec",
    "load_drive_profile",
    "parse_drives",
    "persona_path",
]
