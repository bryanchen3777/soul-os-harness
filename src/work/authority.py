"""
src/work/authority.py
Authority Boundary — capability policy（2A §5）+ approval model（2C）。

把 2C 的「Approval = Human authority」落成 executable enforcement：

- `CapabilityPolicy`：role → capability → authorized（2A §5.1 靜態矩陣）。
  高風險 capability（2A §5.2）需 approval gate。
- `Approval`：Human 的明確授權（2C §2）。
- `CapabilityGrant`：grant_id → approval_id（provenance chain 一對一，2C §6）。
- `AuthorityManager.grant / revoke / consume / is_authorized`：把 approval 轉成
  bounded action scope 的 enforcement。

核心原則（2C §1）：
> Approval 授權的是 capability + bounded action scope，不是「允許工作」。

Identity boundary（本模組的 seam）：
AuthorityManager 不自己「認證 Human」。它接受外部提供、不可由 Agent 自己製造的
`HumanAuthorityContext`（frozen，含 authority_token），經注入的
`HumanAuthorityPort.authenticate(context)` 驗證。無注入 port → deny-by-default。
`human_identity` 是資料，不是 proof；`granted_by="human"` 不是 authentication。

No agent may manufacture, infer, or substitute a Human Approval（2A invariant #2）。
Every privileged Agent Action MUST have exactly one valid governing Capability Grant
traceable to one Human Approval（2C §8 #5）。斷鏈 = authorization failure。

純 Python domain，零 DSH coupling：
- 不 import 任何 DSH type / id
- capability 名稱是 capability-neutral（非 DSH tool 名）
- role 是 Soul OS 自己的概念，不存 DSH agent/session id

Canonical 來源（權威，不得修改）：
- docs/DSH-WORK-CONTRACT.md §5（capability authorization）
- docs/DSH-HUMAN-AUTHORITY.md §2（Approval schema）、§5（revocation）、§6（provenance）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .bridge import DURABLE_WRITER
from .persistence import (
    AuthorityEvent,
    AuthorityEventType,
    AuthorityStore,
    NoAuthorityStoreError,
)
from .roles import Role, has_capability

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """UTC 帶時區的現在時間（與 schema.py 的 timestamp 慣例一致）。"""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
# 1. Capability Policy（2A §5）
# ─────────────────────────────────────────────

# 高風險 capability（2A §5.2）：需 approval / policy gate。
HIGH_RISK_CAPABILITIES = frozenset({
    "production.write",
    "git.commit",
    "git.push",
    "deploy",
    "external.publish",
})


class CapabilityPolicy:
    """role → capability → authorized（2A §5.1）。

    `authorize` 用 `roles.ROLE_CAPABILITIES`（2A §5.1 唯一 authoritative source）。
    高風險 capability（2A §5.2）需 approval gate：即使 role 具備該 capability，
    也必須有 valid CapabilityGrant（來自 Human Approval）才能執行。
    """

    def authorize(self, role: Role | str, capability: str) -> bool:
        """回傳 role 是否具備 capability（2A §5.1 靜態矩陣）。"""
        return has_capability(role, capability)

    def is_high_risk(self, capability: str) -> bool:
        """回傳 capability 是否為高風險（2A §5.2）。"""
        return capability in HIGH_RISK_CAPABILITIES

    def requires_approval(self, capability: str) -> bool:
        """回傳 capability 是否需 approval gate（2A §5.2）。"""
        return self.is_high_risk(capability)


# ─────────────────────────────────────────────
# 2. Approval / Grant / Action / Context schema（2C §2 / §6）
# ─────────────────────────────────────────────

class ActionScope(str, Enum):
    """Approval 的 action_scope（2C §2 / §3）。"""
    SINGLE_ACTION = "single_action"   # 一次 approval = 一次 action，用完即失效
    WORK_SCOPED = "work_scoped"       # 對該 Work 預先宣告的 capability/action set


class Approval(BaseModel):
    """
    Human Approval（2C §2）。

    Human 批准的是「這個 capability 對這個具體 action/context 的授權」，
    不是模糊的「批准 git.push」。`granted_by` 是 human_identity 資料，不是
    proof——authentication 走 `HumanAuthorityPort`（2A invariant #2：
    No agent may manufacture, infer, or substitute a Human Approval）。

    frozen：authority state 對 caller 不可變（I6）。撤銷走 `AuthorityManager.revoke`。
    """
    model_config = ConfigDict(frozen=True)

    approval_id: str = Field(default_factory=lambda: str(uuid4()))
    work_id: str
    capability: str  # capability-neutral，如 "git.commit"，不是 DSH tool 名
    requested_action: dict[str, Any] = Field(default_factory=dict)  # 具體 action/context
    action_scope: ActionScope
    work_scope: str | None = None  # 2C §2：work_scope = work_id（work_scoped 的 scope 錨點）
    grantee_role: str  # developer | chief | ...
    granted_by: str  # human_identity（資料，非 proof；authentication 走 HumanAuthorityPort）
    granted_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None  # null 僅限低風險；production.write 必須有 expiry
    revoked_at: datetime | None = None
    scope_constraints: dict[str, Any] = Field(default_factory=dict)

    def is_revoked(self) -> bool:
        """回傳 approval 是否已撤銷（2C §5）。"""
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        """回傳 approval 是否已過期（2C §4）。expires_at=None 視為不過期。"""
        if self.expires_at is None:
            return False
        return (now or _utcnow()) >= self.expires_at


class CapabilityGrant(BaseModel):
    """
    Capability Grant（2C §6）：grant_id → approval_id（provenance chain 一對一）。

    每個 privileged Agent Action 必須有且只有一個 valid governing grant，
    traceable to one Human Approval。grant 是 approval 的 materialization：
    承載 capability / grantee_role / action_scope / expires_at，供 action 消費。

    frozen：caller 不能直接 mutate（`grant.consumed = True` 消失，I6）。
    消費走 `AuthorityManager.consume`（I8：single_action exactly once）。
    """
    model_config = ConfigDict(frozen=True)

    grant_id: str = Field(default_factory=lambda: str(uuid4()))
    approval_id: str  # grant → approval（一對一 provenance，2C §6）
    work_id: str
    capability: str
    grantee_role: str
    action_scope: ActionScope
    issued_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None
    consumed: bool = False  # single_action：用完即失效（2C §3）


class AgentAction(BaseModel):
    """
    Agent Action（2C §6）：action_id → grant_id（provenance chain 一對一）。

    privileged action 的 governing grant 由 grant_id 指向；斷鏈 = authorization
    failure（2C §8 #5 / #6）。frozen：caller 不能 mutate action 的 authority state。
    """
    model_config = ConfigDict(frozen=True)

    action_id: str = Field(default_factory=lambda: str(uuid4()))
    grant_id: str  # action → grant（一對一 provenance，2C §6）
    work_id: str
    role: str
    capability: str
    action: dict[str, Any] = Field(default_factory=dict)


class HumanAuthorityContext(BaseModel):
    """
    Human Authority Context（identity seam）。

    由 trusted Human Authority boundary（未來 DSH Adapter / runtime integration）
    簽發，Agent 自己無法製造。`identity` 是資料，`authority_token` 是 proof。
    AuthorityManager 不產生 context、不實作認證，只接受注入的 port 委派驗證。
    """
    model_config = ConfigDict(frozen=True)

    identity: str            # human identity（資料，非 proof）
    authority_token: str     # authenticated handle（proof，由 trusted boundary 簽發）
    issued_at: datetime
    expires_at: datetime | None


@runtime_checkable
class HumanAuthorityPort(Protocol):
    """Human authority 驗證 seam（由未來 Adapter / runtime integration 實作）。

    AuthorityManager 不自己認證 Human，只委派 `authenticate(context)`。
    """
    def authenticate(self, context: HumanAuthorityContext) -> bool: ...


# ─────────────────────────────────────────────
# 3. Exceptions
# ─────────────────────────────────────────────

class NotHumanGrantorError(PermissionError):
    """Human authority 驗證失敗：port 未注入（deny-by-default）或 authenticate 回傳 False。"""


class ApprovalRevokedError(ValueError):
    """approval 已撤銷（2C §5）。"""


class ApprovalExpiredError(ValueError):
    """approval 已過期（2C §4）。"""


class InvalidApprovalError(ValueError):
    """approval 無效（如 production.write 缺 expires_at，2C §4）。"""


class ApprovalNotFoundError(KeyError):
    """revoke 找不到指定 approval_id。"""


class GrantNotFoundError(KeyError):
    """consume 找不到指定 grant_id。"""


class GrantAlreadyConsumedError(ValueError):
    """single_action grant 已消耗（I8：exactly once）。"""


class InvalidGrantError(ValueError):
    """grant 不適用於 consume（如 work_scoped 不支援 single_action consumption）。"""


def _satisfies_scope_constraints(
    action: dict[str, Any], constraints: dict[str, Any]
) -> bool:
    """回傳 action 是否滿足 scope_constraints（2C §2 額外約束）。

    scope_constraints 是 Human 對 action 的額外約束（如 {"branch": "main"}）。
    每個 constrained key 在 action 中的值必須相等；缺 key 或值不符 = 不滿足。
    空 constraints（{}）視為無約束，恆滿足。
    """
    for key, value in constraints.items():
        if action.get(key) != value:
            return False
    return True


def _coerce_approval(payload: dict[str, Any]) -> Approval | None:
    """從 event payload 防禦式解析 Approval；malformed → None（skip，不 crash）。

    payload 用 .get() 取 "approval"；缺 key / 非 dict / 欄位無效都視為 malformed，
    留 log 並回傳 None，由 fold 跳過該 event（不產生半套 state）。
    """
    data = payload.get("approval")
    if not isinstance(data, dict):
        logger.warning(
            "[AuthorityManager] malformed approval payload skipped: %r", data
        )
        return None
    try:
        return Approval(**data)
    except (ValueError, TypeError) as e:
        logger.warning(
            "[AuthorityManager] malformed approval payload skipped: %s", e
        )
        return None


def _coerce_grant(payload: dict[str, Any]) -> CapabilityGrant | None:
    """從 event payload 防禦式解析 CapabilityGrant；malformed → None（skip，不 crash）。

    payload 用 .get() 取 "grant"；缺 key / 非 dict / 欄位無效都視為 malformed，
    留 log 並回傳 None，由 fold 跳過該 event（不產生半套 state）。
    """
    data = payload.get("grant")
    if not isinstance(data, dict):
        logger.warning(
            "[AuthorityManager] malformed grant payload skipped: %r", data
        )
        return None
    try:
        return CapabilityGrant(**data)
    except (ValueError, TypeError) as e:
        logger.warning(
            "[AuthorityManager] malformed grant payload skipped: %s", e
        )
        return None


def _fold_authority_events(
    events: list[AuthorityEvent],
) -> tuple[dict[str, Approval], dict[str, CapabilityGrant]]:
    """把 authority durable events fold 成 canonical registry（last-write-wins）。

    重建規則（2D §3：current state = fold(events)）：
    - approval_granted：approvals[approval_id] = Approval(payload["approval"])
    - grant_issued：grants[grant_id] = CapabilityGrant(payload["grant"])
    - approval_revoked：approvals[approval_id] = 帶 revoked_at 的 Approval（覆寫）
    - grant_consumed：grants[grant_id] = 帶 consumed=True 的 CapabilityGrant（覆寫）

    payload 承載完整序列化物件（model_dump(mode="json")），fold 時以最後一筆
    同 id 的 event 為準（revoke / consume 覆寫 grant / approval 的 authority state）。

    防禦式解析（P1）：payload 用 .get() 取 approval / grant；malformed event
    （缺 key / 非 dict / 欄位無效）跳過並留 log，不 crash、不產生半套 state。
    """
    approvals: dict[str, Approval] = {}
    grants: dict[str, CapabilityGrant] = {}
    for event in events:
        if event.event_type == AuthorityEventType.APPROVAL_GRANTED:
            approval = _coerce_approval(event.payload)
            if approval is not None:
                approvals[approval.approval_id] = approval
        elif event.event_type == AuthorityEventType.GRANT_ISSUED:
            grant = _coerce_grant(event.payload)
            if grant is not None:
                grants[grant.grant_id] = grant
        elif event.event_type == AuthorityEventType.APPROVAL_REVOKED:
            approval = _coerce_approval(event.payload)
            if approval is not None:
                approvals[approval.approval_id] = approval
        elif event.event_type == AuthorityEventType.GRANT_CONSUMED:
            grant = _coerce_grant(event.payload)
            if grant is not None:
                grants[grant.grant_id] = grant
    return approvals, grants


# ─────────────────────────────────────────────
# 4. AuthorityManager — executable enforcement
# ─────────────────────────────────────────────

class AuthorityManager:
    """authority boundary 的 executable enforcement。

    持有 approval / grant registry，把 2C 的「Approval = Human authority」落成：
    - `grant(approval, context)`：經 `HumanAuthorityPort.authenticate` 驗證 Human
      authority context 後才發 grant（deny-by-default，2A invariant #2）。
    - `revoke(approval_id)`：立即阻止新的 privileged action（2C §5）。
    - `consume(grant_id)`：single_action 原子消費（I8：exactly once）。
    - `is_authorized(action)`：只接受 action，從 canonical registry 依
      `action.grant_id` 取 canonical grant，逐欄比對 grant↔approval（I5），
      並做 exact-action structural comparison（requested_action / scope_constraints，
      2C §8 #7），斷鏈 = deny（2C §8 #5 / #6）。

    Registry boundary（核心 invariant）：
    > Caller never supplies or controls the canonical Approval / CapabilityGrant
    > registry used for authorization.

    canonical registry 是 manager-controlled storage，隔離於 public object graph：
    - 用 `__slots__`（無 `__dict__`）+ name-mangled slot（`__approvals` / `__grants`）
      存放，不暴露任何 public accessor。
    - caller 無法經 `mgr._grants` / `mgr._approvals` / `mgr.__dict__` / `vars(mgr)`
      注入、替換或 mutate canonical registry。
    - name mangling 是 defense-in-depth，不是 security boundary（Python 無真 private）；
      真正的 boundary 是 `is_authorized` 只讀 manager-controlled canonical state，
      並做 structural comparison（不信任 caller 提供的任何 grant/approval）。

    純 Python domain，零 DSH coupling，不 import 任何 DSH type。
    """

    __slots__ = ("_policy", "_human_authority", "__approvals", "__grants", "_store")

    def __init__(
        self,
        policy: CapabilityPolicy | None = None,
        human_authority: HumanAuthorityPort | None = None,
        store: AuthorityStore | None = None,
    ):
        self._policy = policy if policy is not None else CapabilityPolicy()
        self._human_authority = human_authority
        # canonical registry：manager-controlled storage，隔離於 public object graph。
        # name-mangled slot（無 __dict__），caller 無法經 public attribute 注入/替換。
        self.__approvals: dict[str, Approval] = {}
        self.__grants: dict[str, CapabilityGrant] = {}
        # durable log（2D §3 / §6）：approval/grant 的建立、撤銷、消費都記成
        # durable event。store=None 時維持純 in-memory（backward compatible）。
        self._store = store

    # ── grant ──

    def grant(self, approval: Approval, context: HumanAuthorityContext) -> CapabilityGrant:
        """把 Human Approval 轉成 CapabilityGrant（2C §6）。

        Human authority 由注入的 `HumanAuthorityPort.authenticate(context)` 驗證
        （I1：Human authority cannot be self-attested；I2：Approval must originate
        from a trusted Human Authority boundary）：
        - 無注入 port → deny（deny-by-default）。
        - authenticate 回傳 False → deny。
        - 不寫死 `if actor == "human"`、不寫死 `if token == "..."`。

        額外驗證：
        - approval 未撤銷、未過期（2C §4 / §5）。
        - production.write 必須 time-bounded（expires_at=null = invalid，2C §4）。
        - 一對一 provenance：同一 approval 只能發一次 grant（2C §6）。
        """
        if self._human_authority is None:
            raise NotHumanGrantorError(
                "no HumanAuthorityPort injected; human authority is deny-by-default"
            )
        if not self._human_authority.authenticate(context):
            raise NotHumanGrantorError(
                "human authority context failed authentication"
            )
        if approval.is_revoked():
            raise ApprovalRevokedError(
                f"approval {approval.approval_id} is revoked"
            )
        if approval.is_expired():
            raise ApprovalExpiredError(
                f"approval {approval.approval_id} is expired"
            )
        if approval.capability == "production.write" and approval.expires_at is None:
            raise InvalidApprovalError(
                "production.write approval must be time-bounded "
                "(expires_at=null is invalid, 2C §4)"
            )
        if approval.approval_id in self.__approvals:
            raise InvalidApprovalError(
                f"approval {approval.approval_id} already granted "
                f"(one-to-one provenance, 2C §6)"
            )

        grant = CapabilityGrant(
            approval_id=approval.approval_id,
            work_id=approval.work_id,
            capability=approval.capability,
            grantee_role=approval.grantee_role,
            action_scope=approval.action_scope,
            expires_at=approval.expires_at,
        )
        # durable log（2D §3 / §6）：approval 建立 + grant 發行都記成 durable event。
        # write-ahead：先 append durable truth，再 mutate in-memory registry。
        if self._store is not None:
            self._store.append(AuthorityEvent(
                event_type=AuthorityEventType.APPROVAL_GRANTED,
                payload={"approval": approval.model_dump(mode="json")},
            ), DURABLE_WRITER)
            self._store.append(AuthorityEvent(
                event_type=AuthorityEventType.GRANT_ISSUED,
                payload={"grant": grant.model_dump(mode="json")},
            ), DURABLE_WRITER)
        self.__approvals[approval.approval_id] = approval
        self.__grants[grant.grant_id] = grant
        return grant

    # ── revoke ──

    def revoke(self, approval_id: str) -> None:
        """撤銷 approval（2C §5）：立即阻止新的 privileged action。

        controlled operation：不 mutate 原 frozen Approval，而是產生新的
        authoritative state（revoked_at 已設）並替換 registry 中的物件（I6 / I9）。

        in-flight action 依 atomicity / safe-cancellation 決定（2C §5），
        本 MVP 只負責「新 action 的 authorization 立即失效」。
        """
        approval = self.__approvals.get(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(f"no approval with approval_id={approval_id}")
        revoked = approval.model_copy(update={"revoked_at": _utcnow()})
        # durable log（2D §3 / §6）：撤銷記成 durable event（覆寫 approval state）。
        if self._store is not None:
            self._store.append(AuthorityEvent(
                event_type=AuthorityEventType.APPROVAL_REVOKED,
                payload={"approval": revoked.model_dump(mode="json")},
            ), DURABLE_WRITER)
        self.__approvals[approval_id] = revoked

    # ── consume ──

    def consume(self, grant_id: str) -> None:
        """原子消費 single_action grant（2C §3 / I8：exactly once）。

        atomic validation → mark consumed → 後續 authorization = DENY。
        caller 不能手動設 `grant.consumed = True`（frozen，I6）；只能走本方法。
        """
        grant = self.__grants.get(grant_id)
        if grant is None:
            raise GrantNotFoundError(f"no grant with grant_id={grant_id}")
        if grant.action_scope != ActionScope.SINGLE_ACTION:
            raise InvalidGrantError(
                f"grant {grant_id} is {grant.action_scope.value}, not single_action; "
                f"only single_action grants can be consumed (2C §3)"
            )
        if grant.consumed:
            raise GrantAlreadyConsumedError(
                f"grant {grant_id} already consumed (single_action is exactly-once, I8)"
            )
        consumed = grant.model_copy(update={"consumed": True})
        # durable log（2D §3 / §6）：消費記成 durable event（覆寫 grant state）。
        if self._store is not None:
            self._store.append(AuthorityEvent(
                event_type=AuthorityEventType.GRANT_CONSUMED,
                payload={"grant": consumed.model_dump(mode="json")},
            ), DURABLE_WRITER)
        self.__grants[grant_id] = consumed

    # ── resume ──

    def resume(self) -> None:
        """從 durable log fold 出 canonical registry（recovery flow，2D §5）。

        restart → load durable log → fold registry → 後續 authorization 用恢復的
        canonical state。resume() 以 durable truth 覆寫 in-memory registry
        （last-write-wins），不假設 in-process 狀態存活（2D §5）。

        未注入 AuthorityStore 時拋 NoAuthorityStoreError（無 durable truth 可恢復）。
        """
        if self._store is None:
            raise NoAuthorityStoreError(
                "no AuthorityStore injected; cannot resume from durable log"
            )
        self.__approvals, self.__grants = _fold_authority_events(
            self._store.read_events()
        )

    # ── is_authorized ──

    def is_authorized(self, action: AgentAction) -> bool:
        """回傳 privileged action 是否被授權（2C §8 #5 / #6 / #7）。

        只接受 action；caller 傳入的 grant 不再是 authoritative（I3）。
        從 canonical registry 依 `action.grant_id` 取 canonical grant（I4），再依
        `grant.approval_id` 取 canonical approval，逐段驗證 provenance chain：

        1. action → grant：`action.grant_id` 必須 resolve 到已註冊的 grant。
        2. grant → approval：`grant.approval_id` 必須 resolve 到已註冊的 approval。
        3. approval 未撤銷、未過期（2C §4 / §5）。
        4. grant 未過期、single_action 未消耗（2C §3 / §4）。
        5. grant ↔ approval 逐欄比對（capability / grantee_role / work_id /
           action_scope / expires_at，I5）。
        6. grant ↔ action 逐欄比對（capability / role / work_id）。
        7. exact-action：`action.action` 必須 structural equal
           `approval.requested_action`（2C §8 #7：approved scope 不可擴張）。
        8. scope_constraints：`action.action` 必須滿足 `approval.scope_constraints`。

        任何一段斷掉 = authorization failure（回傳 False，禁止推斷 approval，I10）。
        """
        # 1. action → grant：canonical grant 由 action.grant_id 解析（I4）
        grant = self.__grants.get(action.grant_id)
        if grant is None:
            return False
        # 2. grant → approval：canonical approval 由 grant.approval_id 解析
        approval = self.__approvals.get(grant.approval_id)
        if approval is None:
            return False
        # 3. approval 未撤銷、未過期
        if approval.is_revoked():
            return False
        if approval.is_expired():
            return False
        # 4. grant 未過期、single_action 未消耗
        if grant.expires_at is not None and _utcnow() >= grant.expires_at:
            return False
        if grant.action_scope == ActionScope.SINGLE_ACTION and grant.consumed:
            return False
        # 5. grant ↔ approval 逐欄比對（I5：provenance must match exactly）
        if grant.capability != approval.capability:
            return False
        if grant.grantee_role != approval.grantee_role:
            return False
        if grant.work_id != approval.work_id:
            return False
        if grant.action_scope != approval.action_scope:
            return False
        if grant.expires_at != approval.expires_at:
            return False
        # 6. grant ↔ action 逐欄比對（capability / role / work_id）
        if grant.capability != action.capability:
            return False
        if grant.grantee_role != action.role:
            return False
        if grant.work_id != action.work_id:
            return False
        # 7. exact-action：action.action 必須 structural equal requested_action
        #    （2C §8 #7：A valid grant authorizes only the exact bounded action
        #    declared by its governing Approval；capability equality alone 不足）。
        if action.action != approval.requested_action:
            return False
        # 8. scope_constraints：action.action 必須滿足額外約束（2C §2）
        if not _satisfies_scope_constraints(action.action, approval.scope_constraints):
            return False
        return True
