# EI-1-ACCEPTANCE-RATIFY-1 FUP-2 補證記錄

- 工單：EI-1-ACCEPTANCE-RATIFY-1-FUP-2（主腦退回補證）
- 適用 commit：`f19aac1`；解譯器：`.venv` Python 3.11（計數口徑唯一準）
- 每次 pytest 前新建 TEMP 作 `SOUL_OS_DATA_DIR`
- 改動範圍：`tests/interfaces/test_ei1_acceptance_ratified.py`、
  `docs/EI-1-ACCEPTANCE-RATIFIED.md`（四格證據欄）、本檔新增；
  `verification/.../SUMMARY.md` 維持原記錄不改（歷史見證凍結，新數只記於此）。
- `git status --short` 全程僅上述 `??`；`data/` 零變動；未 commit／add／合入。

## 1. 變異 oracle 三態重做

退回點：原寫法「只證變異生效、未證 oracle 殺死」（只斷言 mutant 行為跑偏，
沒跑同一 oracle 證正常版綠＋mutant 版紅）。重做後每個變異測試皆為三態：
**正常綠**（同一 oracle 跑正常版須通過）＋**放寬紅**（mutant 誤接納 ⇒ oracle 轉紅）／
**收緊紅**（mutant 誤拒絕合法輸入 ⇒ 殺死）。

共享 helper（現檔 `:232–262`）：
`_oracle_rejects_unknown_top_key`（C1 專用）、`_oracle_timing_guard`（I3 專用）、
`_loosen_kill`（放寬型通用：同一拒絕斷言跑正常版綠＋mutant 版 `DID NOT RAISE` 紅；
採用點 `:330` C2、`:369` C3、`:482` C5、`:533／548` C6，其餘放寬型為同形手寫三態）。

### 1.1 C1-loosen（工單 :248 → 現 `:281 test_c1_mut_loosen_top_key`）

- 修正前（無歷史版本留存，新檔未追蹤；以下為碼內 FUP-2 註記原文）：
  「只證 mutant 被接納，未證同一斷言遇變異必紅」。
- 修正後（現 `:281–290`）：`_oracle_rejects_unknown_top_key(parse_request, raw, _route())`
  先跑正常版（綠）；同一 oracle 跑 mutant 版必須拋 `DID NOT RAISE`（紅，
  mutant 誤接納未知頂層鍵 `tools` 即見證）。

### 1.2 I3-loosen（工單 :910 → 現 `:1051 test_i3_mut_loosen_plain_eq`）

- 修正前（碼內註記原文）：「只斷言 mutant 缺比較＝證變異生效」。
- 修正後（現 `:1051–1075`）：恆時性條款是源碼性質（plain-eq 下功能輸出不變），
  故 oracle 定為存在性斷言 `_oracle_timing_guard`：正常版含 `hmac.compare_digest`（綠），
  同一斷言跑 mutant（`cred.digest == digest`）必須拋 AssertionError（紅）；
  另保留 mutant 功能面見證（合法 token 過／錯 token 仍 401），註明三態已由存在性 oracle 承載。

### 三態表（全 59 個 mut 測試一體適用）

| 態 | 正常版 | mutant 版 | 判定 |
|---|---|---|---|
| 放寬型 | 同一拒絕斷言通過（綠） | 誤接納 ⇒ 內層 `pytest.raises` 拋 `DID NOT RAISE`（紅） | 殺死＝紅出現 |
| 收緊型 | 同一 oracle 合法輸入通過（綠） | 誤拒絕 ⇒ status／異常見證（紅） | 殺死＝紅出現 |
| 行為型（C7/P3/P4/P5/P6/S/N1-loosen） | 狀態／預設／形狀斷言通過（綠） | 狀態翻轉（候選錯授／預設掉出合法區／欄位可寫／repr 洩漏） | 殺死＝翻轉見證 |

### P1-loosen 真 bug（重做附帶發現，已修）

mutant 住隔離包（`mutpkg_*`），其 `InterfaceError` 與正常版**不同一類別**，
`except InterfaceError` 接不住 mutant 的 404（實測 FAILED）。現判據為
`except BaseException`＋`getattr(e, "status", None) == 404`（現 `:1195–1199`）。

## 2. 30 AC 逐條自查清單

59 個 mut 測試全含正常版對照斷言（檔內 57 處「正常綠」行內標記＋共享 helper 內建；
`_loosen_kill` 5 處採用＋其餘同形手寫；C1／I3 用專用 oracle；G6 無變異見下）。

