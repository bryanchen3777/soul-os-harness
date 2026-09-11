"""
user_identity — 通道身分正規化（單一事實來源, MEM-IDENTITY-1）

背景（已實證）：
  三模態共用同一個 SAGE graph.sqlite，但 source_pair 身分分裂造成
  語音↔文字的記憶互相遮蔽：
    - VC 寫入側硬編碼 "bryan:<agent_id>"（canonical）
    - TG/Web inbound 帶數字 user id "1696287850" → 寫成 "1696287850:<agent_id>"
  讀側 (middleware.prefetch) 只放行 f"{target_user_id}:{agent_id}"，
  TG/Web 回合看不到 VC 寫的 14 筆；主動回合 (filter="bryan:*") 看不到 TG/Web 的 8 筆。

  修法：記憶層內部把通道身分正規化為 canonical 使用者 id，並在讀側放行
  該使用者的所有已知身分別（別名集）。正規化只發生在記憶層內部，
  絕不修改 event payload 的 target_user_id（router.py:197,219 用它做
  TG 投遞路由，改成 "bryan" 會讓推播送往非數字 chat id 而故障）。

  別名證據（拍板 2026-09）：
    - "1696287850" : src/io/channels/telegram.py:44  _BRYAN_TG_USER_ID = 1696287850
    - "user_bryan" : src/io/channels/router.py:68   _BRYAN_ENTITY_ID = "user_bryan"
  純 stdlib、0 依賴。
"""

from __future__ import annotations

CANONICAL_USER_ID = "bryan"

# Bryan 的已知身分別（對照拍板證據；大小寫不敏感, int/str 皆可）。
# 注意: middleware.py:135 註解提到 web 端 "bryan_test"，但全 repo 無任何程式碼
# 把它當 target_user_id 使用 → 依「沒有證據一律不加」原則, 不放入別名表。
_BRYAN_ALIASES = frozenset({
    "bryan",
    "user_bryan",
    "1696287850",
})


def canonical_user_id(raw) -> str:
    """把通道身分正規化為 canonical 使用者 id。

    已知的 Bryan 身分別（大小寫不敏感、int/str 皆可）→ "bryan"；
    其他任何值 → 原樣回傳（字串化），以保留多使用者隔離。None/空 → CANONICAL_USER_ID。
    """
    if raw is None:
        return CANONICAL_USER_ID
    raw_str = str(raw)
    if not raw_str.strip():
        return CANONICAL_USER_ID
    if raw_str.strip().lower() in _BRYAN_ALIASES:
        return CANONICAL_USER_ID
    return raw_str


def user_identity_aliases(canonical: str) -> set[str]:
    """回傳該 canonical 使用者的所有已知身分別（含 canonical 自身）。

    canonical 為 "bryan" 時至少包含 {"bryan", "user_bryan", "1696287850"}；
    其他使用者 → {canonical}。
    """
    if canonical is None or str(canonical).strip().lower() == CANONICAL_USER_ID:
        return set(_BRYAN_ALIASES)
    return {str(canonical)}


def source_pair_candidates(raw_user, agent_id: str) -> set[str]:
    """回傳讀側應放行的 source_pair 候選集合。

    = {f"{alias}:{agent_id}" for alias in user_identity_aliases(canonical_user_id(raw_user))}
    """
    return {f"{alias}:{agent_id}" for alias in user_identity_aliases(canonical_user_id(raw_user))}