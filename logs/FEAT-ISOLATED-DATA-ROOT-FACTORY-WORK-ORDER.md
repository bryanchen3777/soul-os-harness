# 工單：FEAT-ISOLATED-DATA-ROOT-FACTORY（受控隔離環境上下文工廠）

> 開立：2026-10-24 15:15 EDT（主大腦；落實 **Owner 2026-10-24 14:2x 裁決 2**：「允許在 `src/paths.py` 或內部配置層提供**顯式的隔離環境上下文工廠**（如 `isolated_data_root(tmp_path)` 上下文管理器），**由內部統一置換**，而非由外部隨意傳參注入」）
> 依賴：CAMPAIGN-B E2E 封頂（`7224c4a`）已合入 main（它揭露了「測試端必須到處覆寫 10 個 import 期資料根常數」的脆弱性）
> 排序：**必須排在 FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE 之後** —— 兩者都會改 `tests/test_observability_completeness_1.py` 的 `allowed` 清單，並行會撞同一檔

---

## 0. 出發點（已量現況，不猜）

| 事實 | 值 | 來源 |
|---|---|---|
| P5-① 的生產唯讀面**路徑不可注入** | `format_working_state_lines_production(agent_id, *, max_entries, max_chars)`（簽章**不含** `store_dir`／`path`） | `85b0ab7` |
| P5-② 的生產寫入面同樣**路徑不可注入** | `drive_loop_async_production(agent_id, *, thinker, now)` | `7facd88` |
| 沙盒隔離守門**拒絕生產路徑** | `check_store_dir()` 對生產資料根／repo `data/`／非 temp 三重 fail-closed | `tension_closure_loop.py:248-275` |
| ⇒ **設計張力** | 「生產唯讀面」與「沙盒寫入面」**在路徑上互斥** ⇒ 沙盒測試無法直接用生產讀取面讀沙盒寫入面的產物 | E2E worker 回報（`7224c4a`） |
| E2E 現況做法 | 測試內**執行期覆寫 10 個 import 期資料根常數**（`src.paths.data_root` ＋ `proxy.INNER_LIFE_DATA_DIR`／`CONV_DIR`／`_GROUP_FILE` ＋ `emotion/memory.store DB_PATH` ＋ `tts_toggle` 2 項 ＋ `diary DEFAULT_DIARY_ROOT` ＋ `tension_closure_loop._production_data_root`） | E2E worker 回報；主大腦 14:47 實測 8 passed / 1.95s |
| ⇒ **脆弱點** | ① 散落多處、易漏；② 漏一個就會讀到生產或寫到生產；③ 與測試執行順序相關；④ **若未來新增一個 import 期資料根常數，E2E 會靜默失效**（不會報錯，只會讀到生產） | 主大腦裁定 |
| `src/paths.py` 的資料根是**行程期快取單例** | `data_root()` 的結果被快取 ⇒ 改環境變數不會立刻生效 ⇒ 必須有**明確的失效／重置**機制 | 主大腦實測 |
| 🔺 **隔離機制早就存在（不得另造平行機制）** | `tests/conftest.py` 已有 `_isolate_soul_os_data_root` autouse fixture：設 `SOUL_OS_DATA_DIR` → 呼叫 `reset_data_root()` → 退出時 `env_patch.undo()` ＋ 再次 `reset_data_root()`；並有 **opt-out 標記** `@pytest.mark.allow_real_data_root`。**現況：沒有任何測試使用該 opt-out** | 主大腦 2026-10-24 15:11 實讀 `tests/conftest.py:50-120` ＋ 全庫 grep |
| ⇒ **真正的缺口** | 既有 fixture **只覆寫「根」**（靠 env ＋ 重置），**沒覆寫「import 期已擷取根」的衍生常數**（那 10 項）⇒ E2E 才不得不自行 monkeypatch ⇒ **本票應擴充既有機制**，由 `src/paths.py` 提供底層工廠、`conftest` 沿用它，**不是**在測試端再造一套 | 主大腦裁定 |

