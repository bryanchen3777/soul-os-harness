# EI-1-ACCEPTANCE-RATIFY-1 FUP-3 補證記錄

- 工單：EI-1-ACCEPTANCE-RATIFY-1-FUP-3（主腦點名四缺件精準補）
- 適用 commit：`f19aac1`；解譯器：`.venv` Python 3.11（計數口徑唯一準）
- 每次 pytest 前新建 TEMP 作 `SOUL_OS_DATA_DIR`
- 改動範圍（僅此三檔）：`tests/interfaces/test_ei1_acceptance_ratified.py`、
  `docs/EI-1-ACCEPTANCE-RATIFIED.md`、本檔新增；
  `verification/.../FUP-2.md` 與 `SUMMARY.md` 維持原記錄不動（歷史見證凍結）。
- `git status --short` 全程僅上述 `??`；`data/` 零變動；未 commit／add／合入。

## 1. Oracle 全面化（Sol 點名項 A，前後對比）

除已成立的 C1 `:281`、I3 `:1051` 外，其餘 16 處「錯誤結果應成立」反向斷言
（只證變異生效、未證同一 oracle 殺死）全部改為**同一驗收 callable 三態**：
正常綠＋mutant 跑同一 oracle 必須轉紅（`pytest.raises(AssertionError)` 捕獲 oracle
的預期失敗，或放寬型的 `DID NOT RAISE`、收緊型的 status／code 誤拒）＋失敗原因
（診斷行，明確標註「非殺死證據」，只說明 mutant 行為，不再作為殺死依據）。
隔離包比較一律經 `.value`／屬性（status／code）正規化，不靠類別錯配製造紅。

| 測試 | 修正前（反向斷言） | 修正後（同一 callable） |
|---|---|---|
| C7-loosen | `:624` 斷言 mutant 末則 `!= CURRENT_USER` | oracle `_oracle_last_is_current_user`：正常綠＋mutant `pytest.raises(AssertionError)` 紅＋`external_history` 診斷 |
| C7-tighten | `:642` 斷言 mutant 歷史段 `== external_instruction` | oracle `_oracle_history_stays_history`：正常綠＋mutant 紅＋升格診斷 |
| G5-loosen | `:893–894` 斷言 mutant 回傳首 route | 改用共享 `_loosen_kill`（與 C2/C3/C5/C6 同一 callable，expect `(404, route_not_found)`）＋誤綁診斷 |
| I1-loosen | 已三態，診斷行註記不清 | 診斷行改標「失敗原因（非殺死證據）」 |
| P3-loosen | `:1331` 斷言 mutant `== interactive_candidate` | oracle `_oracle_utility_no_memory`（policy＋全段 False）：正常綠＋mutant 紅＋誤授診斷 |
| P3-tighten×2 | `:1347`／`:1358` 斷言 mutant 降級值 | oracle `_oracle_interactive_candidate`／`_oracle_last_marked`：正常綠＋mutant 紅＋降級／遺失診斷 |
| P4-loosen／tighten | `:1385` 斷言 `(9,9)`；`:1401` 斷言 `!= 1` | oracle `_oracle_single_round`／`_oracle_has_attempt`：正常綠＋mutant 紅＋`(9,9)`／`0` 診斷 |
| P5-loosen／tighten | `:1436` 斷言碰撞成立；`:1453` 斷言分裂成立 | oracle `_oracle_namespace_splits`／`_oracle_same_op_same_key`：正常綠＋mutant 紅＋碰撞／分裂診斷 |
| P6-loosen／tighten | `:1488` 斷言相等；`:1506` 斷言不等 | oracle `_oracle_turn_sensitive`／`_oracle_request_id_isolated`：正常綠＋mutant 紅＋遺失／破線診斷 |
| S1-loosen | `:1582` 斷言 `not (==0.5 and in-range)` | oracle `_oracle_default_temp_in_range`（以函式為參的同一 callable）：正常綠＋mutant 紅＋`2.5` 診斷 |
| S2-loosen | `:1628–1631` 斷言改寫成功 | oracle `_oracle_frozen`（內層 `pytest.raises`）：正常綠＋mutant `DID NOT RAISE` 紅＋改寫成功診斷 |
| S3-loosen | `:1674` 斷言 digest 在 repr 內 | oracle `_oracle_digest_hidden`：正常綠＋mutant 紅＋外洩診斷 |
| S4-loosen | `:1704` 斷言 `param is None` | oracle `_oracle_param_kept`：正常綠＋mutant 紅＋遺失診斷 |
| S5-loosen／tighten | `:1744`／`:1762` 斷言 mutant 判定值 | oracle `_oracle_id_strict`／`_oracle_model_len`（以檢查函式為參）：正常綠＋mutant 紅＋放寬／誤拒診斷 |
| N1-loosen | `:1792` 斷言 `hasattr` | oracle `_oracle_no_leak`：正常綠＋mutant 紅＋`InterfaceError` 洩漏診斷 |

