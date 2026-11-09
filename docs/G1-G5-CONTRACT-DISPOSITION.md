# G1–G5 原位缺口歸層補證（CONTRACT-DISPOSITION-1）

- 结论先行：G1–G5 缺的是 **Journal／舊 `submit` 的原位能力**，不是能力本身缺失。
  替代路径（`coordinator`／`backend_execution`）已覆盖全部五项能力；
  本票**推荐保留原契约与原五红测试不断言、不转绿、不弱化**，不宣称修复。
- Frozen 口径：`docs/EI-1-ACCEPTANCE-RATIFIED.md:82`（G1–G5 维持 must-stay-RED）不变；
  `src/interfaces/journal.py`、`src/inner_life`、衰减门槛零差分。
- 编号警告：`tests/interfaces/test_backend_settlement.py` 内的 G1..G5
 （cancel／completion／failure／retry／protocol）与本票 G1..G5
  **是两套编号，不可按编号对应**。引用替代行为时以函数名为准。

## G1 — backend-completed 在 Journal 行状态不可达

- 原 nodeid：`tests/interfaces/test_journal.py:571`
  `test_gap_backend_completed_unreachable`（期望 `_row[8] == "completed"`）。
- 现行拒绝（精确到行）：
  - `src/interfaces/journal.py:345` `record_transport` 只在
    `generated`／`transport_sent` 之间流转行 `state`，**没有 `completed` 行状态**；
  - `src/interfaces/journal.py:482` `record_outcome` 把 `completed` 写进
    `outcomes` 字典（`data[_MEMORY_DEST] = status`），不碰行 `state`。
  - 实测红因：`assert 'transport_sent' == 'completed'`（行状态恒为 `transport_sent`）。
- 替代 API（精确到行）：`src/interfaces/coordinator.py:1846`
  `_apply_port_return` —— 仅**真实 `PortResult(completed=True)` ＋
  `record_generated` 持久化成功**后记 `BACKEND_COMPLETED`；持久化失败／
  `completed=False`／非 `PortResult` 一律 `BACKEND_UNKNOWN`（sealed，不可重试）。
- 正证据（见证 `witness_g1_g5.py`）：
  - `G1-normal-submit-completes`：`submit_backend`＋`OkPort` → `completed`。
  - `G1-normal-response-fixed-durably`：journal 行 `state == "generated"` 且
    `(response_id, response_body) == ("resp-9", "body-9")` —— 回复先固化才算完成。
- 反证据（原位缺口仍在）：`G1-gap-no-completed-row-state` —— 走完
  claim／begin／generated／transport＋`record_outcome(memory, completed)` 后，
  行 `state` 仍为 `transport_sent`；`completed` 只存在于 `outcomes["memory"]`。
- 残余限制：Journal 行状态永远不会出现 `completed`；完成证据只活在
  coordinator 内存账本（`BackendRecord.state == "completed"`）＋ journal 的
  `generated` 行；**backend 终态不能跨重启恢复**（见末节）。

## G2 — Journal 拒绝 backend dest

- 原 nodeid：`tests/interfaces/test_journal.py:581`
  `test_gap_backend_dest_completion_rejected`。
- 现行拒绝（精确到行）：`src/interfaces/journal.py:399`
  `record_outcome` 首行 `if dest != _MEMORY_DEST: raise JournalError("bad dest")`；
  实测红因即此 `JournalError: bad dest`。
- 替代 API（精确到行）：`src/interfaces/coordinator.py:1889` `submit_backend` ——
  签名 `(ctx, capability, decision, port)`（见证已断言参数表逐字相等），
  **没有 destination 参数**；`Decision`（`backend_execution.py:50`）刻意无址
  （`operation`＋`payload`，调用方永不命名目的地）。
- 正证据：
  - `G2-substitute-signature-dest-free`：签名 `== ["ctx", "capability", "decision", "port"]`。
  - `G2-decision-carries-no-dest`：`Decision` 无 `destination` 属性。
  - `G2-normal-submit-completes`：`submit_backend` → `completed`。
- 反证据：`G2-gap-backend-dest-rejected` ——
  `record_outcome(req, 7, "backend", "completed", {...})` 必抛 `bad dest`。
- 残余限制：任何经由 Journal 纪 outcome 的 backend 完成语义**此路不通**；
  backend 完成只能走 coordinator 账本；同 G1，终态内存化、不可跨重启恢复。

## G3 — Journal 无 cancel

- 原 nodeid：`tests/interfaces/test_journal.py:589` `test_gap_cancel_absent`。
- 现行拒绝：`Journal` 类无 `cancel` 属性（`journal.py` 全文件无 `def cancel`）；
  实测红因 `AttributeError: 'Journal' object has no attribute 'cancel'`。
- 替代 API（精确到行）：`src/interfaces/coordinator.py:1972` `cancel_backend` ——
  先封存（`sealed = True`）再取消；未提交（`port is None`）→ `CANCELLED`
  （结构零副作用）；已提交须 `port.confirm_exited()` 真值才 `CANCELLED`，
  缺失／ falsy／抛错一律 `UNKNOWN`；已封存记录直接返回现态、不碰 port。
- 正证据：
  - `G3-normal-cancel-before-submit`：未提交 → `cancelled`。
  - `G3-normal-cancelled-releases`：`settle_request` → `released`
    （终态＋sealed＋零 permit 才释放）。
  - `G3-normal-cancel-after-submit-joins-clean`：RETRYABLE＋clean join → `cancelled`。
  - `G3-normal-completed-false-is-unknown`／`G3-normal-unknown-never-releases`：
    `completed=False` → `unknown`；`settle_request` → `isolated`（UNKNOWN 不得释放）。
