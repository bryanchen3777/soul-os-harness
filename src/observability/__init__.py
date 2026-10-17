"""src/observability/ — 可觀測性 sidecar（OBS-CHRONO-1）。

凍結禁令（沿用 src/memory/continuity.py 的紀律）：本套件**不得** import
任何 LLM client。它只能被動觀察已組裝好的資料，一旦反向相依就會製造
「觀測層改變被觀測對象」的迴圈。
"""
