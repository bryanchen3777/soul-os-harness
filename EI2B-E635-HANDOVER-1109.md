# EI-2B e635 合入交接素材（非 canonical／待合入）

> Sol 裁决：ACCEPTED、放行合 main。尚未合入、未部署。canonical 待取得原仓写入窗口后一次追加;
> 本文件仅为交接暂存，不替代 `logs/ENGINEERING_STATE.md`。

## 提交链（整合树 `ei2b-proven18-integrated-1109`，干净）

- 基底：`be6219c`（EI-2B integrated candidate，前置含 SETTLE-1 等核准范围）
- `63a8cec` — `scripts/eval_lifecycle_cli.py`（11-01 cohort 台账 +1，bounds=nodes=112；PROVEN18 豁免保留）
- `78f9a09` — `tests/test_eval_lifecycle_cli.py`（`TestE635LedgerBoundary` 7 测）
- merge-base（origin `5ae6760` vs 整合树 HEAD）：`5ae6760`＝origin main 本身 ⇒ 祖先关系成立，快转路径可用（快转不产生合并提交）。

## 验证证据（解译器：repo 内 `.venv` Python 3.11.15）

- CLI 冻结槽（来源唯读＋自建 TEMP，`--simulated-time 2026-11-09T03:00:00+00:00`）：提交后重跑 `exit=0`／PASS
  日志：`C:\Users\bbfcc\AppData\Local\Temp\e635_postcommit_cli.stdout.log`
- `tests/test_eval_lifecycle_cli.py`：117 passed、1 skipped（提交后重跑一致）
- C5 变异主脑重跑：`TEMP/e635_mut/run_e635_mut.py` OVERALL=GREEN（control 绿、M1–M6 各因指定断言红、repo SHA 不变）
- C6 主脑核读：FAILED/ERROR 46vs46 双向差分空；collect 大小写归一后 extra44/missing0；
  passed 差 +44 吻合（CLI 38＋EI1 guard 6）；大小写归一仅用于已确认的 9 组配对，不得扩张为通用规则
- 全库既有红：36 failed／10 errors（＋g5 环境性红），非本票新增，不得写成全库全绿。

## 原仓状态（2026-11-09 检查，本轮未碰原仓）

- origin HEAD：`5ae6760`（main），整合树快转祖先条件成立。
- 但原仓工作树脏（他人在途）：`M AGENTS.md`、`M logs/ENGINEERING_STATE.md`、
  `M scripts/eval_lifecycle_cli.py`（与本票同文件重叠！）、`M src/...` 多档、
  `A tests/infra/test_soeb_judge_merge_guard.py`、`M tests/...`、大量 `??`（docs／logs／scripts／src/interfaces／tests）。
- 结论：窗口未成立，不得快转／push／stash 他人改动／重置工作树。合入动作停止，工程其他部分继续。

## Notion

- marker `EI2B-E635-ACCEPTED-1109`：状态页 2697→2701、EI 计划页 418→421（分頁回读验证）。

## 窗口成立后的动作

1. 重新读取最新 `main` 与 canonical，确认基底未动、重叠方已交接。
2. 在隔离树完成必要整合与 canonical 追加（仅追加本票条目，不整份替换）。
3. 检查最终差异＋适用验证（受验内容变化则补验受影响闸门）。
4. 经协调的安全路径合入，更新 Notion（已验收／已合入／未部署分开标），部署另计。