其餘 loosen（G1/G2×2/G3/G4/I4/P2×2/C4/C6×2…）已是 `_loosen_kill` 或同形三態，
tighten（try/except＋`getattr status/code`）已是同一呼叫正常綠＋誤拒紅，
I2-loosen（`None`→TypeError 而非 401）屬真實行為分歧（oracle 失敗模式即 TypeError，
非類別錯配），P1-loosen（404 而非 401）已按 status 正規化，皆保留並見下表。

### 三態表（全 60 mut 測試一體適用，FUP-2 表＋FUP-3 增量）

| 態 | 正常版 | mutant 版 | 判定 |
|---|---|---|---|
| 放寬型 | 同一拒絕／存在斷言通過（綠） | 誤接納 ⇒ `DID NOT RAISE`（紅）；源碼性質 ⇒ 同一存在性斷言拋 AssertionError（紅） | 殺死＝紅出現 |
| 收緊型 | 同一 oracle 合法輸入通過（綠） | 誤拒絕 ⇒ status／code／TypeError／NameError 見證（紅） | 殺死＝紅出現 |
| 行為型（C7/P3/P4/P5/P6/S/N1-loosen） | 同一 oracle 斷言通過（綠） | 同一 oracle 斷言拋 AssertionError（紅，`pytest.raises` 捕獲）＋失敗原因診斷行 | 殺死＝紅出現 |

FUP-3 增量：mut 測試 59→**60**（新增 `test_p6_mut_loosen_sort_keys_unstable`；
`test_p6_sensitivity_*` 與 `test_n2_normal_import_under_blocked_io` 為正常見證，
不計 mut）。

## 2. P6／N2 補件證據（Sol 點名項 B／C）

- P6（`test_p6_sensitivity_route_generation_policy_candidate` 重做）：
  FUP-2 版在 `:1533` 同時改變識別欄＋policy＋candidate，不能獨證 policy。
  本版全部改用 `dataclasses.replace` 單欄獨證：route 只動 `soul_alias`、
  generation 只動 `max_tokens`、policy 只動 `memory_policy`
  （`INTERACTIVE_CANDIDATE`→`EPHEMERAL`）、candidate 只翻末則
  `memory_candidate`，其餘欄全同，各自指紋不同。
- P6 sort_keys（`test_p6_mut_loosen_sort_keys_unstable` 新增，文件舊宣稱無實作→選補做）：
  共享存在性 oracle `_oracle_serialization_stable`（正常綠／mutant 紅）＋
  mutant 可載入可運行見證（非稻草人；同次建構插入序固定故功能輸出相同，
  殺死純由 oracle 承載，與 I3 同構）。途中修 1 處變異寫法：
  行內 `#` 註解吞掉後續參數致 SyntaxError（與 SUMMARY 記之 C5 同款陷阱），
  去註解即過。
- N2（`test_n2_normal_import_under_blocked_io` 新增；`:1826` 原測試不動）：
  封鎖 socket／subprocess／寫入式 open／`os.environ` 讀取後，正常六模組
  purge 重匯入成功＋全鏈（validate→parse→classify→authenticate→authorize→
  admit→fingerprint）成功。讀取向 open 不封（它是匯入機制本身）；
  `os.open` 等 os 層寫入不在 mutant 威脅模型內（源碼級注入由 AST 門擋），不封。

## 3. DRIFT（沿用 FUP-2 §4，未重跑，全庫見 §5）

