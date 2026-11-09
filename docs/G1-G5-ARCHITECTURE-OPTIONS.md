# G1–G5 架構收斂選項（GAP-DECISION-1）

- 前提：先讀 `docs/G1-G5-CONTRACT-DISPOSITION.md` 全文。本文件僅準備逐項決策，**不代選、不實作、不改碼**。
- 授權：既有擱置決定仍有效（`logs/ENGINEERING_STATE.md:10138`、處置文件原結論）。
- Frozen：`src/interfaces/journal.py`、`src/inner_life`、衰減門檻不得動；五紅測試不斷言、不轉綠、不弱化。
- 規則：B 選項只形成另案施工前提，不授權改碼；任何 B 的實作須另開 decision-complete 工單並經主腦＋Owner 明示批准。

五紅基線（本票驗收時確認仍紅、原因不變）：
`tests/interfaces/test_journal.py:571`（G1 行狀態恆 `transport_sent`）、`:581`（G2 `JournalError: bad dest`）、
`:589`（G3 `AttributeError: cancel`）、`tests/interfaces/test_runtime_port.py:398`（G4 重調仍 `UNCERTAIN` 且 `retry.calls == []`）、`:411`（G5 `ImportError: Decision`）。

---

## G1 — Journal 行 `completed`

| | A：從公開承諾排除 Journal completed | B：獨立持久 backend 終態帳本與復原契約 |
|---|---|---|
| 能力 | 公開契約寫明：Journal 行狀態終態為 `transport_sent`，`completed` 只存在於 `outcomes["memory"]`；完成證據以 `coordinator._apply_port_return`（`coordinator.py:1846`）的 `BackendRecord.state == "completed"` ＋ journal `generated` 行為準 | 新訂持久 backend 終態帳本（落盤 COMPLETED／FAILED／CANCELLED／UNKNOWN／RETRYABLE）＋重啟復原契約（啟動對帳、密封重建、retry 圍欄重建規則） |
| 代價 | 不具跨重啟終態：重啟後 `ctx._backend_ledger` 清空，已完成不可再證（處置文件共同殘余限制 §2，原樣接受） | 需新增持久格式、版本化、遷移與損毀處理；引入「重啟後終態可信度」新語義，須定義陳舊終態是否仍可作為釋放／重試依據 |
| 遷移／回退 | 無遷移；回退＝維持現狀（零差分） | 遷移：另案工單定義帳本 schema＋只讀對帳先行；回退：刪帳本讀取路徑、回落 A 語義（須另案工單） |
| Frozen 影響 | 無：不碰 `journal.py`、`inner_life`、衰減門檻 | 有：觸及持久化契約與 coordinator 證據語義，屬 Frozen 相鄰面，須主腦＋Owner 明示批准（本票不批准） |
| Owner 選項欄 | [ ] 選 A（維持現狀，接受終態不跨重啟） | [ ] 選 B（另案立項做持久帳本，本票只記錄意向） |

---

## G2 — Journal backend destination

| | A：明確不支援 Journal backend destination，採無址 `submit_backend` | B：新訂 backend outcome 儲存及權限契約，不借 memory 偽裝完成 |
|---|---|---|
| 能力 | 公開契約寫明：`record_outcome` 僅接受 `dest == "memory"`（`journal.py:399` `bad dest` 為預期拒絕）；backend 完成只走 `submit_backend(ctx, capability, decision, port)`（`coordinator.py:1889`），`Decision`（`backend_execution.py:50`）刻意無址 | 新訂獨立 backend outcome 儲存位置與寫入權限契約（誰可寫、寫什麼 schema、Journal 行關聯鍵），backend 完成不再寫入 `outcomes["memory"]` 偽裝 |
| 代價 | 任何經 Journal 記 backend outcome 的語義此路不通；同 G1，終態記憶體化、不可跨重啟恢復 | 需設計新儲存位＋權限矩陣＋舊 `outcomes["memory"]` 讀者的相容規則；誤設計會讓「完成證據」出現雙源（Journal vs 新儲存）分歧 |
| 遷移／回退 | 無遷移；回退＝維持現狀 | 遷移：另案工單先訂讀寫契約＋雙寫／唯讀驗證期；回退：停用新儲存寫入、回落 A（須另案工單） |
| Frozen 影響 | 無 | 有：觸及 outcome 儲存語義與權限，須主腦＋Owner 明示批准（本票不批准） |
| Owner 選項欄 | [ ] 選 A（維持 `bad dest` 拒絕＋無址提交） | [ ] 選 B（另案立項，本票只記錄意向） |

---

## G3 — Journal cancel

