"""
src/inner_life/firewall_wiring.py — SI-2.1 防線 3 生產接線（per-agent IdentityFirewall）

工單: OQ5-SI21-WIRING-1（SI-2.1 三道防線歷史遺留缺口接線）
契約: docs/SOCIAL-DIFFUSION-CONTRACT.md（SI-2.1）§4（防線 3 Identity Firewall）
      ＋ src/inner_life/submission_gate.py:326-345（verify() 第 6 步）
      ＋ docs/LIFE-THREAD-ENGINE-CONTRACT.md §11 OQ-5（缺口登記）

背景（缺口）:
  ``SubmissionGate.verify()`` 第 6 步是 ``if self._identity_firewall is not None:``
  —— 未注入 firewall 就**整步跳過**（靜默放行）。生產端
  ``scripts/run_server.py``（原 ``:495-498``）建構 gate 時只傳 ``writer=`` /
  ``trace_reader=``，故防線 3 在生產從未生效。

本模組只做接線（**0 改判定**）:
  - 判定語意 100% 由既有 ``IdentityFirewall`` 承載 → 本模組 0 改判定。
  - 不修改 ``submission_gate.py`` / ``identity_firewall.py``（兩者皆 frozen 面）。

🔴 為什麼必須是 **per-agent** 而不是「在單一 gate 上補一個 firewall」:
  ``IdentityFirewall.current_agent_id`` 是**單一值**（``identity_firewall.py:53``），
  而生產只有**一顆** gate 服務**全部靈魂** —— 三個 submit 呼叫點分別來自
  DreamHandler / DiaryHandler / EventHandler（``scripts/run_server.py`` 的
  ``submission_gate.submit(_event.event_id, agent_id=_event.provenance.actor_id)``），
  另有 WorldInnerLifeAdapter 的系統路徑（``submit(_eid)``, actor_id=None）。
  若在單一 gate 上注入 ``IdentityFirewall(current_agent_id="agent_X")``，
  則**其餘 9 個靈魂的自我事件**（actor_id=agent_Y != agent_X）會被判定
  ``EXTERNAL_OTHER_ACTION`` → **fail-closed 誤擋** ⇒ 9/10 靈魂的昇華鏈整體停擺。
  故本模組以「per-agent gate（每個靈魂一顆, 各自持有自己的 firewall）＋ 系統 sentinel
  gate」的分派器取代單一 gate, 使每個靈魂只對**自己的** actor_id 放行。

與 ``tests/harness/social_harness_fixtures.py:335-357`` 的既有 per-agent 組裝
（``IdentityFirewall(current_agent_id=agent_id)`` ＋
 ``SubmissionGate(agent_id=agent_id, identity_firewall=self.firewall)``）語意一致。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

from src.social.identity_firewall import IdentityFirewall

from .submission_gate import SubmissionGate

logger = logging.getLogger("soul_os.inner_life.firewall_wiring")

# 系統／世界事件路徑的 sentinel ``current_agent_id``。
#
# 為什麼安全: 世界事件由 M5.9-3 WorldInnerLifeAdapter 產生, ``actor_id=None``
# （EL-OWN-0 刻意不傳 agent_id, 使昇華節點維持 system-level "default"）。
# ``IdentityFirewall.classify(None)`` 直接回 ``SYSTEM_ACTION``（
# ``identity_firewall.py:78-79``）→ 不與 sentinel 比較 → 行為與注入前一致（放行）。
# sentinel 只是把「未注入 = 靜默跳過」換成「真的有判」: 若未來有任何**帶真實
# actor_id** 的事件誤走系統路徑, 會被判他者 → fail-closed 拒絕（正確方向）。
SYSTEM_AGENT_ID = "__system__"


def build_identity_firewall(agent_id: str) -> IdentityFirewall:
    """建構 IdentityFirewall（fail-closed: agent_id 缺/空一律 raise）。"""
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError(
            f"agent_id 必填且為非空 str（fail-closed, 不得靜默跳過防線 3）, "
            f"got: {agent_id!r}"
        )
    return IdentityFirewall(current_agent_id=agent_id.strip())


def build_submission_gate(
    *,
    writer: Any,
    trace_reader: Any = None,
    agent_id: str,
    llm: Any = None,
    store_dir: Any = None,
    enabled: bool = True,
) -> SubmissionGate:
    """建構**已注入防線 3** 的 SubmissionGate（agent_id 必填, fail-closed）。

    ``identity_firewall`` 一律顯式注入 —— 本函式**不提供**「不注入」的選項,
    使「忘了接線」在型別/參數層就不可能發生。
    """
    firewall = build_identity_firewall(agent_id)
    return SubmissionGate(
        writer=writer,
        trace_reader=trace_reader,
        llm=llm,
        store_dir=store_dir,
        agent_id=agent_id,
        enabled=enabled,
        identity_firewall=firewall,
    )


def build_system_submission_gate(
    *,
    writer: Any,
    trace_reader: Any = None,
    llm: Any = None,
    store_dir: Any = None,
    enabled: bool = True,
) -> SubmissionGate:
    """建構系統／世界事件路徑的 gate（``agent_id=None`` 維持 EL-OWN-0 語意）。

    ``agent_id=None`` 讓 ``run_elevation`` 沿用既有 system-level 歸屬（"default"）;
    firewall 用 ``SYSTEM_AGENT_ID`` sentinel（見上方說明）。
    """
    return SubmissionGate(
        writer=writer,
        trace_reader=trace_reader,
        llm=llm,
        store_dir=store_dir,
        agent_id=None,
        enabled=enabled,
        identity_firewall=IdentityFirewall(current_agent_id=SYSTEM_AGENT_ID),
    )


class FirewalledSubmissionGate:
    """per-agent 防火牆分派器（production 組裝用 drop-in 替代單一 SubmissionGate）。

    對外介面與 ``SubmissionGate`` 相容（``.submit()`` / ``.get_stats()``）:
      - ``submit(event_id, agent_id=<該靈魂>)`` → 路由到該靈魂專屬的 firewalled gate
      - ``submit(event_id)``（無 agent_id, 世界／系統路徑）→ 路由到系統 sentinel gate

    Fail-closed:
      - 無法為某 agent 建構 gate（firewall 或 gate 建構失敗）⇒ **拒絕**
        （回 ``[]``, 不 consume）, 絕不退回「無 firewall 的 gate」。
      - ``base_gate``（可選）僅用於系統路徑的既有 gate 物件沿用; 未提供時由
        本類自行建構系統 sentinel gate。
    """

    def __init__(
        self,
        *,
        writer: Any,
        trace_reader: Any = None,
        llm: Any = None,
        store_dir: Any = None,
        enabled: bool = True,
        base_gate: Optional[SubmissionGate] = None,
        system_gate: Optional[SubmissionGate] = None,
    ) -> None:
        self._writer = writer
        self._trace_reader = trace_reader
        self._llm = llm
        self._store_dir = store_dir
        self.enabled = bool(enabled)
        self._gates: Dict[str, SubmissionGate] = {}
        # 系統／世界事件路徑: base_gate 或自建 sentinel gate
        self._system_gate: Optional[SubmissionGate] = (
            system_gate
            if system_gate is not None
            else (
                base_gate
                if base_gate is not None
                else build_system_submission_gate(
                    writer=writer,
                    trace_reader=trace_reader,
                    llm=llm,
                    store_dir=store_dir,
                    enabled=enabled,
                )
            )
        )
        self._refused = 0

    # ── 對外 API（與 SubmissionGate 相容）───────────────────

    def submit(
        self,
        event_id: str,
        memory_facts: Sequence[Any] = (),
        *,
        agent_id: Optional[str] = None,
    ) -> list:
        """驗證該靈魂的 event_id → 通過則 consume；無 gate 可用則 fail-closed ``[]``。"""
        gate = self._gate_for(agent_id)
        if gate is None:
            self._refused += 1
            return []
        return gate.submit(event_id, memory_facts, agent_id=agent_id)

    def get_stats(self) -> Dict[str, Any]:
        """Observability: 已建構的 gate 數 / 拒絕數 / 各 gate counters。"""
        return {
            "gates": len(self._gates),
            "refused": self._refused,
            "per_agent": {
                aid: gate.get_stats() for aid, gate in self._gates.items()
            },
        }

    # ── 內部 ──────────────────────────────────────────────

    @property
    def system_gate(self) -> Optional[SubmissionGate]:
        """系統／世界事件路徑的 gate（觀測用）。"""
        return self._system_gate

    def gate_for(self, agent_id: Optional[str]) -> Optional[SubmissionGate]:
        """取（必要時建構）某 agent 的 firewalled gate（觀測 / 測試用）。"""
        return self._gate_for(agent_id)

    def _gate_for(self, agent_id: Optional[str]) -> Optional[SubmissionGate]:
        key = (
            agent_id.strip()
            if isinstance(agent_id, str) and agent_id.strip()
            else SYSTEM_AGENT_ID
        )
        if key == SYSTEM_AGENT_ID:
            return self._system_gate
        cached = self._gates.get(key)
        if cached is not None:
            return cached
        try:
            gate = build_submission_gate(
                writer=self._writer,
                trace_reader=self._trace_reader,
                agent_id=key,
                llm=self._llm,
                store_dir=self._store_dir,
                enabled=self.enabled,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed: 建不出來就拒絕
            logger.warning(
                f"[FirewalledSubmissionGate] 無法為 agent={key!r} 建構 "
                f"firewalled gate — fail-closed 拒絕 (不 consume, 絕不退回無 "
                f"firewall 的 gate): {type(exc).__name__}: {exc}"
            )
            return None
        self._gates[key] = gate
        logger.info(
            f"[FirewalledSubmissionGate] 已為 agent={key!r} 建構 firewalled "
            f"SubmissionGate (identity_firewall=yes, agent_id={key!r})"
        )
        return gate


__all__ = [
    "SYSTEM_AGENT_ID",
    "FirewalledSubmissionGate",
    "build_identity_firewall",
    "build_submission_gate",
    "build_system_submission_gate",
]
