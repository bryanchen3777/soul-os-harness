"""src/soul/proactive_policy.py — 主動 DM 的頻率護欄（DELIVERABILITY-RELEASE-1）。

Owner 裁定（2026-09-13，票 5 / 最小放行集 R2）逐字原文：

    放寬 4h 硬阻斷 ＋ (a) 先唯讀量測 longing 曲線 (b) 單調遞增則加每角色每日上限 1 則
    (c) 不變量 `daily_proactive_cap(silence=3d) == daily_proactive_cap(silence=3h)` 測試釘死
    (d) TA-2 措辭逐字不動、TA-2 狀態不得參與發起判定

(a) 唯讀量測結果（`src/agent/emotion.py:compute_longing`，ruka ＝ 白名單唯一角色，
    `configs/default.yaml:18` `intimacy_level: 60`）：

    沉默 1h→0.0250 / 3h→0.0750 / 6h→0.1500 / 12h→0.3000 / 1d→0.6000 / 2d→0.6000 / 3d→0.6000

    曲線在 [0, 24h] 嚴格遞增、>= 24h 飽和於 `intimacy/100`
    （`compute_longing` 的 `silence_factor = clamp(silence_minutes / 1440, 0, 1)`），
    **全程不存在任何遞減區間 → 「單調遞增（單調不減）」成立** → 走 (b) 分支：加每日上限。

本模組是**純函式模組**：0 I/O / 0 讀檔 / 0 時間相依 / 0 LLM / 0 隨機。
`daily_proactive_cap()` 的回傳值**與沉默時長無關**（(c) 不變量的實作保證）——
`silence_minutes` 只作為**輸入簽章**存在，用途是把「上限不得隨沉默時長變動」這件事
**結構性**釘死：任何「沉默越久 → 上限越鬆」的改動，都會立刻破壞
`daily_proactive_cap(3d) == daily_proactive_cap(3h)` 這條不變量（測試釘死）。
"""
from __future__ import annotations

from typing import Optional

# 每角色每日主動 DM 上限（Owner 裁定 (b)：固定 1 則）。
# 刻意寫成常數而非「沉默時長的函式」—— 上限是**政策**，不是沉默的函數。
PROACTIVE_DM_DAILY_CAP_PER_AGENT = 1


def daily_proactive_cap(silence_minutes: Optional[float] = None) -> int:
    """回傳「每個角色每日允許的主動 DM 上限」。

    回傳值恆為 `PROACTIVE_DM_DAILY_CAP_PER_AGENT`（= 1）。

    不變量（Owner 裁定 (c)，由 `tests/test_deliverability_release_1.py` 釘死）：

        daily_proactive_cap(silence=3d) == daily_proactive_cap(silence=3h)

    沉默時長**不參與**計算（不查表、不插值、不加成、不設區間）→
    長沉默不會變成騷擾許可證。`None` = 未量測，同樣回傳同一常數。
    """
    return PROACTIVE_DM_DAILY_CAP_PER_AGENT