---

## 1. 目標

提供**單一入口、內部統一置換、退出必還原**的隔離環境上下文工廠，取代測試端到處 monkeypatch 全域常數的做法；**同時把生產的防注入契約完整保住**。

**為什麼**：現在的沙盒隔離靠「測試端手動覆寫 10 個常數」⇒ 每多一個 import 期資料根常數，就多一個**能靜默讀寫生產**的缺口；而防注入契約（路徑不可注入）**不該**為了測試便利而被放寬。

---

## 2. 做法（決策已定，執行者照做）

### 2.1 API 形狀（**決策已定**）

在 `src/paths.py` 提供：

```python
@contextlib.contextmanager
def isolated_data_root(root: Any) -> Iterator[Any]:
    """（本票新增）受控隔離環境：把**所有**資料根衍生常數統一置換到 ``root``，退出時還原。

    🔴 **僅限測試／沙盒**：啟用條件為「存在 ``PYTEST_CURRENT_TEST`` 環境變數」
    ⇒ **生產行程無法啟用**（生產沒有這個變數）⇒ 不構成後門。
    🔴 **退出必還原**：含例外路徑（``finally``）⇒ 例外也不會洩漏。
    🔴 **可巢狀**：進入時保存當前值、退出時還原成**進入前**的值 ⇒ 巢狀語意正確。
    🔴 **不改變防注入契約**：`format_working_state_lines_production()`／
    `drive_loop_async_production()` 的「路徑不可注入」語意**不變**；`check_store_dir()` **一行不改**。
    """
```

**設計選擇（worker 可在以下兩者中擇一，但必須滿足 §2.2 的全部硬性要求）**：
- **設計甲（登錄制）**：`src/paths.py` 提供一個「資料根衍生常數登錄表」，各模組在 import 期**主動登錄**自己的衍生常數（單行註冊）⇒ 工廠統一置換。
- **設計乙（延遲解析制）**：把各模組的 import 期常數改成**呼叫期解析**（例如 property／函式）⇒ 工廠只需切換根節點。

⚠️ 設計甲／乙的選擇**由 worker 依實測決定**，但必須在報告中說明理由，並**量化受影響的模組數**。

### 2.2 硬性要求（**全部必須成立**）

1. 🔴 **僅限測試／沙盒**：非測試情境（無 `PYTEST_CURRENT_TEST`）**必須拒絕啟用**並拋出明確例外 ⇒ **不得**成為生產可用的第二條路徑。
2. 🔴 **退出必還原**：正常返回、**例外**、`return`、多層巢狀，四種路徑都要把**每一個**被置換的常數還原成進入前的值。
3. 🔴 **完整覆蓋**：所有「在 import 期擷取資料根」的常數都必須被置換 ⇒ **以盤點結果為準**（E2E worker 已列 10 項；你必須自行**重新盤點全庫**並在報告中列出清單與數量）。**漏一個就是一個能靜默讀寫生產的缺口。**
4. 🔴 **不改生產語意**：`check_store_dir()` 一行不改；`format_working_state_lines_production()`／`drive_loop_async_production()` 的簽章不變。
5. 🔴 **不得新增持久化／schema／計時器／背景 task／網路呼叫**；不得引入新的全域可變狀態以外的機制。
6. 🔴 **不得**把 `data_root()` 的快取語意改掉（仍可快取，但**必須提供明確的重置路徑**供工廠使用）。
7. 🔴 測試端**不得**再手動 monkeypatch 那 10 個常數 ⇒ **本票必須把 E2E 測試改成使用 `isolated_data_root()`**，並證明它仍然 8 筆全綠。
8. 🔺 **擴充既有隔離機制、不得另造平行機制**：`tests/conftest.py::_isolate_soul_os_data_root` **已經存在**（設 env ＋ `reset_data_root()` ＋ 退出還原 ＋ `allow_real_data_root` opt-out）。⇒ 本票的工廠是**底層能力**（在 `src/paths.py`），而 **conftest 的既有 fixture 應沿用它**（或由它取代既有 fixture 的實作）⇒ **全庫只應有一套隔離機制**。⇒ 報告必須說明：既有 fixture 與新工廠的**分工**、以及**擴充後 opt-out 的使用狀況**（**現況：無任何測試使用** ⇒ 擴充後應維持，或明確記錄誰用、為什麼）。

