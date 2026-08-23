"""
tests/test_work_authority.py
Soul OS — DSH Multi-Agent MVP-5-R2：Authority Boundary（registry encapsulation + exact-action）。

驗收（對照 logs/DSH-MVP-5-R2-WORK-ORDER.md）：
- capability policy 對齊 2A §5（role → capability 矩陣 + 高風險 capability gate）
- Human authority 由 HumanAuthorityPort 驗證（deny-by-default，非 self-attested 字串）
- is_authorized 只接受 action，從 canonical registry 依 action.grant_id 取 canonical grant
- Approval / CapabilityGrant / AgentAction / HumanAuthorityContext 全部 frozen
- single_action 由 consume(grant_id) 原子消費（exactly once）
- revocation 立即阻止新 action
- 斷鏈 = authorization failure（禁止推斷 approval）
- production.write 必須 time-bounded（expires_at=null = invalid）
- authority.py 不 import 任何 DSH type
- **Registry boundary**：canonical registry 是 manager-controlled storage，隔離於
  public object graph；external caller 無法 inject/replace/mutate registry 使
  is_authorized() == True（adversarial mutation / reflection-style probing）
- **Exact-action authorization**：is_authorized 做 structural comparison，
  action.action vs approval.requested_action（以及 scope_constraints）不匹配 = DENY

執行：pytest tests/test_work_authority.py
"""
import inspect
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from pydantic import ValidationError

from src.work import authority as authority_mod
from src.work.authority import (
    HIGH_RISK_CAPABILITIES,
    ActionScope,
    AgentAction,
    Approval,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalRevokedError,
    AuthorityManager,
    CapabilityGrant,
    CapabilityPolicy,
    GrantAlreadyConsumedError,
    GrantNotFoundError,
    HmacHumanAuthorityPort,
    HumanAuthorityContext,
    HumanAuthorityExpiredError,
    InvalidApprovalError,
    InvalidGrantError,
    NotHumanGrantorError,
    issue_hmac_context,
    sign_authority_token,
)
from src.work.roles import ROLE_CAPABILITIES, Role


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)

# 預設 requested_action：與 _approval 預設一致，供 _action 建 matching action。
_DEFAULT_REQUESTED_ACTION = {"repository": "soul-os-harness", "branch": "main"}


