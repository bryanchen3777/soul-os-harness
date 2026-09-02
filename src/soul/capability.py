"""
src/soul/capability.py — Soul Capability Definitions + Awareness Projection (CA-2)

定位（照 docs/SOUL-CAPABILITY-AWARENESS-DESIGN.md，CA-1 已鎖）：
  - **Definition**（系統事實，machine-readable，唯一權威源）：``CAPABILITY_DEFINITIONS``
    常數。v1 有 3 個：``communicate``（→ proactive_message）、``observe_environment``、
    ``reflect_memory``。不上 YAML。
  - **Awareness**（靈魂對自我的認知）：由 Definition 投影成 CAPABILITY block，
    在 proxy.py 的 identity 之後、emergent 之前注入。LLM 只看 Awareness，不看 Definition。
  - **正式原則**：``Capability expands the action space; it does not select an action.``
    CAPABILITY block 是「我知道我能」，不是「我應該」——只擴展 action space，
    不選擇 action（選擇權在 Agency Decision）。

v1 投影規則（死規則，工單已鎖）：
  - 只讀、inference-time：讀 ``CAPABILITY_DEFINITIONS`` 常數，不寫任何狀態
    （sidecar 是 append-only 審計記錄，不是狀態）。
  - fail-silent：讀取/格式化失敗 → 空字串，prompt 與未實現時完全等價。
  - deterministic：無 relevance score / decay / random / confidence 動力學；
    v1 全量投影（3 個 capability），按 dict 插入順序。
  - 措辭原則：expression 陳述「能」（can），不陳述「應」（should）。

不碰 frozen contract：不改 Agency 4 stages / TriggerEnvelope / InnerLifeEvent /
4 handlers / SAGE 寫入；不 import ``src/work/roles.py``（DSH ROLE_CAPABILITIES
隔離，Q7 死規則），反之亦然。

可觀測性：每次投影把投影到的 capability id 記錄進自有 sidecar
``data/soul/capability_projection_trace.jsonl``（append-only，獨立 schema），
並打 logger.info——與 emergent projection 的 ``elevation_projection_trace.jsonl``
模式對齊（CA-2 定文件名與位置）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

logger = logging.getLogger("soul_os.soul.capability")

# 自有 sidecar 文件名（掛在 data_root()/soul/ 下，與 emergent 的
# elevation_projection_trace.jsonl 模式對齊；CA-2 定文件名與位置）。
CAPABILITY_TRACE_FILENAME = "capability_projection_trace.jsonl"


@dataclass(frozen=True)
class CapabilityDefinition:
    """單一 capability 的系統事實定義（id + expression 兩字段）。

    - ``id``：machine-readable，穩定、小寫、無空格。v1 有 ``"communicate"`` /
      ``"observe_environment"`` / ``"reflect_memory"``。
    - ``expression``：人類可讀，投影時原樣使用。**措辭原則：陳述能力（can），
      不陳述義務（should）**——寫「你可以…」不寫「你應該…」，防止從「我能」滑成「我應」。
    """

    id: str
    expression: str


# 唯一權威源（Q1：單一 runtime source，不上 YAML）。
# v1 ontology 極小：perceive / remember / interpret 是 pipeline/substrate，
# **不是** capability，不進這裡。
CAPABILITY_DEFINITIONS: dict[str, CapabilityDefinition] = {
    "communicate": CapabilityDefinition(
        id="communicate",
        expression="你可以主动给 Bryan 发消息（proactive_message）。",
    ),
    "observe_environment": CapabilityDefinition(
        id="observe_environment",
        expression="你可以感知外部环境（天气、时间、日历），丰富自己的认知。",
    ),
    "reflect_memory": CapabilityDefinition(
        id="reflect_memory",
        expression="你可以回顾自己的日记与记忆，整理思绪。",
    ),
}


def _utcnow_iso() -> str:
    """當前 UTC 時刻的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    """解析 sidecar 目錄：顯式傳入 > data_root()/soul（對齊 emergent_projection）。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root  # lazy import 避免 cycle

    return data_root() / "soul"


def _append_projection_trace(
    store_dir: Path,
    agent_id: str,
    projected: Sequence[CapabilityDefinition],
) -> None:
    """把本次投影的 capability id 記錄進自有 sidecar（append-only，失敗靜默）。

    記錄帶 ts + agent_id + capability id 列表，讓之後寫入的 InnerLifeEvent 能
    按 agent_id + ts 時間窗串回本次投影了哪些 capability。
    這是**審計記錄**，不是狀態（不影響任何 gate / 狀態機 / 事件總線 payload）。
    """
    try:
        path = store_dir / CAPABILITY_TRACE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _utcnow_iso(),
            "event_type": "capability_projected",
            "agent_id": agent_id,
            "projected_capability_ids": [c.id for c in projected],
            "capability_count": len(projected),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("capability projection trace write failed (%s): %s", path, exc)


def project_capabilities(
    agent_id: str,
    *,
    store_dir: Optional[Any] = None,
    record_trace: bool = True,
) -> List[CapabilityDefinition]:
    """把 CAPABILITY_DEFINITIONS 投影出來（read-side，inference-time）。

    規則（v1 死規則）：
      1. 只讀 ``CAPABILITY_DEFINITIONS`` 常數，不寫任何狀態。
      2. 全量投影（v1 有 3 個），按 dict 插入順序（deterministic）。
      3. 無定義 → 回 []（不 raise，fail-silent）。

    Returns:
        list[CapabilityDefinition]：按定義順序；無定義回 []。
    """
    projected: List[CapabilityDefinition] = list(CAPABILITY_DEFINITIONS.values())
    for c in projected:
        logger.info(
            "capability projected id=%s agent_id=%s",
            c.id,
            agent_id,
        )
    if record_trace and projected:
        _append_projection_trace(_resolve_store_dir(store_dir), agent_id, projected)
    return projected


def format_capability_block(
    agent_id: str,
    *,
    store_dir: Optional[Any] = None,
    record_trace: bool = True,
) -> str:
    """把投影結果格式化成 CAPABILITY block 字符串（注入 prompt 用）。

    無定義時回 ""（fail-silent，prompt 與未實現時完全等價）。

    block 格式（injection 時以 ``\\n`` 粘進 system_parts，identity 之後、emergent 之前）：

        [CAPABILITY] 以下是你知道自己能做的事——这是能力声明（「我知道我能」），
        不是行为指令（「我应该」）。它们只说明什么是可能的，不决定你现在该做什么；
        选择权在你。
        - 你可以主动给 Bryan 发消息（proactive_message）。
        - 你可以感知外部环境（天气、时间、日历），丰富自己的认知。
        - 你可以回顾自己的日记与记忆，整理思绪。

    anti-runaway invariant：格式與措辭顯式區分「能力聲明」與「行為指令」，
    防止 LLM 拿 capability 當義務回環自證。
    """
    projected = project_capabilities(agent_id, store_dir=store_dir, record_trace=record_trace)
    if not projected:
        return ""
    lines = [
        "[CAPABILITY] 以下是你知道自己能做的事——这是能力声明（「我知道我能」），"
        "不是行为指令（「我应该」）。它们只说明什么是可能的，不决定你现在该做什么；"
        "选择权在你。",
    ]
    for c in projected:
        lines.append(f"- {c.expression.strip()}")
    return "\n".join(lines) + "\n"


__all__ = [
    "CAPABILITY_DEFINITIONS",
    "CAPABILITY_TRACE_FILENAME",
    "CapabilityDefinition",
    "format_capability_block",
    "project_capabilities",
]
