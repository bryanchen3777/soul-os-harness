"""
tests/test_work_persistence.py
Soul OS — DSH Multi-Agent MVP-6：Recovery / Resume（durable approval/grant registry）。

驗收（對照 logs/DSH-MVP-6-WORK-ORDER.md）：
- approval/grant registry 是 durable（append-only log，restart 後可 resume）
- AuthorityManager.resume() 從 durable log fold 出 canonical registry
- resume 後 authorization 用恢復的 canonical state（revoke / consume 跨 restart 生效）
- 文件化 mangled-name 雙注入測試（known/accepted limitation）
- persistence.py 不 import 任何 DSH type

執行：pytest tests/test_work_persistence.py
"""
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work import authority as authority_mod
from src.work import persistence as persistence_mod
from src.work.authority import (
    ActionScope,
    AgentAction,
    Approval,
    AuthorityManager,
    CapabilityGrant,
    HumanAuthorityContext,
    InvalidApprovalError,
)
from src.work.bridge import DURABLE_WRITER
from src.work.persistence import (
    AuthorityEvent,
    AuthorityEventType,
    AuthorityStore,
    NoAuthorityStoreError,
    NotDurableWriterError,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)

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
    """建一個與 grant 匹配的 privileged action（可覆寫欄位）。"""
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
    用於文件化 mangled-name 雙注入的 known/accepted limitation。
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


def _manager(port=None, store=None) -> AuthorityManager:
    """建 AuthorityManager；port=None 時注入 trusted fake port。"""
    return AuthorityManager(
        human_authority=port if port is not None else _TrustedPort(),
        store=store,
    )


# ─────────────────────────────────────────────
# 1. AuthorityStore（append-only JSONL）
# ─────────────────────────────────────────────

def test_authority_store_append_and_read(tmp_path):
    """AuthorityStore append → read_events 回傳（按 append 順序）。"""
    store = AuthorityStore(data_dir=tmp_path)
    store.append(AuthorityEvent(
        event_type=AuthorityEventType.APPROVAL_GRANTED,
        payload={"approval": {"approval_id": "a-1"}},
    ), DURABLE_WRITER)
    store.append(AuthorityEvent(
        event_type=AuthorityEventType.GRANT_ISSUED,
        payload={"grant": {"grant_id": "g-1"}},
    ), DURABLE_WRITER)

    events = store.read_events()
    assert len(events) == 2
    assert events[0].event_type == AuthorityEventType.APPROVAL_GRANTED
    assert events[0].payload["approval"]["approval_id"] == "a-1"
    assert events[1].event_type == AuthorityEventType.GRANT_ISSUED
    assert events[1].payload["grant"]["grant_id"] == "g-1"


def test_authority_store_is_append_only(tmp_path):
    """AuthorityStore 只有 append / read_events，無 update / delete API。"""
    store = AuthorityStore(data_dir=tmp_path)
    for forbidden in ("update", "delete", "remove", "modify", "overwrite", "replace"):
        assert not hasattr(store, forbidden), f"store 不應有 {forbidden} API"


