# G1–G5 见证记录（WITNESS-LOG）

## 解译器声明

- 唯一口径：`C:/Users/bbfcc/.local/bin/soul-os-harness/.venv/Scripts/python.exe -B`
 （repo 内 `.venv`，`-B` 抑止 `.pyc` 写盘）。
- Shell：Windows PowerShell 5.1；执行前 `Set-Location` 至仓根。
- `SOUL_OS_DATA_DIR`：指向既有系统 TEMP（`$env:TEMP`），见证 DB 另用
  `tempfile.mkdtemp` 自建子目录；**未触碰 `data/**`**，无写生产路径。
- 行程安全：未杀任何行程；未绑生产埠；无真实 API／LLM／网络调用
  （见证脚本仅 `import sqlite3/tempfile`＋`src.interfaces.*`，无 IO／socket／subprocess）。

## 命令

```powershell
Set-Location 'C:/Users/bbfcc/.local/bin/soul-os-harness'
$env:SOUL_OS_DATA_DIR = $env:TEMP
.venv\Scripts\python.exe -B verification\G1-G5-CONTRACT\witness_g1_g5.py
```

附：原五红确认命令（只跑、未改任何被测物）：

```powershell
Set-Location 'C:/Users/bbfcc/.local/bin/soul-os-harness'
$env:SOUL_OS_DATA_DIR = $env:TEMP
.venv\Scripts\python.exe -B -m pytest tests/interfaces/test_journal.py::test_gap_backend_completed_unreachable tests/interfaces/test_journal.py::test_gap_backend_dest_completion_rejected tests/interfaces/test_journal.py::test_gap_cancel_absent tests/interfaces/test_runtime_port.py::test_gap_no_side_effect_retry_still_uncertain tests/interfaces/test_runtime_port.py::test_gap_decision_port_result_absent -q
```

## 输出（见证脚本，EXIT=0，25 passed, 0 failed）

```text
PASS G1-normal-submit-completes -- state=completed
PASS G1-normal-response-fixed-durably -- state=generated
PASS G1-gap-no-completed-row-state -- row state=transport_sent
PASS G2-gap-backend-dest-rejected -- record_outcome(dest='backend') raises 'bad dest'
PASS G2-substitute-signature-dest-free -- params=['ctx', 'capability', 'decision', 'port']
PASS G2-decision-carries-no-dest -- Decision has no destination field
PASS G2-normal-submit-completes -- state=completed
PASS G3-gap-journal-has-no-cancel -- Journal.cancel absent
PASS G3-normal-cancel-before-submit -- state=cancelled
PASS G3-normal-cancelled-releases -- terminal CANCELLED + zero permits -> released
PASS G3-normal-cancel-after-submit-joins-clean -- state=cancelled
PASS G3-normal-completed-false-is-unknown -- state=unknown
PASS G3-normal-unknown-never-releases -- UNKNOWN -> isolated (no release)
PASS G4-gap-old-submit-general-exc-is-uncertain -- kind=UNCERTAIN
PASS G4-gap-old-submit-has-no-retryable -- first kind=UNCERTAIN, retry kind=UNCERTAIN (never RETRYABLE)
PASS G4-substitute-general-exc-never-retryable -- ValueError -> UNKNOWN via _apply_port_error
PASS G4-normal-clean-failure-is-retryable -- state=retryable
PASS G4-normal-submit-cannot-reenter -- calls=1
PASS G4-normal-retry-replays-stored-call -- state=retryable calls=2 attempt=2
PASS G4-normal-stale-attempt-fence -- calls=2
```

（注：上为首次全绿跑的逐行输出誊录；脚本重跑结果一致，`25 passed, 0 failed`，`EXIT=0`。
首次跑曾有 1 项系见证脚本自身期望笔误——旧 `submit` 重调同 `request_id`
返回已存 `UNCERTAIN` 而非 `NEW`；已修正期望为“永不出现 RETRYABLE”，
被测行为前后一致。）

## 输出（原五红确认，EXIT=1，五项皆红且失败模式与归层一致）

```text
FAILED test_gap_backend_completed_unreachable - assert 'transport_sent' == 'completed'
FAILED test_gap_backend_dest_completion_rejected - JournalError: bad dest
FAILED test_gap_cancel_absent - AttributeError: 'Journal' object has no attribute 'cancel'
FAILED test_gap_no_side_effect_retry_still_uncertain - assert [] == [1]
FAILED test_gap_decision_port_result_absent - ImportError: cannot import name 'Decision'
5 failed in 0.46s
```

## 解读

- 每项“正常例绿”证明替代路径（coordinator／backend_execution）已承载该能力；
- 每项“拒绝例红”钉住原位缺口仍然存在（五红测试保持原断言原失败）；
- G4 的 `retry.calls == []` 与见证 `retry kind=UNCERTAIN` 互证：
  旧 `submit` 对同 `request_id` 的第二次调用直接返回已存 kind，
  port 未被再次调用——RETRYABLE 语义在旧路径永不存在。