| | A：不提供 `Journal.cancel`，僅公開 coordinator 的有條件取消 | B：新訂持久取消意圖、退出證據與重啟對帳契約；取消不得等同副作用回滾 |
|---|---|---|
| 能力 | 公開契約寫明：`Journal` 無 `cancel`（全文件無 `def cancel` 為預期）；取消唯一入口為 `cancel_backend`（`coordinator.py:1972`）：先封存再取消，未提交→`CANCELLED`，已提交須 `port.confirm_exited()` 真值，否則 `UNKNOWN` | 新訂持久取消意圖（誰、何時、對哪個 backend record）、退出證據（`confirm_exited` 結果的落盤形式）與重啟對帳契約（重啟後 sealed／cancelled 如何重建、不誤釋放） |
| 代價 | Journal 側永遠沒有取消語義；取消證據只活在記憶體帳本，重啟後已取消／已密封區分隨之遺失（fail-closed，呼叫方不得假設可恢復） | 需定義「取消意圖」與「退出已證」的落盤邊界；必須明文：取消 ≠ 副作用回滾（已發生的外部副作用不因取消而撤銷） |
| 遷移／回退 | 無遷移；回退＝維持現狀 | 遷移：另案工單先訂意圖＋證據 schema＋重啟對帳唯讀演練；回退：停用持久意圖讀取、回落 A（須另案工單） |
| Frozen 影響 | 無 | 有：觸及取消／密封／釋放語義，須主腦＋Owner 明示批准（本票不批准） |
| Owner 選項欄 | [ ] 選 A（維持無 `cancel`＋有條件取消） | [ ] 選 B（另案立項，本票只記錄意向） |

---

## G4 — UNCERTAIN 重試

| | A：明禁舊 `submit` 重試，僅顯式無副作用 `PortError(False)` 可走 coordinator 重試 | B：新訂持久 attempt／去重與無副作用證明；UNKNOWN 仍不得盲重送 |
|---|---|---|
| 能力 | 公開契約寫明：舊 `submit`（`runtime_port.py:102`）一切一般例外→`UNCERTAIN`，同 `request_id` 重調返回已存 kind、不重調 port（`retry.calls == []` 為預期）；唯一可重試路徑為 `_apply_port_error`（`coordinator.py:1872`）僅 `PortError(ambiguous=False)`→`RETRYABLE`，`retry_backend`（`:1922`）重放已存 `decision`／`port`＋`attempt` 圍欄 | 新訂持久 `attempt` 計數／去重鍵與「無副作用證明」的落盤形式（何謂可重放的 stored call、stale attempt 判定、併發恰一次規則跨重啟如何保持） |
| 代價 | 舊 `submit` 原位不動（保持紅）；`attempt` 圍欄與 stored call 皆為記憶體態，重啟後不可恢復 | 需證明「重放同一 stored call」跨重啟仍安全；任何放寬「一般例外也可重試」即破線——B 必須保留：`UNKNOWN`（歧義 `PortError` 或任何一般例外）仍不得盲重送 |
| 遷移／回退 | 無遷移；回退＝維持現狀 | 遷移：另案工單先訂去重鍵＋圍欄持久化＋唯讀對帳；回退：停用持久圍欄、回落記憶體圍欄（須另案工單） |
| Frozen 影響 | 無 | 有：觸及重試／去重安全語義，須主腦＋Owner 明示批准（本票不批准） |
| Owner 選項欄 | [ ] 選 A（維持舊 `submit` 不重試＋僅 `PortError(False)` 可重試） | [ ] 選 B（另案立項，本票只記錄意向） |

---

## G5 — 舊模組型別

| | A：公開型別只由 `backend_execution` 匯入 | B：新訂版本化 facade／遷移契約，保留舊模組缺省；不得偷偷 re-export 消紅 |
|---|---|---|
| 能力 | 公開契約寫明：`Decision`（`:50`）、`PortResult`（`:69`）、`PortError`（`:93`）只從 `src/interfaces/backend_execution.py` 匯入；`src/interfaces/runtime_port.py` 僅含 `FakeCancelled`／`FakeClock`／`SubmitResult`／`FakeRuntime`／`submit`，缺三型別為預期（`ImportError` 為預期拒絕） | 新訂版本化 facade（例如 `interfaces/vN` 匯出面）＋遷移契約（舊 import 路徑何時轉址、轉址期雙路並存規則、移除時間表），舊模組在遷移完成前保持缺省 |
| 代價 | 舊 import 路徑永遠報 `ImportError`；呼叫方必須改 import 源 | 需維護版本對照表與棄用期；任何「在 `runtime_port` 加 `from backend_execution import Decision` 消紅」的偷渡 re-export 均為破線（判準放寬，禁止） |
| 遷移／回退 | 無遷移；回退＝維持現狀 | 遷移：另案工單先訂 facade 版本號＋並存期＋呼叫方改寫清單；回退：移除 facade 轉址、回落 A（須另案工單） |
| Frozen 影響 | 無 | 有：觸及公開匯出面，須主腦＋Owner 明示批准（本票不批准） |
| Owner 選項欄 | [ ] 選 A（維持舊模組缺省，呼叫方改 import 源） | [ ] 選 B（另案立項版本化 facade，本票只記錄意向） |

---

## 決策待辦清單（主腦／Owner 逐項勾選）

- [ ] G1：A（接受終態不跨重啟）／ B（另案持久帳本）—— Owner 圈選一項。
- [ ] G2：A（維持 `bad dest`＋無址提交）／ B（另案 outcome 儲存契約）—— Owner 圈選一項。
- [ ] G3：A（維持無 `cancel`）／ B（另案持久取消意圖）—— Owner 圈選一項。
- [ ] G4：A（維持舊 `submit` 不重試）／ B（另案持久 attempt／去重）—— Owner 圈選一項。
- [ ] G5：A（維持舊模組缺省）／ B（另案版本化 facade）—— Owner 圈選一項。
- 任選 B 者：由主腦另開 decision-complete 工單（含 Frozen 影響評估＋Owner 明示批准欄），本票不授權動碼。