def test_authority_store_corrupt_row_skipped(tmp_path):
    """corrupt row（壞 JSON）跳過，不影響 read_events。"""
    store = AuthorityStore(data_dir=tmp_path)
    store.append(AuthorityEvent(
        event_type=AuthorityEventType.APPROVAL_GRANTED,
        payload={"approval": {"approval_id": "a-1"}},
    ), DURABLE_WRITER)
    with open(store.store_file, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
    store.append(AuthorityEvent(
        event_type=AuthorityEventType.GRANT_ISSUED,
        payload={"grant": {"grant_id": "g-1"}},
    ), DURABLE_WRITER)

    events = store.read_events()
    assert len(events) == 2  # corrupt row 被跳過


# ─────────────────────────────────────────────
# 2. Durable persistence（grant 寫入 durable log）
# ─────────────────────────────────────────────

def test_grant_writes_durable_events_to_disk(tmp_path):
    """grant() 把 approval_granted + grant_issued 寫入 durable log（磁碟）。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr = _manager(store=store)
    mgr.grant(_approval(), _context())

    assert store.store_file.exists()
    types = [e.event_type for e in store.read_events()]
    assert AuthorityEventType.APPROVAL_GRANTED in types
    assert AuthorityEventType.GRANT_ISSUED in types


def test_revoke_writes_durable_event(tmp_path):
    """revoke() 把 approval_revoked 寫入 durable log。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr = _manager(store=store)
    approval = _approval()
    mgr.grant(approval, _context())
    mgr.revoke(approval.approval_id)

    types = [e.event_type for e in store.read_events()]
    assert AuthorityEventType.APPROVAL_REVOKED in types


def test_consume_writes_durable_event(tmp_path):
    """consume() 把 grant_consumed 寫入 durable log。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr = _manager(store=store)
    grant = mgr.grant(_approval(action_scope=ActionScope.SINGLE_ACTION), _context())
    mgr.consume(grant.grant_id)

    types = [e.event_type for e in store.read_events()]
    assert AuthorityEventType.GRANT_CONSUMED in types


# ─────────────────────────────────────────────
# 3. Resume（fold durable log → canonical registry）
# ─────────────────────────────────────────────

def test_resume_recovers_granted_registry(tmp_path):
    """restart 後 resume() 從 durable log 恢復 grant，authorization 用恢復的 state。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    grant = mgr1.grant(approval, _context())
    action = _action(grant)
    assert mgr1.is_authorized(action) is True

    # restart：新 manager，同 store，resume
    mgr2 = _manager(store=store)
    mgr2.resume()
    assert mgr2.is_authorized(action) is True


def test_resume_recovers_revocation_across_restart(tmp_path):
    """revoke 跨 restart 生效：resume 後 authorization 用恢復的 revoked state。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    grant = mgr1.grant(approval, _context())
    action = _action(grant)
    mgr1.revoke(approval.approval_id)
    assert mgr1.is_authorized(action) is False

    mgr2 = _manager(store=store)
    mgr2.resume()
    assert mgr2.is_authorized(action) is False


def test_resume_recovers_consumption_across_restart(tmp_path):
    """consume 跨 restart 生效：resume 後 single_action grant 已消耗 → deny。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    grant = mgr1.grant(_approval(action_scope=ActionScope.SINGLE_ACTION), _context())
    action = _action(grant)
    mgr1.consume(grant.grant_id)
    assert mgr1.is_authorized(action) is False

    mgr2 = _manager(store=store)
    mgr2.resume()
    assert mgr2.is_authorized(action) is False


def test_resume_preserves_one_to_one_provenance(tmp_path):
    """resume 後，同一 approval 不能再次 grant（一對一 provenance 跨 restart）。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    mgr1.grant(approval, _context())

    mgr2 = _manager(store=store)
    mgr2.resume()
    with pytest.raises(InvalidApprovalError):
        mgr2.grant(approval, _context())


def test_resume_replaces_in_memory_state(tmp_path):
    """resume 以 durable truth 覆寫 in-memory registry（不假設 in-process 存活）。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    grant = mgr1.grant(_approval(), _context())
    action = _action(grant)

    # 新 manager 未 resume 前，registry 是空的（in-memory 不存活）
    mgr2 = _manager(store=store)
    assert mgr2.is_authorized(action) is False
    mgr2.resume()
    assert mgr2.is_authorized(action) is True


def test_resume_without_store_raises():
    """未注入 AuthorityStore → resume() 拋 NoAuthorityStoreError。"""
    mgr = _manager()  # 無 store
    with pytest.raises(NoAuthorityStoreError):
        mgr.resume()


def test_resume_is_idempotent(tmp_path):
    """resume() 重複呼叫是 idempotent（fold 結果不變）。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    grant = mgr1.grant(_approval(), _context())
    action = _action(grant)

    mgr2 = _manager(store=store)
    mgr2.resume()
    mgr2.resume()
    assert mgr2.is_authorized(action) is True


# ─────────────────────────────────────────────
# 4. Full recovery flow（restart → load → fold → authorize）
# ─────────────────────────────────────────────

def test_full_recovery_flow(tmp_path):
    """restart → load durable log → fold registry → authorization 用恢復的 canonical state。"""
    store = AuthorityStore(data_dir=tmp_path)

    # session 1：grant 兩個 approval，revoke 一個，consume 一個
    mgr1 = _manager(store=store)
    a1 = _approval(work_id="work-1")
    g1 = mgr1.grant(a1, _context())
    a2 = _approval(work_id="work-2", capability="production.write", expires_at=_future(minutes=30))
    g2 = mgr1.grant(a2, _context())
    mgr1.revoke(a1.approval_id)
    mgr1.consume(g2.grant_id)

    # session 2（restart）：新 manager，同 store，resume
    mgr2 = _manager(store=store)
    mgr2.resume()

    # revoked approval → deny
    assert mgr2.is_authorized(_action(g1)) is False
    # consumed single_action grant → deny
    assert mgr2.is_authorized(_action(g2, action=dict(a2.requested_action))) is False


# ─────────────────────────────────────────────
# 5. 零 DSH coupling
# ─────────────────────────────────────────────

def test_persistence_does_not_import_dsh():
    """persistence.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(persistence_mod)), (
        "persistence.py 不得 import DSH / Cordis type"
    )