- 反证据：`G3-gap-journal-has-no-cancel` —— `hasattr(Journal, "cancel") is False`。
- 残余限制：Journal 侧永远没有取消语义；取消证据只活在内存账本；
  跨重启后账本丢失（见末节），已取消／已密封的区分随之丢失。

## G4 — 旧 `submit` 把一切一般例外归为 UNCERTAIN（无 RETRYABLE）

- 原 nodeid：`tests/interfaces/test_runtime_port.py:398`
  `test_gap_no_side_effect_retry_still_uncertain`。
- 现行拒绝（精确到行）：`src/interfaces/runtime_port.py:102`
  `submit` 的 `except Exception: … return SubmitResult("UNCERTAIN", rid)` ——
  包括 `ValueError("clean failure")` 这类已证无副作用的失败。
  实测红因：同 `request_id` 的第二次 `submit` 直接返回已存 kind，
  `retry.calls == []`（port 根本没被第二次调用，更无 RETRYABLE 语义）。
- 替代 API（精确到行）：
  - `src/interfaces/coordinator.py:1872` `_apply_port_error` —— 仅显式
    `PortError(ambiguous=False)`（port 自证无副作用）→ `RETRYABLE`（lease 保留）；
    歧义 `PortError` 或**任何一般例外一律 `UNKNOWN`（sealed），永不进 RETRYABLE**。
  - `retry_backend`（`:1922`）重放**已存 `decision`／`port`**＋`attempt` 围栏
    （stale attempt 零重发拒绝；并发重试 GIL 顺序下恰一次 port 调用）。
- 正证据：
  - `G4-substitute-general-exc-never-retryable`：`ValueError` → `unknown`。
  - `G4-normal-clean-failure-is-retryable`：`PortError(False)` → `retryable`。
  - `G4-normal-submit-cannot-reenter`：重复 `submit_backend` 抛错且 `calls == 1`。
  - `G4-normal-retry-replays-stored-call`：`retry_backend` → `retryable`，
    `calls == 2`，`attempt == 2`（同一 stored call）。
  - `G4-normal-stale-attempt-fence`：`attempt=0`（stale）抛错且 `calls` 不变。
- 反证据：`G4-gap-old-submit-general-exc-is-uncertain`／
  `G4-gap-old-submit-has-no-retryable` —— 旧 `submit` 首调 `UNCERTAIN`、
  重调仍 `UNCERTAIN`，`RETRYABLE` 永不出现。
- 残余限制：旧 `submit` 原位不动（保持红）；重试语义只存在于 coordinator；
  `attempt` 围栏与 stored call 皆为内存态，重启后不可恢复（见末节）。

## G5 — Decision／PortResult／PortError 型别不在旧模组

- 原 nodeid：`tests/interfaces/test_runtime_port.py:411`
  `test_gap_decision_port_result_absent`。
- 现行拒绝：`src/interfaces/runtime_port.py` 全模块仅含
  `FakeCancelled`／`FakeClock`／`SubmitResult`／`FakeRuntime`／`submit`，
  无 `Decision`／`PortResult`（实测红因 `ImportError: cannot import name
  'Decision'`；`PortResult` 同理，见证已双双验证）。
- 替代 API（精确到行）：`src/interfaces/backend_execution.py`
  `:50` `Decision`（`operation`＋`payload`，`__post_init__` 非空校验）、
  `:69` `PortResult`（`response_id`／`response_body`／`completed: bool`
  严格布尔）、`:93` `PortError`（`ambiguous: bool` 严格布尔；
  `False`＝自证无副作用→可重试，`True`＝未知→隔离）。
- 正证据：
  - `G5-normal-port-without-execute-refused`：无 `execute` 的 port 被拒。
  - `G5-normal-non-decision-refused`：非 `Decision` 输入被拒。
  - 另有既有测试 `test_g5_port_protocol_refusals`（`test_backend_settlement.py:494`）
    覆盖携 `destination`／`memory`／`completed` 证据属性的脏 port 一律拒绝——
    本票仅引用，不断言细节（**注意该文件的 G5 与本票 G5 不同义**）。
- 反证据：`G5-gap-Decision-absent-from-runtime-port`／
  `G5-gap-PortResult-absent-from-runtime-port`。
- 残余限制：旧模组永不增补三型别（保持红）；port 协议检查只在
  `submit_backend` 入口执行一次（`_require_backend_port`），已存 port 假设已验。

## 共同残余限制（五项共有）

1. **五红继续存在**：`test_journal.py:571／:581／:589`、
   `test_runtime_port.py:398／:411` 保持原断言、原失败，不得 skip／xfail／转绿；
   关闭它们需要主大脑另开决策工单。
2. **backend 终态不能跨重启恢复**：`BackendRecord` 为 `AdmissionContext`
   内存账本（`ctx._backend_ledger`），`COMPLETED／FAILED／CANCELLED／UNKNOWN／
   RETRYABLE` 皆不落盘；journal 只持久化 `generated` 行与 memory outcomes。
   重启后账本清空：已完成不可再证、已密封不可再辨、retry 围栏归零。
   这是设计（fail-closed 内存证据），不是待修 bug；调用方不得假设终态可恢复。
3. **本票零产品码变更**：仅新增 `docs/G1-G5-CONTRACT-DISPOSITION.md` 与
   `verification/G1-G5-CONTRACT/` 下见证档；`git diff --stat` 应仅有新增档，
   frozen 档与 `data/` 零变动。