### 2.3 邊界（硬性）
- 🔴 不得改 `src/llm/proxy.py` 的**既有行為**（若設計甲需要加一行「註冊」，**只允許新增註冊那一行**；若設計乙需要把常數改成 property，**不得改動其語意**）。⚠️ proxy.py 是主對話路徑 ⇒ **觸及面必須最小**，且必須逐項列出改了哪些行與為什麼。
- 🔴 不得動 11 個 `frozen_dirs` 檔。
- 🔴 0 真實 LLM、0 網路、**0 生產寫入**；不得重啟服務、不得殺行程、不得部署。
- 🔴 不得 `git add`／`git commit`／`git push`（**絕對禁止 `git add .`**）。

---

## 3. 守衛（新增檔 `tests/infra/test_isolated_data_root_guard.py`，**恰 8 筆** I1～I8）

| # | 內容 |
|---|---|
| I1 | **僅限測試**：無 `PYTEST_CURRENT_TEST` 時 `isolated_data_root()` **必須拋例外**（⇒ 不構成生產後門） |
| I2 | **完整覆蓋**：盤點清單中的**每一個** import 期資料根常數，進入上下文後**都被置換**（逐項斷言） |
| I3 | **退出必還原**：正常退出後，每個常數**逐位元等於**進入前的值 |
| I4 | **例外路徑必還原**：上下文內拋例外後，常數**仍全部還原** |
| I5 | **巢狀正確**：巢狀兩層，內層退出後回到**外層**的值（不是原始值） |
| I6 | **快取失效**：上下文內 `data_root()` 回傳新根；退出後回傳**原根**（行程期快取單例必須被正確重置） |
| I7 | **防注入契約未動**：`check_store_dir()` 對生產路徑**仍拒絕**；`format_working_state_lines_production()`／`drive_loop_async_production()` 的簽章**不含路徑參數** |
| I8 | **E2E 改用它**：E2E 測試檔**不再 monkeypatch** 那 10 個常數，而是使用 `isolated_data_root()` ⇒ 兩檔一起跑仍全綠 |

### 變異實驗（**每條必須轉紅**；ABORT 與意外全綠同等嚴重）

| # | 變異 | 應轉紅 |
|---|---|---|
| N1 | 移除「僅限測試」的啟用條件 | I1 |
| N2 | 漏掉盤點清單中的一個常數 | I2 |
| N3 | 移除 `finally` 還原（或只在正常路徑還原） | I3／I4 |
| N4 | 巢狀時還原成原始值而非外層值 | I5 |
| N5 | 不重置 `data_root()` 的快取 | I6 |
| N6 | 放寬 `check_store_dir()` | I7 |
| N7 | 工廠不置換常數（空操作） | I2／I8 |

⚠️ 變異落點分散在 `src/paths.py`（＋可能的各模組註冊行）⇒ harness 必須**逐檔複製**到沙盒後再變異，**repo/worktree 檔全程不寫入**；baseline 必須是**整個新守衛檔全綠**（8 筆）**且** E2E 8 筆＋既有 inner_life 守衛仍全綠；收尾必印被測檔 sha256 與初始值相同。

---

