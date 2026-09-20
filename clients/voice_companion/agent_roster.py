"""
agent_roster.py — VC-ASR-CHANNEL-HINT-1 名冊讀取（薄、純、fail-closed）。

用途：語音通道提示的 `【名冊】` 一行需要「真實顯示名」清單。本模組只做一件事：
把**現成來源**的名冊讀出來；讀不到／任何異常 ⇒ 回 `[]`，由呼叫端省略名冊行
（frozen contract 4.3：標與 4.2 三段照出，名冊缺席不影響）。

來源與理由（VC-ASR-CHANNEL-HINT-1 recon 實測，非設計選擇）：
  1. 成員與順序：`configs.loader.load_config()` 的 `agents` 表（enabled 且帶 id），
     與主服務 `src/io/gateway.py:_list_agents()` 同源。
  2. 顯示名：`src/io/gateway.py` 的 `AGENT_DISPLAY_NAMES` 字面值。**這是全庫唯一
     一份顯示名對照表**——`configs/default.yaml` 的 agents 表只有 id / class /
     intimacy_level / enabled，**沒有 name 欄位**（實測：10 隻 agents 全無顯示名）；
     `src/llm/proxy.py:AGENT_NAMES` 只有 3 隻，不是名冊。
     取得方式為 **ast 讀原始碼字面值**，而非 `import src.io.gateway`：gateway 模組
     頂層 import fastapi / eventbus / agent.emotion（主服務重依賴），本票明令禁止
     把該依賴拉進 VC 進程。讀字面值 = 0 import 副作用、0 手抄死名單、
     仍是單一事實來源（兩處定義漂移由測試 `test_display_names_match_gateway` 釘住）。

契約：lazy（被呼叫才讀檔）、零網路、例外全吞 —— 絕不 raise 到呼叫端。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]  # clients/voice_companion/ → repo root
_GATEWAY_SRC = _REPO_ROOT / "src" / "io" / "gateway.py"
_NAMES_CONST = "AGENT_DISPLAY_NAMES"


def _display_name_map() -> dict[str, str]:
    """讀 `src/io/gateway.py` 的 `AGENT_DISPLAY_NAMES` 字面值（0 import、0 副作用）。

    只認頂層 `AnnAssign` / `Assign` 的 `AGENT_DISPLAY_NAMES`；找不到或非字面值 ⇒ `{}`。
    例外不在此吞（由 `display_names()` 統一吞），保持本函式語意單一。
    """
    tree = ast.parse(_GATEWAY_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == _NAMES_CONST):
            continue
        if value is None:  # AnnAssign 無值（純宣告）
            continue
        raw = ast.literal_eval(value)
        if not isinstance(raw, dict):
            continue
        return {
            k: v
            for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str) and v
        }
    return {}


def display_names() -> list[str]:
    """回傳 enabled agent 的顯示名（config 順序）；**任何失敗 ⇒ `[]`，永不 raise**。

    只輸出「config 有列且 gateway 名冊有對照」的名字：新 agent 尚未登記顯示名時
    靜默省略（名冊是提示，不是完整性檢查）。
    """
    try:
        names = _display_name_map()
        if not names:
            return []
        from configs.loader import load_config

        cfg = load_config() or {}
        out: list[str] = []
        for entry in cfg.get("agents", []) or []:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            name = names.get(entry.get("id"))
            if name:
                out.append(name)
        return out
    except Exception:  # noqa: BLE001 — fail-closed：讀不到名冊 ≠ 讓回合失敗
        return []
