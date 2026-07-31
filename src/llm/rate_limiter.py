"""
Shared LLM rate limiter for Soul OS background calls.

Lesson 38 (2026-07-30 Bry 拍板):
MiniMax provider 已知 529 overload 風險。所有 background LLM 呼叫
(diary.py / dream_event.py) 共用單一 asyncio.Semaphore(5),避免:
  - 22:00 night diary 10 個並發 + 22:05 dream event 5 個並發
    疊加超過 5 → 集體 429/529
  - 不同時段因 drift 重疊,讓實際並發數超出預期

⚠️ Scope 限制:此限流目前**只涵蓋 background 排程呼叫**,
不包含主對話路徑 (proxy.py 的 _complete_with_retry)。
如果未來觀察到 22:00-22:06 期間主對話也撞 rate limit,
再考慮擴大範圍。擴大前須評估對互動延遲的影響。

用法:
    from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT

    async with LLM_CONCURRENCY_LIMIT:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(...)
"""
import asyncio

# 單一 instance,所有 background caller 共用
LLM_CONCURRENCY_LIMIT: asyncio.Semaphore = asyncio.Semaphore(5)
