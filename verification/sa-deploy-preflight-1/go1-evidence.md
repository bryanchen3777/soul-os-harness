# SA-BUDGET-GO-1 evidence citation (no new model calls in this ticket)
source: verification/sa-budget-go-1/SUMMARY.json @ 6fdd38f
  sha256: 5305e7f0191c15b7c5d57beead6345d1e8865b1f30b373da753fcca42bcbe26b

- verdict: COMPLETE_15_OF_15_ALL_ACCEPT (5 channels x T1 x 3 shots)
- total_billed_output: 40088 tokens (output_tokens 原值計一次，含推理；reasoning 明細不重複相加)
- retries: 0 | halted_any: False | unknown_count: 0 | 超時: 0 | 存證失敗: 0
- per-channel billed [r1, r2, r3]:
  - glm-5.3-flash (chat-completions, effort low): [818,987,861] budget 8000/24000/3
  - mimo-v2.6-flash (chat-completions, effort null): [1219,2607,2294]
  - minimax-m3.1-flash (chat-completions, effort null): [4599,5039,2995]
  - qwen3.8-flash (messages, effort null): [5174,4414,3566]
  - muse-spark-1.3-contributor (responses, effort low): [1793,1858,1864] budget 16000/48000/3
  (full per-model finish reasons + auth_ids in SUMMARY.json; finish: stop/stop/stop,
   end_turn x3, completed x3 — 五通道協議皆正常結束)
- criteria: criteria-go1.json + detector_go1.py (sha256 2cceec8f... / 7cc16754...)
  HTTP200 / 非空 / 協議正常結束 / 輸出<本發上限 / 五問齊全 / 無推理洩漏；UNKNOWN 停全批
- spec frozen: task-t1-go-effort-low.json / task-t1-go-effort-null.json
  task_sha256 c76c9253613bdab7c8e52b00dc89c0f16b40820a
- settlement: billed = output_tokens 原值；缺失/非法/口徑矛盾判 UNKNOWN；本批 15 發全 settled
- 測試額度聲明：測試額度不轉成上線額度；本票不再發真實模型請求（零新增成本）。
- "59 測試全綠"：GO-1 commit 測試檔 tests/test_sa_budget_go1.py + tests/test_sa_harness_budget_1.py
  + tests/infra/test_sa_harness_budget_guard.py，於本票全庫回歸中重驗（見 full-regression.txt）。
