"""
src/work_adapter/__init__.py
DSH-P0-1 — Work Execution Adapter（Python 端）。

獨立 package，**不放進 src/work/ Domain Core**（避免污染零 DSH boundary）：
- Domain Core（src/work/ 十一模組）零 DSH import 永久鎖死；本 package 只
  **import** Domain Core 的 contract（BridgeMessage / HandoffResult / kernel /
  workflow），Domain Core 永不 import 本 package。
- 本 package 只做 transport/invoke wiring：無 durable write authority，
  durable write 一律回 Domain Core（WorkflowOrchestrator.consume_handoff）。
"""
from .bridge import BridgeExecutionError, WorkExecutionBridge
from .execution import build_execution_request, execute_work

__all__ = [
    "BridgeExecutionError",
    "WorkExecutionBridge",
    "build_execution_request",
    "execute_work",
]