| AC | loosen（放寬三態） | tighten（收緊＋綠對照） | 正常見證 |
|---|---|---|---|
| C1 | `:281` 專用 oracle | `:293` 刪 `user` 拒合法 | `:268` |
| C2 | `:325` `_loosen_kill` | `:339` 反轉條件拒合法 | `:314` |
| C3 | `:364` `_loosen_kill` | `:378` 恆真拒合法 | `:355` |
| C4 | `:410` 四閾值天文數字 | `:442` `>0` 拒非空 | `:394`（413 code `request_too_large` 四組皆是） |
| C5 | `:467` 五層一併移除＋`_loosen_kill` | `:491` 恆真拒乾淨 | `:458` |
| C6 | `:528` stream＋bool-temp（`_loosen_kill`×2） | `:557` 範圍收緊拒合法 | `:507` |
| C7 | `:615` 末則掉進 HISTORY | `:627` 全升 INSTRUCTION | `:581`（行為型，狀態翻轉見證） |
| G1 | `:668` `extra` 被接納 | `:686` `!=5` 拒合法 4 鍵 | `:648` |
| G2 | `:732` 壞 digest＋重複 backend 被接納 | `:757` `!=65` 拒合法 | `:702` |
| G3 | `:782` 外來 principal 被接納 | `:798` 恆真拒 owner | `:773` |
| G4 | `:829` utility＋持久記憶被接納 | `:848` 恆真拒合法 interactive | `:814` |
| G5 | `:879` 未知路由綁首 route | `:897` 合法路由亦 404（oracle `(404, route_not_found)`） | `:869` |
| G6 | —（無變異；500 斷言併 G1–G4） | — | `:916` |
| I1 | `:935` 任意 token 皆認證 | `:957` 正確 token 被拒 | `:928` |
| I2 | `:991` `None` 繞型別閘（TypeError 破線） | `:1015` `>0` 拒合法 | `:979` |
| I3 | `:1051` 專用存在性 oracle | `:1078` 壞雜湊拒合法（401） | `:1037` |
| I4 | `:1121` 未授權被放行 | `:1142` 已授權被拒 | `:1100` |
| P1 | `:1174` 認證延後變 404（status 判據，見 §1 真 bug） | `:1204` 刪 branch 預設拒合法 | `:1160` |
| P2 | `:1243` 重複＋未知 x-soul 被接納 | `:1274` 已知 header 被拒 | `:1223` |
| P3 | `:1317` utility 誤授候選 | `:1334` 候選被降級 | `:1296`（行為型） |
| P4 | `:1372` 9/9 多輪被放行 | `:1388` 0 次破壞合法規格 | `:1364`（行為型） |
| P5 | `:1420` 刪 branch 欄致碰撞 | `:1439` 加 request_id 致分裂 | `:1407`（行為型） |
| P6 | `:1477` 刪 turn 致指紋相同 | `:1491` 加 request_id 致指紋變 | `:1459`＋`:1509` 四者敏感度（行為型，見 §3） |
| S1 | `:1573` 預設 2.5 掉出合法區 | `:1585` 刪預設定義期 TypeError | `:1546`（行為型） |
| S2 | `:1617` 去 frozen 可改寫 | `:1634` 改名爆 TypeError | `:1596`（行為型） |
| S3 | `:1661` repr 洩 digest | `:1677` 刪欄爆 TypeError | `:1655`（行為型） |
| S4 | `:1697` param 被吞 | `:1707` 加必填爆 TypeError | `:1692`（行為型） |
| S5 | `:1734` 字元集放寬 | `:1747` 長度收緊拒 `model-a` | `:1724`（行為型） |
| N1 | `:1782` 洩漏符號 | `:1795` import 期 raise | `:1768`（行為型） |
| N2 | `:1847` 外來包＋`:1867` 封鎖 I/O 見證（見 §3） | `:1855` 刪匯入致 NameError | `:1826`（行為型） |

## 3. P6／N2 補件證據

- P6（現 `:1509 test_p6_sensitivity_route_generation_policy_candidate`）：
  既有 `:1459` 只見證 text／turn；本測試補 route（soul-b/backend-b 指紋不同）、
  generation（max_tokens 128 vs 512 指紋不同）、policy（全 header vs 缺 header，
  `memory_policy` 不同且指紋不同）、candidate（僅末則 `memory_candidate` 翻轉即指紋不同）。