（沿用 FUP-2 兩條 reason；本輪聚焦／interfaces／infra 皆為 observe-only 非 full-repo 模式。）

## 4. 回歸差分

- 聚焦：`pytest -q tests/interfaces/test_ei1_acceptance_ratified.py` → **92 passed**
  （90＋2 新：`:sort_keys` mut 測試＋`:blocked_io` 正常測試）。
- `pytest -q tests/interfaces` → **5 failed, 486 passed, 1 skipped**：
  5 敗為原有 `test_gap_*`（journal×3、runtime_port×2），與 SUMMARY §2 基線同；
  passed 較 FUP-2（484）＋2＝486，對上新增數。
- `pytest -q tests/infra` → **1 failed, 318 passed**：僅 hygiene 未追蹤新檔
  （offender 恰本票新檔；`git add --dry-run` 可加，合入即解；worker 無權 add）。
- Frozen 守衛：沿用（frozen 檔零碰，`git status` 無其他變動）。
- 全庫 `pytest -q tests` → **7 failed, 8372 passed, 33 skipped, 32 subtests passed**
  （實測，945.60s）：7 個 FAILED nodeid 為 hygiene（offender 恰本票新檔）＋
  journal×3＋runtime_port×2＋`test_g2`，與 SUMMARY §5 名單逐條比對一致；
  相對 SUMMARY 基線（7F／8368P／33S）差分恰為 **passed＋4、failed＋0**
  （＋4＝FUP-2 的 2＋本票的 2），總量 8412 對上 ✓；
  `[SKIP-BASELINE] full-repo mode => FAIL` 同前（DRIFT＋2 同 FUP-2 §4 兩條）。

## 5. nodeid 差分（未達項，誠實明列）

repo 內無 `raw/full_suite.txt` 基線清單（`**/full_suite.txt` glob 實測零命中），
無法做新舊 nodeid 逐條差分。代替：本輪 7 個 FAILED nodeid 須與 SUMMARY §5 名單
逐條比對一致，passed 差分＋4 恰為兩輪新增（FUP-2 的 2＋本票的 2）；不虛報全庫逐條差分。
（依主腦裁決：nodeid 基線缺失保留未驗證，不得以計數替代；i63 等計數細節以實測為準。）

## 6. 文件對齊

`docs/EI-1-ACCEPTANCE-RATIFIED.md` 三處：
I3 M-LOOSEN 格「AST 釘選」→「字串存在性 oracle」（選主腦建議前者，直接改文件）；
P6 M-LOOSEN 格加 sort_keys 變異、M-TIGHTEN 格「AST 釘選」→存在性 oracle
`_oracle_serialization_stable`、證據格加兩新測試並註明 replace 單欄獨證；
N2 條款格加封鎖見證說明、證據格加 `test_n2_normal_import_under_blocked_io`
（另順手補 N2 證據格缺失的右反引號，無語義變更）。其餘格未動。

## 7. 正式計數口徑（沿用，不重寫歷史）

SUMMARY 正式記錄維持 **7F／8368P／33S＋32 subtests，SKIP-BASELINE FAIL**；
FUP-2 新數記於 FUP-2；本輪新數（聚焦 92、interfaces 486、infra 318＋1 預期紅、
全庫 8372P／7F／33S）只記於此，不回填 SUMMARY／FUP-2。

## 8. hygiene 追蹤（待追蹤實測）

五檔（測試檔、文件檔、FUP-2.md、SUMMARY.md、本檔）`git check-ignore`
無忽略（exit 1，實測；本檔已補查，exit 1），`git add --dry-run` 可加（exit 0）。**hygiene 待追蹤實測**
（dry-run 不算通過，合入追蹤後須實測轉綠）。

## Blockers / 需主大腦裁決

1. hygiene 合入前恆紅（`git add` 即解，worker 無權）。
2. nodeid 基線缺失，逐條差分不可做（§5）。
3. `test_g2` 在強制 TEMP harness 下恆敗（環境耦合，見 SUMMARY §5，按原口徑不視為綠燈）。
4. 全庫實測已完成（7F／8372P／33S／32 subtests，945.60s；FAILED 名單與
   SUMMARY §5 逐條一致，passed 差分＋4 對上；§4 實測數已填入）。
