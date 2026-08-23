"""
src/work/roles.py
Role model + role → capability mapping（2A §5）。

純 Python domain，零 DSH coupling：
- capability 名稱是 capability-neutral（非 DSH tool 名）
- role 是 Soul OS 自己的概念，owner / assigned_agents 存 role，不存 DSH agent/session id

Canonical 來源（權威，不得修改）：
- docs/DSH-WORK-CONTRACT.md §5.1（Role → Capability 矩陣）
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """
    Work 的 role model（2A §5.1）。

    owner / assigned_agents 存 role，不存 DSH agent/session id（2A §8.2）。
    human 供 Human Approval 用（2A §3.1：Approval 只能由 Human 產生）。
    """
    RESEARCHER = "researcher"
    DEVELOPER = "developer"
    TESTER = "tester"
    AUDITOR = "auditor"
    CHIEF = "chief"
    HUMAN = "human"


class CapabilityNotAuthorizedError(PermissionError):
    """role 不具備 result_type 對應的 capability（2A §5.1）。"""


# Role → Capability 矩陣（2A §5.1 唯一 authoritative source）。
# capability 名稱是 capability-neutral（非 DSH tool 名）。
#
# Contract change（DSH-DEV-ENV-0 §0.5，Owner 拍板 2026-08-23）：Developer 加
# `artifact.create`——定性「修復 2A §5.1 / 2B §5 / 實務三處不一致」（2B §5 明說
# developer 對 artifact store 是 write）。developer 產 text artifact 自此合法
# （P1-C0 enforcement 相應由 DENY 遷移為 PASS）。
ROLE_CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.RESEARCHER: frozenset({"workspace.read", "research", "artifact.create"}),
    Role.DEVELOPER: frozenset({"workspace.read", "isolated.write", "test.execute", "git.branch", "artifact.create"}),
    Role.TESTER: frozenset({"workspace.read", "test.execute", "evidence.create"}),
    Role.AUDITOR: frozenset({"workspace.read", "review", "evidence.create"}),
    Role.CHIEF: frozenset({"orchestration", "decision", "work.assign"}),
    Role.HUMAN: frozenset({"approval", "privileged actions"}),
}


def capabilities_for(role: Role | str) -> frozenset[str]:
    """回傳 role 的 capability 集合（2A §5.1）。"""
    return ROLE_CAPABILITIES[Role(role)]


def has_capability(role: Role | str, capability: str) -> bool:
    """回傳 role 是否具備指定 capability（2A §5.1）。"""
    return capability in ROLE_CAPABILITIES[Role(role)]