## 4. 驗收（完成的定義）
1. `git diff --name-only HEAD` **恰好 5 檔**：`src/paths.py`（工廠）、`tests/inner_life/test_campaign_b_e2e_closed_loop.py`（改用工廠）、`tests/infra/test_isolated_data_root_guard.py`（新增）、`tests/test_observability_completeness_1.py`（**`allowed` 逐票更新**：新增 `src/paths.py`）、`logs/ENGINEERING_STATE.md`（追加條目）。多一檔少一檔都**停止並回報**。
2. 新守衛 **8 筆全綠**；變異 **N1～N7 全數轉紅**；**E2E 8 筆仍全綠**且**已不再手動 monkeypatch 那 10 個常數**（報告須附「改前 vs 改後」的覆寫數對照）。
3. 全庫回歸：期望＝**當下 HEAD 的基線**（🔴 **基線必須在合入當下重算**，不得沿用工單寫死的數字；本票**淨新增 8 筆**守衛、**改寫** E2E 檔但**不增減**其測試數）。引用數字必須註明解譯器是 repo 的 `.venv`。
4. `logs/ENGINEERING_STATE.md` 追加條目必須寫明：① **生產防注入契約完整保留**（`check_store_dir()` 一行未改、路徑不可注入語意不變）② 工廠**僅限測試**（非測試情境拒絕啟用）③ **完整覆蓋的盤點清單與數量** ④ 巢狀／例外／快取三種語意的證明 ⑤ **本票不改變任何生產行為** ⑥ 守衛 8 條 ＋ 變異 N1～N7 ⑦ 誠實聲明：**本票是測試基礎設施，不是功能**；它讓未來的沙盒測試不再需要散落覆寫全域常數。
5. 🔴 **生產零寫入證據**：執行前後生產 `data/**` 的**受影響路徑**位元組對帳（**不可**比對整棵樹——線上服務會並行寫 `data/inner_life/trace.jsonl` ⇒ 整樹比對必然 flaky）。

## 5. 測試
- 定向：`pytest -q tests/infra/test_isolated_data_root_guard.py tests/inner_life/test_campaign_b_e2e_closed_loop.py` ⇒ 預期 **16 passed / 0 failed**（8 新 ＋ 8 E2E）。
- 既有守衛不得被削弱：內心生活全套守衛（Phase 1–4 70 ＋ P5-① 15 ＋ P5-② 12 ＋ 判定器 6 ＋ E2E 8 ＝ 111 筆，以你實際量到的為準）。
- 回歸：`.venv\Scripts\python.exe -m pytest -q tests`。
- 🔴 **回歸的失敗數必須以 pytest 的 returncode 為主閘門**；失敗數只從 pytest 的**耗時摘要行**取，**找不到 "failed" token 一律當 0**。
- 🔴 測試**不得**啟動伺服器、**不得**綁 `:8000`／`:8765-8767`、**不得**對生產發真實請求；`tests/**` 禁用 `subprocess`／`os.spawn*`／`os.system`／`os.popen`。

## 6. 不做（Out of Scope）
- **不**改 `check_store_dir()`；**不**放寬任何防注入契約；**不**新增生產可用的資料根切換路徑。
- **不**改 `src/llm/proxy.py` 的既有行為（只允許設計所需的最小註冊／延遲解析行，且須逐項列出）。
- **不**動 11 個 `frozen_dirs` 檔。
- **不**寫 `data/**`、不重啟、不部署、不打真實 LLM、不連網。
- 不 commit／push／git add（由主大腦合入時處理）。

## 7. Frozen Contract 注意
11 檔永久紅線：`src/agency/` 5 檔、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`。本票不觸碰。

## 8. 回報格式
- 5 檔範圍 ＋ numstat。
- **盤點清單**（逐項：模組、屬性名、型別、原值取得方式）＋ 總數 ⇒ **這是本票最重要的交付**。
- 設計甲／乙的選擇與理由 ＋ 受影響模組數 ＋ **觸及面最小性**的說明。
- 新守衛 8 筆 ＋ 變異 N1～N7 逐條 ＋ E2E 8 筆。
- **「改前 vs 改後」的覆寫數對照**（10 個手動 monkeypatch → N 個）＋ 兩種測試順序各跑一次的結果。
- 生產零寫入證據（受影響路徑的位元組對帳）。
- `logs/ENGINEERING_STATE.md` 追加條目內容摘要。
- 未做與明文排除。
- 若遇工單內部矛盾或缺資訊：**標示並附理由回報**，不要自行改選解法。
