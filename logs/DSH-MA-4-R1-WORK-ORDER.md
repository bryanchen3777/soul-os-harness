# 工單：MA-4-R1 — Authority Trust Establishment + Resume Idempotency

**日期**：2026-08-23
**性質**：MVP implementation（corrective work，可寫 code）
**上游**：`docs/DSH-ADAPTER-BUILD-PLAN.md`（MA-4 BLOCKED）、MA-4 Independent Review（2 個 BLOCK）

---

## 0. 背景

MA-4 Build Plan 被 Independent Review 判 BLOCKED，兩個 assertion 要收斂成可驗證 mechanism：

1. **Authority trust establishment 未 specify**：`authority_token` 是普通字串，`HumanAuthorityPort.authenticate()` 是未實作 Protocol，無 HMAC/signature。token 可偽造/重放，`grant()` 不檢查 `context.expires_at`。
2. **Resume idempotency claim 錯誤**：`idempotency_keys` 是 dead field（永遠 `[]`），`consume_handoff` 無 dedup。

## 核心原則（不可破）

**Authority 與 idempotency 都屬於 Soul Domain Core / durable authority boundary 的責任；Adapter 只能 transport / invoke，不能成為新的 authority 或 durable truth owner。**

本工單只改 `src/work/`（Domain Core），不碰 DSH Adapter。

## P0 — Authority Trust Establishment

1. `authority_token` 改為 HMAC-signed：`HMAC-SHA256(secret, f"{identity}:{issued_at}:{expires_at}:{nonce}")`。
2. 新增 concrete `HmacHumanAuthorityPort`（實現 `HumanAuthorityPort.authenticate`）：驗證 HMAC signature、issued_at 在過去、expires_at 在未來、nonce 未重放。
3. `grant()` 強制檢查 `context.expires_at`（過期 → deny）。
4. replay 防護：nonce registry（已用 nonce 拒絕重放）。
5. 明確區分 trust establishment（HMAC 驗證，Domain Core）與 IPC transport（token 傳遞，未來 Adapter）。

## P1 — Resume Idempotency

1. `idempotency_key = hash(work_id + role + result_type + artifact_refs/evidence_refs/decision)`。
2. `consume_handoff()` 在 Domain Core 做 dedup：若 idempotency_key 已存在於 durable log → skip（回傳既有 event），不重複 append。
3. duplicate / crash-after-write / retry 都有 deterministic behavior（dedup by idempotency_key）。
4. 補測試證明 effectively-once（至少明確 dedup semantic）。

## 範圍

- 修改 `src/work/authority.py`（HMAC + grant expiry + replay）
- 修改 `src/work/kernel.py`（consume_handoff dedup）
- 修改 `src/work/store.py` / `bridge.py`（idempotency_key 推導，只做 additive）
- 修改 `tests/test_work_*.py`（補 forgery / replay / expiry / dedup / crash-after-write 測試）

## 驗收（完成的定義）

- 全 work 回歸綠。
- authority_token 是 HMAC-signed（不是普通字串）。
- grant() 檢查 expires_at（過期 deny）。
- replay 防護（nonce 重放 deny）。
- consume_handoff 有 dedup（duplicate handoff 不重複 append）。

## 不做（Out of Scope）

- 不碰 DSH Adapter、不 import DSH。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 只做 additive，不破壞既有語意。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- P0 / P1 的實作方式
- 新增了哪些測試
- 完整回歸結果
- 確認 authority trust establishment + replay protection + resume idempotency 全部 enforced
- 剩餘 architectural concerns