def _source_of(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def _future(**delta_kwargs) -> datetime:
    """回傳未來時間（預設 +1 小時）。"""
    return datetime.now(timezone.utc) + timedelta(**delta_kwargs)


def _approval(**overrides) -> Approval:
    """建一個合法的 Human Approval（可覆寫欄位）。granted_by 是資料，非 proof。"""
    base: dict = {
        "work_id": "work-1",
        "capability": "git.commit",
        "requested_action": dict(_DEFAULT_REQUESTED_ACTION),
        "action_scope": ActionScope.SINGLE_ACTION,
        "grantee_role": "developer",
        "granted_by": "human",
        "expires_at": _future(hours=1),
    }
    base.update(overrides)
    return Approval(**base)


def _context(**overrides) -> HumanAuthorityContext:
    """建一個合法的 HumanAuthorityContext（可覆寫欄位）。"""
    base: dict = {
        "identity": "bryan",
        "authority_token": "trusted-token",
        "issued_at": datetime.now(timezone.utc),
        "expires_at": _future(hours=1),
    }
    base.update(overrides)
    return HumanAuthorityContext(**base)


def _action(grant: CapabilityGrant, **overrides) -> AgentAction:
    """建一個與 grant 匹配的 privileged action（可覆寫欄位）。

    預設 action 與 _approval 的預設 requested_action 一致，使 happy path 成立。
    """
    base: dict = {
        "grant_id": grant.grant_id,
        "work_id": grant.work_id,
        "role": grant.grantee_role,
        "capability": grant.capability,
        "action": dict(_DEFAULT_REQUESTED_ACTION),
    }
    base.update(overrides)
    return AgentAction(**base)


def _forged_chain():
    """建一組「內部一致」的偽造 approval+grant+action（未經 grant() 註冊）。

    若 registry 可被注入，這組偽造對會讓 is_authorized() 回傳 True；
    本測試用它驗證 registry boundary 擋下 injection / replacement。
    """
    approval = _approval(
        work_id="forged-work",
        capability="production.write",
        expires_at=_future(hours=1),
    )
    grant = CapabilityGrant(
        approval_id=approval.approval_id,
        work_id=approval.work_id,
        capability=approval.capability,
        grantee_role=approval.grantee_role,
        action_scope=approval.action_scope,
        expires_at=approval.expires_at,
    )
    action = _action(grant, action=dict(approval.requested_action))
    return approval, grant, action


class _TrustedPort:
    """Fake HumanAuthorityPort：只認可 authority_token == "trusted-token" 的 context。"""

    def __init__(self, valid_token: str = "trusted-token"):
        self._valid_token = valid_token

    def authenticate(self, context) -> bool:
        if not isinstance(context, HumanAuthorityContext):
            return False
        return context.authority_token == self._valid_token


class _RejectingPort:
    """Fake HumanAuthorityPort：一律拒絕。"""

    def authenticate(self, context) -> bool:
        return False


def _manager(port=None) -> AuthorityManager:
    """建 AuthorityManager；port=None 時注入 trusted fake port。"""
    return AuthorityManager(
        human_authority=port if port is not None else _TrustedPort()
    )


# ─────────────────────────────────────────────
# 1. Capability policy（2A §5）
# ─────────────────────────────────────────────

def test_capability_policy_aligns_with_2a_s5():
    """CapabilityPolicy.authorize 對齊 2A §5.1 矩陣（唯一 authoritative source）。"""
    policy = CapabilityPolicy()
    for role, caps in ROLE_CAPABILITIES.items():
        for cap in caps:
            assert policy.authorize(role, cap), f"{role} 應具備 {cap}"
        # 不具備的 capability 回傳 False
        for cap in HIGH_RISK_CAPABILITIES:
            assert not policy.authorize(role, cap), f"{role} 不應具備高風險 {cap}"


def test_high_risk_capabilities_are_2a_s5_2():
    """高風險 capability 集合對齊 2A §5.2。"""
    assert HIGH_RISK_CAPABILITIES == {
        "production.write", "git.commit", "git.push", "deploy", "external.publish",
    }


def test_high_risk_capabilities_require_approval_gate():
    """高風險 capability 需 approval gate；低風險不需。"""
    policy = CapabilityPolicy()
    for cap in HIGH_RISK_CAPABILITIES:
        assert policy.requires_approval(cap), f"{cap} 應需 approval gate"
    for cap in ("workspace.read", "isolated.write", "test.execute", "work.assign"):
        assert not policy.requires_approval(cap), f"{cap} 不應需 approval gate"


def test_authorize_accepts_role_enum_and_str():
    """authorize 接受 Role enum 與 role string。"""
    policy = CapabilityPolicy()
    assert policy.authorize(Role.DEVELOPER, "isolated.write")
    assert policy.authorize("developer", "isolated.write")
    assert not policy.authorize(Role.DEVELOPER, "approval")


# ─────────────────────────────────────────────
# 2. Human authority via port（forgery #1：Human forgery）
# ─────────────────────────────────────────────

def test_grant_denies_without_port():
    """無注入 HumanAuthorityPort → deny（deny-by-default）。"""
    mgr = AuthorityManager()  # 無 port
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(), _context())


def test_grant_denies_when_port_rejects():
    """HumanAuthorityPort.authenticate 回傳 False → NotHumanGrantorError。"""
    mgr = _manager(_RejectingPort())
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(), _context())


def test_grant_rejects_self_attested_string():
    """self-attested "human" 字串不再有效：context 必須是 HumanAuthorityContext。"""
    mgr = _manager()
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(), "human")  # 舊的 self-attested 字串


def test_grant_rejects_wrong_token():
    """authority_token 不符 → authenticate 回傳 False → deny。"""
    mgr = _manager()
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(), _context(authority_token="forged-token"))


def test_grant_accepts_authenticated_context():
    """valid context + port 驗證通過 → grant 成功。"""
    mgr = _manager()
    approval = _approval()
    grant = mgr.grant(approval, _context())
    assert isinstance(grant, CapabilityGrant)
    assert grant.approval_id == approval.approval_id


