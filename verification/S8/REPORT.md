# S8 收尾驗收報告（LAN→gateway→cloud 路線交付）

- 工單：S8-CLOSEOUT-1（主大腦 Sol 已拍板，執行者照做、不重裁）
- 範圍：只動 `docs/EXTERNAL-INTERFACE-GUIDE.md`（補 §8＋修過期描述）、
  新增 `tests/external_gateway/test_s8_acceptance.py`、新增本報告。
  產品碼、正式配置（`soul-os-gateway` 下全部）、canonical
 （`logs/ENGINEERING_STATE.md`）一律未動。
- 解譯器口徑：倉內 `.venv/Scripts/python.exe -B`（Python 3.11.15）。

## 1. 交付內容

| # | 檔案 | 異動 |
|---|---|---|
| 1 | `docs/EXTERNAL-INTERFACE-GUIDE.md` | 頁首版本註記改為 S8（移除「不掛 gateway」過期描述）；§7 末點改為「閘道僅在受信 LAN 內提供服務」；新增 §8（埠 18765／rem・primary／精確 agent 映射／Bearer 操作／非串流限制／preset 逐 call 重讀／registry 啟動快照／LAN 明文風險／H4・M4 引用／未驗證項）。G1–G5 限制（§6）逐字保留。 |
| 2 | `tests/external_gateway/test_s8_acceptance.py` | 新增 13 項（ASGI 記憶體傳輸＋假 registry＋注入式假 carrier；拒絕路徑皆斷言 provider 零呼叫）。 |
| 3 | `verification/S8/REPORT.md` | 本報告。 |

## 2. 程式驗收（新增測試）

- `tests/external_gateway/test_s8_acceptance.py`：**13 passed**（`.venv -B`）。
- 覆蓋：chat 200（形狀：`chatcmpl-` 前綴、`usage` 全 0、`agent_rem` 綁定）、
  models 200 靜態表、401（缺 key／錯 key）、403（路由未授權）、
  404（未知 soul／未知 preset）、400（`stream:true` 拒絕，固定文案）、
  500（agent 錯配，無細節洩漏）、preset 換模免重啟、
  registry 磁碟改寫不生效→重建（重啟）生效、key 零外洩（回文＋日誌）。
- 拒絕路徑 7 條皆斷言假 carrier **零呼叫**。

## 3. 實機證據 H4／M4（引用既有，未補發請求）

- **H4**：Owner 直連 ollama cloud `model=deepseek-v4.1-flash:1017` 回 200
  （`chatcmpl-233` 前綴 id 族）。
- **M4**：`POST /rem/primary/v1/chat/completions` 回 200；無 key／錯 key 皆 401。
- 來源：Sol S8-CLOSEOUT-1 工單記述之既有證據；**repo 內未找到原始出處日誌**，
  原始出處待主腦合入 canonical 時補登。本次工單**未發起任何真實 LLM 請求**
  （新測試與探針皆為假 carrier／ASGI 記憶體傳輸）。
- 宣稱邊界：只稱 `rem/primary` 已實機驗證；**不擴稱五魂、不擴稱兩 provider**。
- 200 ≠ 人格／記憶持久化驗收（已寫入 §8.6）。

## 4. 能力限制（誠實登記）

- G1–G5 仍 BLOCKED（§6 逐字保留；五紅見 §6）。
- `mai／ruka／akane／mahiru` 實機通路未驗證；deepseek 直連 provider 實機未驗證。
- 受信 LAN 明文（無 TLS）——不得暴露到非受信網路（§8.5）。
- 首批非串流（`stream:true`→400）；無自啟／watchdog／schtasks（§8.7＋腳本註解）。

## 5. 守衛與回歸

- 閘道＋介面＋範圍守衛
  （`tests/external_gateway`＋`tests/interfaces`＋
  `tests/infra/test_ei1_scope_registration_guard.py`＋
  `tests/test_observability_completeness_1.py`）：
  **785 passed, 1 skipped**，僅 G1–G5 五紅（預期，見 §6）。
- 全庫回歸（`-q tests`，`.venv -B`，耗時 952.17s）：
  **8704 passed, 32 skipped, 32 subtests passed, 6 failed**，
  6 項失敗＝G1–G5 五紅（預期，見 §7）＋ hygiene 守衛 1 紅
  （唯一 offender 即本工單新檔，見下）。
- 新增 hygiene 守衛影響：`test_every_collectable_test_file_under_tests_is_git_tracked`
  唯一 offender 為 `tests/external_gateway/test_s8_acceptance.py`
 （單獨重跑已確認，别無其他）。在主腦合入（`git add`／commit）前為紅，
  屬預期中的待合入狀態，非程式回歸；合入後即綠。

## 6. 變異（隔離副本，執行期硬攔，不觸達生產）

探針：`$env:TEMP/s8_mutation_probe.py`（repo 外，一次性；socket.create_connection
硬攔；tmp 檔＋SENTINEL only；serve 腳本未匯入；uvicorn 未觸碰；data/** 未寫入）。

| 變異 | 做法 | 結果 |
|---|---|---|
| 控制 | 同 S8 斷言原文 | GREEN（401 成立；重讀成立） |
| M-AUTH（繞過認證） | `extract_bearer`＋`authenticate` 執行期強制走真 token | 指定斷言轉 **RED**（無 key 回 200，401 不成立） |
| M-PRESET（停用重讀） | loader 結果快取（首 call 後不再讀檔） | 指定斷言轉 **RED**（換模後仍舊模） |

結論：兩條守衛斷言皆會咬（control 綠／mutant 紅），非空轉守衛。

## 7. G1–G5 五紅（不得轉綠，實測仍紅）

- `tests/interfaces/test_journal.py::test_gap_backend_completed_unreachable` — RED
- `tests/interfaces/test_journal.py::test_gap_backend_dest_completion_rejected` — RED
- `tests/interfaces/test_journal.py::test_gap_cancel_absent` — RED
- `tests/interfaces/test_runtime_port.py::test_gap_no_side_effect_retry_still_uncertain` — RED
- `tests/interfaces/test_runtime_port.py::test_gap_decision_port_result_absent` — RED

## 8. 生命週期 CLI 對帳：N/A

- 本工單未改生命週期／昇華／投影任一模組（`git status` 差分僅 §1 三檔），
  故不跑 `scripts/eval_lifecycle_cli.py`。N/A 理由：無受影響面，對帳無意義；
  生命週期補證另案處理，不綁住 S8。

## 9. Frozen 差分

- Agency／TriggerEnvelope／InnerLifeEvent／四 handlers／SAGE 寫入／衰減門檻／
  must-stay-RED：全未動（差分僅手冊＋新測試＋本報告）。
- 行程安全：未殺行程、未重啟、未綁埠、未對生產發請求；
  背景全庫任務為 pytest 行程（待其自然結束，不做 `task_stop`）。
- `data/**`：零寫入。

## 10. 未驗證項／S8 CLOSED 登記條件

- 未驗證：§4 四魂＋deepseek 直連實機；H4／M4 原始出處補登（待主腦）。
- S8 CLOSED 可登記條件：新增測試全綠（✓ 13 passed）＋零新增回歸
  （✓ 全庫唯一新增紅為待合入 hygiene，非程式回歸）＋
  證據齊全（H4／M4 出處由主腦補登後 ✓）＋手冊過期描述已清（✓）。