def test_authority_still_does_not_import_dsh():
    """authority.py（additive 後）仍不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(authority_mod)), (
        "authority.py 不得 import DSH / Cordis type"
    )


# ─────────────────────────────────────────────
# 6. 文件化 mangled-name 雙注入（known/accepted limitation）
# ─────────────────────────────────────────────

def test_mangled_name_double_injection_returns_true_documented():
    """known/accepted limitation：mangled-name 雙注入「完全一致」偽造對 → True。

    name mangling 是 defense-in-depth，不是 security boundary（Python 無真 private）。
    若 caller 直接經 `_AuthorityManager__grants` / `_AuthorityManager__approvals`
    注入「完全一致」的偽造 approval+grant 對，is_authorized 的 structural comparison
    無法偵測（grant↔approval 逐欄一致），會回傳 True。

    這是 MVP-5-R2 Final Review 明確記錄的 known/accepted limitation：
    真正的 boundary 是「canonical registry 是 manager-controlled storage」，
    name mangling 只是讓 public object graph 無法注入；Python 層的
    `_AuthorityManager__grants` 是 escape hatch，非 security boundary。
    """
    mgr = _manager()
    approval, grant, action = _forged_chain()
    # 直接經 name-mangled slot 注入「完全一致」的偽造對
    mgr._AuthorityManager__approvals[approval.approval_id] = approval
    mgr._AuthorityManager__grants[grant.grant_id] = grant
    # 完全一致的偽造對通過 structural comparison → True（known/accepted limitation）
    assert mgr.is_authorized(action) is True


# ─────────────────────────────────────────────
# 7. Single-writer enforcement（R1：bypass / forged event → DENY）
# ─────────────────────────────────────────────

def _write_raw_event(store: AuthorityStore, event_type, payload) -> None:
    """直接寫一筆 raw AuthorityEvent JSON 到 store（模擬 malformed / 手動注入 row）。"""
    event = AuthorityEvent(event_type=event_type, payload=payload)
    with open(store.store_file, "a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")


def test_authority_store_append_requires_actor(tmp_path):
    """direct AuthorityStore.append(event) 缺 actor → TypeError（actor 必填無 default）。"""
    store = AuthorityStore(data_dir=tmp_path)
    event = AuthorityEvent(
        event_type=AuthorityEventType.APPROVAL_GRANTED,
        payload={"approval": {"approval_id": "a-1"}},
    )
    with pytest.raises(TypeError):
        store.append(event)  # 缺 actor


def test_authority_store_append_denies_non_durable_writer(tmp_path):
    """direct AuthorityStore.append(event, "attacker") → NotDurableWriterError（DENY）。"""
    store = AuthorityStore(data_dir=tmp_path)
    event = AuthorityEvent(
        event_type=AuthorityEventType.APPROVAL_GRANTED,
        payload={"approval": {"approval_id": "a-1"}},
    )
    with pytest.raises(NotDurableWriterError):
        store.append(event, "attacker")
    # 偽造 event 未寫入 durable log
    assert store.read_events() == []


def test_authority_store_append_denies_dsh_adapter(tmp_path):
    """DSH 側 actor（dsh_adapter）→ NotDurableWriterError（DSH 只讀不寫 durable state）。"""
    store = AuthorityStore(data_dir=tmp_path)
    event = AuthorityEvent(
        event_type=AuthorityEventType.APPROVAL_GRANTED,
        payload={"approval": {"approval_id": "a-1"}},
    )
    with pytest.raises(NotDurableWriterError):
        store.append(event, "dsh_adapter")


@pytest.mark.parametrize(
    "event_type,payload",
    [
        (AuthorityEventType.APPROVAL_GRANTED, {"approval": {"approval_id": "forged-a"}}),
        (AuthorityEventType.APPROVAL_REVOKED, {"approval": {"approval_id": "forged-a"}}),
        (AuthorityEventType.GRANT_CONSUMED, {"grant": {"grant_id": "forged-g"}}),
    ],
)
def test_forged_authority_event_denied(tmp_path, event_type, payload):
    """forged APPROVAL_GRANTED / APPROVAL_REVOKED / GRANT_CONSUMED event → DENY。

    直接拿 AuthorityStore 以非-durable-writer 身份 append 偽造 authority event，
    必須被 store-level writer check 拒絕（不能只靠 AuthorityManager 擋）。
    """
    store = AuthorityStore(data_dir=tmp_path)
    event = AuthorityEvent(event_type=event_type, payload=payload)
    with pytest.raises(NotDurableWriterError):
        store.append(event, "attacker")
    # 偽造 event 未寫入 durable log
    assert store.read_events() == []


# ─────────────────────────────────────────────
# 8. Malformed event（P1：resume 不 crash / 不產生半套 state）
# ─────────────────────────────────────────────

def test_resume_skips_malformed_approval_event(tmp_path):
    """malformed approval event（payload 缺 "approval" key）→ resume 不 crash、跳過。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    grant = mgr1.grant(approval, _context())
    action = _action(grant)

    # 插入 malformed approval event（valid JSON，但 payload 缺 "approval" key）
    _write_raw_event(store, AuthorityEventType.APPROVAL_GRANTED, {"unexpected": "no approval key"})

    mgr2 = _manager(store=store)
    mgr2.resume()  # 不 crash
    # 合法 grant 仍恢復（malformed event 被跳過，不污染 canonical state）
    assert mgr2.is_authorized(action) is True