def test_granted_by_is_data_not_proof():
    """granted_by 是資料不是 proof：authentication 由 port 決定，不 gate 於字串。"""
    mgr = _manager()
    # granted_by 非 "human" 也不影響：port 驗證通過即發 grant（granted_by 只是資料）
    approval = _approval(granted_by="developer")
    grant = mgr.grant(approval, _context())
    assert isinstance(grant, CapabilityGrant)


# ─────────────────────────────────────────────
# 3. grant 一對一 provenance（2C §6）
# ─────────────────────────────────────────────

def test_grant_links_to_approval_one_to_one():
    """grant.approval_id == approval.approval_id（grant → approval 一對一）。"""
    mgr = _manager()
    approval = _approval()
    grant = mgr.grant(approval, _context())
    assert grant.approval_id == approval.approval_id
    assert grant.grant_id != approval.approval_id  # grant 有自己的 id


def test_grant_ids_are_unique():
    """兩個不同 approval 產生不同 grant_id。"""
    mgr = _manager()
    g1 = mgr.grant(_approval(work_id="work-1"), _context())
    g2 = mgr.grant(_approval(work_id="work-2"), _context())
    assert g1.grant_id != g2.grant_id


def test_same_approval_cannot_be_granted_twice():
    """同一 approval 只能發一次 grant（一對一 provenance）。"""
    mgr = _manager()
    approval = _approval()
    mgr.grant(approval, _context())
    with pytest.raises(InvalidApprovalError):
        mgr.grant(approval, _context())


def test_action_links_to_grant_one_to_one():
    """action.grant_id == grant.grant_id（action → grant 一對一）。"""
    mgr = _manager()
    grant = mgr.grant(_approval(), _context())
    action = _action(grant)
    assert action.grant_id == grant.grant_id


# ─────────────────────────────────────────────
# 4. is_authorized（2C §8 #5 / #6）— canonical grant resolution
# ─────────────────────────────────────────────

def test_is_authorized_signature_accepts_only_action():
    """is_authorized 只接受 action（caller 傳入的 grant 不再是 authoritative，I3）。"""
    sig = inspect.signature(AuthorityManager.is_authorized)
    assert list(sig.parameters) == ["self", "action"]