- N2（現 `:1867 test_n2_mut_loosen_blocked_io_witness`）：
  既有 `:1847` 只證外來包；本測試在 TEMP 副本注入 `import socket`／`import subprocess`／
  寫入式 `open`，`_hard_block_before_import` 與 `_load_mutant` 雙層皆拋
  `blocked mutant` AssertionError（正常版不攔＝對照綠）。

## 4. DRIFT 逐條 reason（前輪 `-rs` 實測原文，非合併歸因）

1. `test_27_symlink_escape_refused` →
   `tests\interfaces\test_harness_soul_context_adapter.py:739`：
   symlink creation needs privilege on this host（OSError 路徑，與 env 無關）。
2. `test_allow_real_data_root_marker_opts_out` →
   `tests\test_data_root_isolation.py:75`：
   Another test module leaked SOUL_OS_DATA_DIR into the pytest process at import time
   (no teardown)，clean-env 前提不成立（whitelist 自證邏輯，與 symlink 特權無關）。
   兩者根因分開，不得合併；皆與本票新檔無關（新檔零 skip 標記）。

## 5. 回歸差分

- 聚焦：`pytest -q tests/interfaces/test_ei1_acceptance_ratified.py` → **90 passed**
 （88＋2 新：`:1509` 正常測試＋`:1867` mut 測試）。
- `pytest -q tests/interfaces` → **5 failed, 484 passed, 1 skipped**：
  5 敗皆原有 `test_gap_*`（journal×3、runtime_port×2），與 SUMMARY §2 基線同；
  passed 較基線 482＋2＝484，對上新增數。
- `pytest -q tests/infra` → **1 failed, 318 passed**：僅 hygiene 未追蹤新檔
  （offender 恰本票新檔；`git add --dry-run` 三檔可加，合入即解；worker 無權 add）。
- Frozen 守衛 `test_no_frozen_contract_file_touched`：沿用 SUMMARY §4（1 passed，
  本輪未重跑；frozen 檔零碰，`git status` 無其他變動）。
- 全庫 `pytest -q tests` → **7 failed, 8370 passed, 33 skipped, 32 subtests passed**
 （952.88s）：7 個 FAILED nodeid 與 SUMMARY §5 逐條一致
  （hygiene 未追蹤新檔＋journal×3＋runtime_port×2＋`test_g2_default_data_root_unchanged`）；
  相對 SUMMARY 基線（7F／8368P／33S）差分恰為 **passed＋2、failed＋0**，
  ＋2 即本票新增（`:1509` 正常測試＋`:1867` mut 測試），總量 8408＋2＝8410 對上 ✓；
  `[SKIP-BASELINE] full-repo mode => FAIL` 同前（DRIFT＋2 同 §4 兩條）。
  先前 SUMMARY §5 的「passed −1 未決小差」為歷史記錄，不在本輪差分內重現（本輪差分乾淨）。

## 6. nodeid 差分（未達項，誠實明列）

repo 內無 `raw/full_suite.txt` 基線清單（`**/full_suite.txt` glob 實測零命中），
無法做新舊 nodeid 逐條差分。代替：本輪 7 個 FAILED nodeid 與 SUMMARY §5 名單逐條比對一致，
passed 差分＋2 恰為本票新增（§5）；不虛報全庫逐條差分。

## 7. 文件對齊

`docs/EI-1-ACCEPTANCE-RATIFIED.md` 四格：C1／I3 證據欄加三態 oracle 註記；
P6 證據欄加 `:1509` 四者敏感度；N2 的 M-LOOSEN 欄加 socket／subprocess／open 硬攔敘述、
證據欄加 `:1867`。其餘 26 格未動。

## 8. 正式計數口徑（沿用 SUMMARY，不重寫歷史）

SUMMARY 正式記錄維持 **7F／8368P／33S＋32 subtests，SKIP-BASELINE FAIL**；
本輪新數（聚焦 90、interfaces 484、infra 318＋1 預期紅、全庫 8370P／7F／33S）只記於此，
不回填 SUMMARY。對帳：全庫相對 SUMMARY 基線 passed＋2（即本票新增）、failed＋0，總量 8410 ✓。

## Blockers / 需主大腦裁決

1. hygiene 合入前恆紅（`git add` 即解，worker 無權）。
2. nodeid 基線缺失，逐條差分不可做（§6）。
3. `test_g2` 在強制 TEMP harness 下恆敗（環境耦合，見 SUMMARY §5，按原口徑不視為綠燈）。
4. 全庫重跑完成（952.88s，7F／8370P／33S＋32 subtests，差分乾淨，見 §5）。