def test_resume_skips_malformed_grant_event(tmp_path):
    """malformed grant event（payload 缺 "grant" key）→ resume 不 crash、跳過。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    grant = mgr1.grant(approval, _context())
    action = _action(grant)

    # 插入 malformed grant event（valid JSON，但 payload 缺 "grant" key）
    _write_raw_event(store, AuthorityEventType.GRANT_ISSUED, {"unexpected": "no grant key"})

    mgr2 = _manager(store=store)
    mgr2.resume()  # 不 crash
    assert mgr2.is_authorized(action) is True


def test_resume_skips_non_dict_payload(tmp_path):
    """payload["approval"] 非 dict（如字串）→ resume 不 crash、跳過。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    grant = mgr1.grant(approval, _context())
    action = _action(grant)

    # payload["approval"] 是字串（非 dict）
    _write_raw_event(store, AuthorityEventType.APPROVAL_GRANTED, {"approval": "not-a-dict"})

    mgr2 = _manager(store=store)
    mgr2.resume()  # 不 crash
    assert mgr2.is_authorized(action) is True


def test_resume_skips_invalid_approval_fields(tmp_path):
    """payload["approval"] 缺必填欄位 → resume 不 crash、跳過（不產生半套 state）。"""
    store = AuthorityStore(data_dir=tmp_path)
    mgr1 = _manager(store=store)
    approval = _approval()
    grant = mgr1.grant(approval, _context())
    action = _action(grant)

    # payload["approval"] 只有 approval_id，缺 work_id/capability 等必填欄位
    _write_raw_event(store, AuthorityEventType.APPROVAL_GRANTED, {"approval": {"approval_id": "half-a"}})

    mgr2 = _manager(store=store)
    mgr2.resume()  # 不 crash
    assert mgr2.is_authorized(action) is True


def test_resume_malformed_grant_does_not_create_half_grant(tmp_path):
    """malformed grant_issued（缺 "grant"）不產生半套 grant → authorization deny。"""
    store = AuthorityStore(data_dir=tmp_path)
    # 只有一筆 malformed grant_issued（缺 "grant" key），無對應 approval
    _write_raw_event(store, AuthorityEventType.GRANT_ISSUED, {"unexpected": "no grant key"})

    mgr = _manager(store=store)
    mgr.resume()  # 不 crash
    # 沒有半套 grant：任何 action 都 deny（grant 不存在）
    forged_grant = CapabilityGrant(
        approval_id="no-such-approval",
        work_id="work-1",
        capability="git.commit",
        grantee_role="developer",
        action_scope=ActionScope.SINGLE_ACTION,
    )
    assert mgr.is_authorized(_action(forged_grant)) is False


# ─────────────────────────────────────────────
# 9. Durable write failure → memory 不 mutation（R1 gate 6）
# ─────────────────────────────────────────────

class _FailingStore:
    """Fake AuthorityStore：append 一律拋 OSError（模擬 durable write failure）。"""

    def append(self, event, actor):
        raise OSError("disk full")

    def read_events(self):
        return []


def test_durable_write_failure_does_not_mutate_memory():
    """durable write failure → in-memory registry 不 mutation（write-ahead）。"""
    mgr = _manager(store=_FailingStore())
    approval = _approval()
    with pytest.raises(OSError):
        mgr.grant(approval, _context())
    # in-memory registry 未 mutation：任何 action 都 deny
    grant = CapabilityGrant(
        approval_id=approval.approval_id,
        work_id=approval.work_id,
        capability=approval.capability,
        grantee_role=approval.grantee_role,
        action_scope=approval.action_scope,
        expires_at=approval.expires_at,
    )
    assert mgr.is_authorized(_action(grant)) is False