def test_is_authorized_happy_path():
    """valid grant + 匹配 action → authorized。"""
    mgr = _manager()
    grant = mgr.grant(_approval(), _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True


def test_is_authorized_denies_unknown_grant_id():
    """action.grant_id 無法 resolve 到 canonical grant（斷鏈）→ deny（I4）。"""
    mgr = _manager()
    grant = mgr.grant(_approval(), _context())
    action = _action(grant, grant_id="forged-grant-id")
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_unresolvable_approval():
    """grant.approval_id 無法 resolve（斷鏈）→ deny。"""
    mgr = _manager()
    # 直接構造一個 approval_id 指向不存在的 grant（未經 grant() 註冊）
    grant = CapabilityGrant(
        approval_id="no-such-approval",
        work_id="work-1",
        capability="git.commit",
        grantee_role="developer",
        action_scope=ActionScope.SINGLE_ACTION,
    )
    action = _action(grant)
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_grant_approval_mismatch():
    """grant ↔ approval 逐欄比對（I5）：canonical grant 與 approval 不符 → deny。

    defense-in-depth：即使 registry boundary 被 name-mangled escape hatch 繞過，
    is_authorized 的逐欄比對仍會 deny 不一致的 grant↔approval 對。
    """
    mgr = _manager()
    grant = mgr.grant(_approval(expires_at=_future(hours=1)), _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True
    # 模擬 registry 被竄改（經 name-mangled escape hatch）：grant 的 expires_at 與 approval 不符
    forged = grant.model_copy(update={"expires_at": _future(hours=2)})
    mgr._AuthorityManager__grants[grant.grant_id] = forged
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_capability_mismatch():
    """grant.capability 與 action.capability 不符 → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(capability="git.commit"), _context())
    action = _action(grant, capability="git.push")
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_role_mismatch():
    """grant.grantee_role 與 action.role 不符 → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(grantee_role="developer"), _context())
    action = _action(grant, role="tester")
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_work_id_mismatch():
    """grant.work_id 與 action.work_id 不符 → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(work_id="work-1"), _context())
    action = _action(grant, work_id="work-2")
    assert mgr.is_authorized(action) is False


# ─────────────────────────────────────────────
# 5. Mutable objects（forgery #3：frozen）
# ─────────────────────────────────────────────

def test_approval_is_frozen():
    """Approval frozen：caller 不能直接 mutate（I6）。"""
    approval = _approval()
    with pytest.raises(ValidationError):
        approval.revoked_at = datetime.now(timezone.utc)


def test_capability_grant_is_frozen():
    """CapabilityGrant frozen：caller 不能手動設 consumed（I6）。"""
    grant = CapabilityGrant(
        approval_id="a-1",
        work_id="work-1",
        capability="git.commit",
        grantee_role="developer",
        action_scope=ActionScope.SINGLE_ACTION,
    )
    with pytest.raises(ValidationError):
        grant.consumed = True


def test_agent_action_is_frozen():
    """AgentAction frozen：caller 不能 mutate authority state（I6）。"""
    grant = CapabilityGrant(
        approval_id="a-1",
        work_id="work-1",
        capability="git.commit",
        grantee_role="developer",
        action_scope=ActionScope.SINGLE_ACTION,
    )
    action = _action(grant)
    with pytest.raises(ValidationError):
        action.capability = "git.push"


def test_human_authority_context_is_frozen():
    """HumanAuthorityContext frozen：caller 不能 mutate authority_token（I6）。"""
    context = _context()
    with pytest.raises(ValidationError):
        context.authority_token = "forged-token"


# ─────────────────────────────────────────────
# 6. single_action consumption（forgery #4：consume）
# ─────────────────────────────────────────────

def test_consume_single_action_once():
    """consume(grant_id) 原子消費 single_action grant（I8）。"""
    mgr = _manager()
    grant = mgr.grant(_approval(action_scope=ActionScope.SINGLE_ACTION), _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True

    mgr.consume(grant.grant_id)
    assert mgr.is_authorized(action) is False


def test_consume_twice_raises():
    """single_action grant 只能消費一次（I8：exactly once）。"""
    mgr = _manager()
    grant = mgr.grant(_approval(action_scope=ActionScope.SINGLE_ACTION), _context())
    mgr.consume(grant.grant_id)
    with pytest.raises(GrantAlreadyConsumedError):
        mgr.consume(grant.grant_id)


def test_consume_unknown_grant_raises():
    """consume 找不到 grant_id → GrantNotFoundError。"""
    mgr = _manager()
    with pytest.raises(GrantNotFoundError):
        mgr.consume("no-such-grant")


def test_consume_work_scoped_raises():
    """work_scoped grant 不支援 single_action consumption → InvalidGrantError。"""
    mgr = _manager()
    grant = mgr.grant(_approval(action_scope=ActionScope.WORK_SCOPED), _context())
    with pytest.raises(InvalidGrantError):
        mgr.consume(grant.grant_id)


def test_work_scoped_grant_not_consumed():
    """work_scoped grant 不因 single_action consumption 而失效。"""
    mgr = _manager()
    grant = mgr.grant(_approval(action_scope=ActionScope.WORK_SCOPED), _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True


# ─────────────────────────────────────────────
# 7. Revocation（2C §5）
# ─────────────────────────────────────────────

def test_revoke_immediately_blocks_new_action():
    """revoke 後，原本 valid 的 grant 立即 deny（阻止新 action，I9）。"""
    mgr = _manager()
    approval = _approval()
    grant = mgr.grant(approval, _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True

    mgr.revoke(approval.approval_id)
    assert mgr.is_authorized(action) is False


def test_revoke_unknown_approval_raises():
    """revoke 找不到 approval_id → ApprovalNotFoundError。"""
    mgr = _manager()
    with pytest.raises(ApprovalNotFoundError):
        mgr.revoke("no-such-approval")


def test_grant_rejects_revoked_approval():
    """已撤銷的 approval（revoked_at 已設）不能發 grant。"""
    mgr = _manager()
    approval = _approval(revoked_at=datetime.now(timezone.utc))
    with pytest.raises(ApprovalRevokedError):
        mgr.grant(approval, _context())


def test_revoke_replaces_approval_with_revoked_state():
    """revoke 是 controlled operation：registry 中的 approval 帶 revoked_at（I6）。

    行為驗證：revoke 後 is_authorized 立即 deny（canonical approval 已帶 revoked_at）。
    """
    mgr = _manager()
    approval = _approval()
    grant = mgr.grant(approval, _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True

    mgr.revoke(approval.approval_id)
    # canonical approval 已帶 revoked_at → is_authorized deny
    assert mgr.is_authorized(action) is False


# ─────────────────────────────────────────────
# 8. production.write 必須 time-bounded（2C §4）
# ─────────────────────────────────────────────

def test_production_write_requires_expiry():
    """production.write 的 expires_at=null → InvalidApprovalError（I7）。"""
    mgr = _manager()
    approval = _approval(capability="production.write", expires_at=None)
    with pytest.raises(InvalidApprovalError):
        mgr.grant(approval, _context())


def test_production_write_with_expiry_is_valid():
    """production.write 帶 expires_at → grant 成功。"""
    mgr = _manager()
    approval = _approval(capability="production.write", expires_at=_future(minutes=30))
    grant = mgr.grant(approval, _context())
    assert grant.capability == "production.write"
    assert grant.expires_at is not None


def test_other_high_risk_capability_may_be_scope_bounded():
    """非 production.write 的高風險 capability 可無 expiry（以 scope 界定）。"""
    mgr = _manager()
    approval = _approval(capability="git.commit", expires_at=None,
                         action_scope=ActionScope.WORK_SCOPED)
    grant = mgr.grant(approval, _context())
    assert grant.expires_at is None


# ─────────────────────────────────────────────
# 9. Expiry（2C §3 / §4）
# ─────────────────────────────────────────────

def test_grant_rejects_expired_approval():
    """已過期的 approval 不能發 grant。"""
    mgr = _manager()
    approval = _approval(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(ApprovalExpiredError):
        mgr.grant(approval, _context())


def test_is_authorized_denies_expired_grant(monkeypatch):
    """canonical grant 過期 → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(expires_at=_future(hours=1)), _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True

    # 模擬時間流逝：_utcnow 前進到 grant 過期之後
    monkeypatch.setattr(authority_mod, "_utcnow", lambda: _future(hours=2))
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_expired_approval(monkeypatch):
    """canonical approval 過期 → deny。"""
    mgr = _manager()
    approval = _approval(expires_at=_future(hours=1))
    grant = mgr.grant(approval, _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True

    # 模擬時間流逝：_utcnow 前進到 approval 過期之後
    monkeypatch.setattr(authority_mod, "_utcnow", lambda: _future(hours=2))
    assert mgr.is_authorized(action) is False


# ─────────────────────────────────────────────
# 10. 零 DSH coupling
# ─────────────────────────────────────────────

def test_authority_does_not_import_dsh():
    """authority.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(authority_mod)), (
        "authority.py 不得 import DSH / Cordis type"
    )


def test_serialized_objects_contain_no_dsh_strings():
    """Approval / CapabilityGrant / AgentAction / HumanAuthorityContext 序列化後不含 DSH type/id 字串。"""
    approval = _approval()
    grant = CapabilityGrant(
        approval_id=approval.approval_id,
        work_id="work-1",
        capability="git.commit",
        grantee_role="developer",
        action_scope=ActionScope.SINGLE_ACTION,
    )
    action = _action(grant)
    context = _context()
    for obj in (approval, grant, action, context):
        assert "dsh" not in obj.model_dump_json().lower()


# ─────────────────────────────────────────────
# 11. Registry boundary（Forgery A：injection / replacement）
# ─────────────────────────────────────────────

def test_registry_not_exposed_as_public_attribute():
    """canonical registry 不暴露為 public attribute（_grants/_approvals/__dict__ 不存在）。"""
    mgr = _manager()
    assert not hasattr(mgr, "_grants")
    assert not hasattr(mgr, "_approvals")
    assert not hasattr(mgr, "__dict__")


def test_registry_injection_attack_denied():
    """attack #1：caller 無法經 public attribute 注入偽造 approval+grant 對。"""
    mgr = _manager()
    approval, grant, action = _forged_chain()
    # 注入攻擊：public attribute 不存在（name-mangled slot，無 __dict__）
    with pytest.raises(AttributeError):
        mgr._grants[grant.grant_id] = grant
    with pytest.raises(AttributeError):
        mgr._approvals[approval.approval_id] = approval
    assert mgr.is_authorized(action) is False


def test_registry_replacement_attack_denied():
    """attack #2：caller 無法替換整個 canonical registry。"""
    mgr = _manager()
    approval, grant, action = _forged_chain()
    with pytest.raises(AttributeError):
        mgr._grants = {grant.grant_id: grant}
    with pytest.raises(AttributeError):
        mgr._approvals = {approval.approval_id: approval}
    assert mgr.is_authorized(action) is False


def test_registry_reflection_probing_denied():
    """reflection-style probing：vars()/__dict__/setattr 無法 mutate canonical registry。"""
    mgr = _manager()
    approval, grant, action = _forged_chain()
    # 無 __dict__：不能經 __dict__ 注入
    assert not hasattr(mgr, "__dict__")
    # vars() 需要 __dict__：slotted class 直接 TypeError（無法經 vars() 探測）
    with pytest.raises(TypeError):
        vars(mgr)
    # setattr 無法建立新 attribute（無 __dict__、無 _grants slot）
    with pytest.raises(AttributeError):
        setattr(mgr, "_grants", {grant.grant_id: grant})
    assert mgr.is_authorized(action) is False


# ─────────────────────────────────────────────
# 12. Exact-action authorization（Forgery B：requested_action / scope_constraints）
# ─────────────────────────────────────────────

def test_is_authorized_accepts_exact_action():
    """exact-action：action.action == requested_action → authorized。"""
    mgr = _manager()
    approval = _approval()
    grant = mgr.grant(approval, _context())
    action = _action(grant, action=dict(approval.requested_action))
    assert mgr.is_authorized(action) is True


def test_is_authorized_denies_altered_requested_action():
    """attack #6：合法 grant + 換 action 目標 → deny（exact-action）。"""
    mgr = _manager()
    grant = mgr.grant(_approval(), _context())
    # 合法 grant，但 action 換成未批准的目標
    action = _action(grant, action={"repository": "other-repo", "branch": "production"})
    assert mgr.is_authorized(action) is False


def test_is_authorized_denies_scope_constraint_violation():
    """scope_constraints 進入 decision：action 缺約束 key → deny。"""
    mgr = _manager()
    approval = _approval(
        requested_action={"repository": "soul-os-harness"},
        scope_constraints={"branch": "main"},
    )
    grant = mgr.grant(approval, _context())
    # action 與 requested_action 完全一致，但缺 scope_constraints 要求的 branch
    action = _action(grant, action={"repository": "soul-os-harness"})
    assert mgr.is_authorized(action) is False


def test_is_authorized_accepts_scope_constraints_satisfied():
    """scope_constraints 滿足（且 requested_action 匹配）→ authorized。"""
    mgr = _manager()
    approval = _approval(
        requested_action={"repository": "soul-os-harness", "branch": "main"},
        scope_constraints={"branch": "main"},
    )
    grant = mgr.grant(approval, _context())
    action = _action(grant, action={"repository": "soul-os-harness", "branch": "main"})
    assert mgr.is_authorized(action) is True


# ─────────────────────────────────────────────
# 13. R2 Final Review 13 條 attack list（全部 deny）
# ─────────────────────────────────────────────

def test_attack_03_forged_approval_denied():
    """attack #3：caller 偽造 Approval（未經 grant() 註冊）→ deny。"""
    mgr = _manager()
    approval, grant, action = _forged_chain()
    # grant 未經 grant() 註冊 → action.grant_id 無法 resolve → deny
    assert mgr.is_authorized(action) is False


def test_attack_04_forged_grant_denied():
    """attack #4：caller 偽造 Grant（未經 grant() 註冊）→ deny。"""
    mgr = _manager()
    grant = CapabilityGrant(
        approval_id="forged-approval",
        work_id="work-1",
        capability="git.commit",
        grantee_role="developer",
        action_scope=ActionScope.SINGLE_ACTION,
    )
    action = _action(grant)
    assert mgr.is_authorized(action) is False


def test_attack_05_forged_agent_action_denied():
    """attack #5：caller 偽造 AgentAction（grant_id 指向不存在）→ deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(), _context())
    action = _action(grant, grant_id="forged-grant-id")
    assert mgr.is_authorized(action) is False


def test_attack_07_altered_work_id_denied():
    """attack #7：合法 grant + 換 work_id → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(work_id="work-1"), _context())
    action = _action(grant, work_id="work-2")
    assert mgr.is_authorized(action) is False


def test_attack_08_altered_role_denied():
    """attack #8：合法 grant + 換 role → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(grantee_role="developer"), _context())
    action = _action(grant, role="tester")
    assert mgr.is_authorized(action) is False


def test_attack_09_altered_capability_denied():
    """attack #9：合法 grant + 換 capability → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(capability="git.commit"), _context())
    action = _action(grant, capability="git.push")
    assert mgr.is_authorized(action) is False


def test_attack_10_revoked_grant_replay_denied():
    """attack #10：revoked grant replay → deny。"""
    mgr = _manager()
    approval = _approval()
    grant = mgr.grant(approval, _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True
    mgr.revoke(approval.approval_id)
    assert mgr.is_authorized(action) is False


def test_attack_11_consumed_single_action_replay_denied():
    """attack #11：consumed single-action replay → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(action_scope=ActionScope.SINGLE_ACTION), _context())
    action = _action(grant)
    assert mgr.is_authorized(action) is True
    mgr.consume(grant.grant_id)
    assert mgr.is_authorized(action) is False


def test_attack_12_expired_production_write_denied(monkeypatch):
    """attack #12：expired production.write replay → deny。"""
    mgr = _manager()
    approval = _approval(capability="production.write", expires_at=_future(minutes=30))
    grant = mgr.grant(approval, _context())
    action = _action(grant, action=dict(approval.requested_action))
    assert mgr.is_authorized(action) is True
    monkeypatch.setattr(authority_mod, "_utcnow", lambda: _future(hours=1))
    assert mgr.is_authorized(action) is False


def test_attack_13_cross_work_grant_reuse_denied():
    """attack #13：grant 用於 work-1，action 聲稱 work-2 → deny。"""
    mgr = _manager()
    grant = mgr.grant(_approval(work_id="work-1"), _context())
    action = _action(grant, work_id="work-2")
    assert mgr.is_authorized(action) is False


# ─────────────────────────────────────────────
# 14. HMAC trust establishment（R1：authority_token 是 HMAC-signed proof）
# ─────────────────────────────────────────────

def test_hmac_token_is_not_plain_string():
    """authority_token 是 HMAC-SHA256 hexdigest，不是普通字串（trust establishment）。"""
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    assert len(ctx.authority_token) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", ctx.authority_token)
    assert ctx.authority_token != "trusted-token"
    assert ctx.nonce  # nonce 是簽署 claims 的一部分（replay 防護）


def test_hmac_token_is_deterministic_for_same_claims():
    """同 secret + 同 claims → 同 token（簽署演算法 deterministic）。"""
    issued = datetime.now(timezone.utc)
    expires = _future(hours=1)  # 只呼叫一次，t1/t2/t3 共用同一個 expires_at
    t1 = sign_authority_token(
        "s", identity="bryan", issued_at=issued,
        expires_at=expires, nonce="n1",
    )
    t2 = sign_authority_token(
        "s", identity="bryan", issued_at=issued,
        expires_at=expires, nonce="n1",
    )
    assert t1 == t2
    # 不同 secret → 不同 token
    t3 = sign_authority_token(
        "other", identity="bryan", issued_at=issued,
        expires_at=expires, nonce="n1",
    )
    assert t1 != t3


def test_hmac_port_accepts_valid_token():
    """HmacHumanAuthorityPort 驗證通過 valid HMAC context。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    assert port.authenticate(ctx) is True


def test_hmac_port_denies_forged_token():
    """forgery：竄改 token → signature 不符 → deny。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    forged = ctx.model_copy(update={"authority_token": "0" * 64})
    assert port.authenticate(forged) is False


def test_hmac_port_denies_wrong_secret():
    """forgery：token 用別的 secret 簽 → deny。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("other-secret", identity="bryan", expires_at=_future(hours=1))
    assert port.authenticate(ctx) is False


def test_hmac_port_denies_tampered_identity():
    """forgery：竄改 identity（claims 與 signature 不符）→ deny。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    tampered = ctx.model_copy(update={"identity": "mallory"})
    assert port.authenticate(tampered) is False


def test_hmac_port_denies_tampered_expiry():
    """forgery：竄改 expires_at（延長效期）→ claims 與 signature 不符 → deny。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    tampered = ctx.model_copy(update={"expires_at": _future(hours=24)})
    assert port.authenticate(tampered) is False


def test_hmac_port_denies_expired_context(monkeypatch):
    """expiry：context 過期 → deny（issued_at 在過去、expires_at 已過）。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    assert port.authenticate(ctx) is True
    monkeypatch.setattr(authority_mod, "_utcnow", lambda: _future(hours=2))
    assert port.authenticate(ctx) is False  # 時間流逝後過期（但 nonce 已用，先驗 expiry 路徑）


def test_hmac_port_denies_future_issued_at():
    """issued_at 在未來：token 尚未生效 → deny。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context(
        "test-secret", identity="bryan", expires_at=_future(hours=2),
        issued_at=_future(hours=1),
    )
    assert port.authenticate(ctx) is False


def test_hmac_port_rejects_replay():
    """replay：同一 context（同 nonce）第二次 → deny（nonce registry）。"""
    port = HmacHumanAuthorityPort("test-secret")
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    assert port.authenticate(ctx) is True
    assert port.authenticate(ctx) is False  # 同 token / 同 nonce 重放


def test_hmac_port_accepts_fresh_nonce_same_content():
    """不同 nonce（新 token）同內容 → 接受（nonce 是 replay 防護的錨點）。"""
    port = HmacHumanAuthorityPort("test-secret")
    c1 = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    c2 = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    assert c1.nonce != c2.nonce
    assert port.authenticate(c1) is True
    assert port.authenticate(c2) is True


def test_grant_with_hmac_port_happy_path():
    """HmacHumanAuthorityPort + HMAC context → grant 成功（真實 trust establishment）。"""
    mgr = AuthorityManager(human_authority=HmacHumanAuthorityPort("test-secret"))
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    grant = mgr.grant(_approval(), ctx)
    assert isinstance(grant, CapabilityGrant)


def test_grant_rejects_replayed_hmac_context():
    """同一 HMAC context 重放 → 第二次 deny（nonce replay 防護貫穿 grant）。"""
    mgr = AuthorityManager(human_authority=HmacHumanAuthorityPort("test-secret"))
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    assert isinstance(mgr.grant(_approval(work_id="work-1"), ctx), CapabilityGrant)
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(work_id="work-2"), ctx)  # 同 nonce 重放


def test_grant_rejects_forged_hmac_token():
    """forged HMAC token 進 grant → NotHumanGrantorError。"""
    mgr = AuthorityManager(human_authority=HmacHumanAuthorityPort("test-secret"))
    ctx = issue_hmac_context("test-secret", identity="bryan", expires_at=_future(hours=1))
    forged = ctx.model_copy(update={"authority_token": "0" * 64})
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(), forged)


# ─────────────────────────────────────────────
# 15. grant() 強制檢查 context.expires_at（R1：manager-level expiry enforcement）
# ─────────────────────────────────────────────

def test_grant_rejects_expired_context_deny_by_default():
    """grant() 強制檢查 context.expires_at：過期 context → deny。

    即使注入的 port 不檢查 expiry（fake trusted port 只比 token），
    grant boundary 仍拒絕過期 context（Domain Core 強制，非 Adapter）。
    """
    mgr = _manager()  # fake trusted port（不檢查 expiry）
    ctx = _context(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(HumanAuthorityExpiredError):
        mgr.grant(_approval(), ctx)


def test_grant_accepts_unexpired_context():
    """context 未過期（port 驗證通過）→ grant 成功。"""
    mgr = _manager()
    grant = mgr.grant(_approval(), _context())
    assert isinstance(grant, CapabilityGrant)


def test_grant_rejects_expired_context_even_with_hmac_port():
    """HMAC port 下過期 context：authenticate 先 deny（HMAC expiry 檢查）。"""
    mgr = AuthorityManager(human_authority=HmacHumanAuthorityPort("test-secret"))
    ctx = issue_hmac_context(
        "test-secret", identity="bryan", expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with pytest.raises(NotHumanGrantorError):
        mgr.grant(_approval(), ctx)
