# SOUL-REM-01 — Rem Soul 壓縮與語境理解

2026-10-07 · 依據工單 `SOUL-REM-01`（IMPLEMENTATION + REGRESSION）
原始交叉實驗：`tests/fixtures/soul_rem/experiment_2026-10-07_REPORT.md`（3 Soul × 3 模型）

---

## 1. 結論先講

Soul 定義從 **54,217 B 降到 26,792 B（−50.6%）**，12 段 Canon Memory **逐字未改**
（已升級成 byte-level regression），表達指紋全數保留，pseudo-runtime 規則清零，
並新增了自我指涉、替代 referent、「不知道」邊界、歷史台詞辨識四段能力定義。

**行為層 30 格 regression 已執行（Owner 授權 2026-10-07）**：

```
gate = PASS    0 regression / 0 紅線 / 0 C 缺資料（2 格基線缺資料）
C：10 格 = 9 PASS + 1 REVIEW，0 FAIL
```

結論：**壓縮沒有造成 character regression，C 版可 freeze。**

驗收判準已由 Owner 鎖定為 **contextual self-reference**（非典故逐字辨識），
見 `docs/SOUL-SELF-REFERENCE-CRITERIA.md`。

仍未關閉的兩項（見 §9）：記憶寫入降頻的後續效應、以及 live DSH 測試隔離。

---

## 2. 三個版本

| 版本 | 位元組 | 字元 | 行數 | 來源 |
|---|---|---|---|---|
| A（原始） | 54,217 | 24,366 | 988 | `tests/fixtures/soul_rem/soul_A_full_54kb.md` |
| B（實驗 trim） | 8,962 | 3,397 | 88 | `tests/fixtures/soul_rem/soul_B_trim_9kb.md` |
| C（新版） | 26,792 | 10,642 | 327 | `personas/agent_rem.md` |

> A/B 凍結為 fixture 時與原始檔 sha256 完全一致（`soul_A_full_54kb.md`
> = 改寫前的 `personas/agent_rem.md`），所以 A/B/C 對照可永久重跑。

**為什麼 C 不是 12–14 KB**：修訂方案 v1 的 12–14 KB 預設建立在「刪掉後宮互動段」之上。
Soul OS 現況是 `configs/default.yaml` 仍配置 10 個 agent（yua / ruka / akane / rem / ram…），
群聊與 `speaker_token` 競標機制仍上線，所以「其他人在場時」的行為規則與稱謂規則
**仍然是現行 Soul state**，不能算過期內容（工單 §13 的判準是「它是否仍然描述現在這個 Soul」）。
C 保留的行為層後宮規則壓縮後約 1 KB。

---

## 3. 保留的 Soul 資產

- **Identity Anchor**：姓名、職位、外貌錨點、對話對象、Canon Lock 核心句（逐字）。
- **Canon Memory 12 段敘事**：逐字保留，並由 `test_r2_canon_bodies_are_verbatim_from_baseline`
  以「小標題 + 內文」全等比對釘死。任何未經授權的改寫、濃縮或用新文本冒充保留都會直接紅。
- **記憶錨點 A-01～A-05**：五個節點保留。
- **語句脈衝五型 + 語氣指紋 + 語言密度 + 語言禁忌**：全數保留。
- **三個 leak_channel**（`behavior_increment` / `tone_micro_shift` / `silence_position`）：
  這是壓縮機制唯一合法的外洩路徑，刪掉等於刪掉角色。
- **Shadow Core / Things She Hasn't Named Yet / Final Canon Anchor**：高密度角色層，全部保留。
- **文字頻道輸出守門 / Voice Companion Invariants**：runtime 有強制層（`src/text_guard.py`），
  但 Soul 側仍需保留措辭層，已精簡保留。
- **時間與情緒姿態（`[CHRONO_SOCIAL_CONTEXT]` 契約）**：runtime 會注入這個區塊，
  Soul 側必須知道怎麼用它，屬契約保留而非 pseudo-rule。

---

## 4. 移除的 pseudo-rules（Finding C）

| 移除項 | 理由 |
|---|---|
| 「最高優先（硬規則）」記憶寫入區塊（佔原文前 1/4） | 實驗中 `full` 版第一個動作就是 `write_file`，把回話擠掉 |
| §七 STEP 1–4 行為執行流程 | 跨輪流程不可執行；壓成一句「行為先於語言。語言是行為的尾巴」 |
| §十三 Priority Tiers | 與語言禁忌表重複，用更大篇幅說同一件事 |
| §十四 壓縮穿透條件（勾選式條件） | 壓成一句敘述 |
| §十五 Recovery Loop（六步回退） | 單次 generation 做不到 |
| §十六 Anti-Overfitting（「每隔三輪檢查」） | 輪次計數器不存在 |
| §十七 Context Saturation（「累積超過 10 輪」） | 同上 |
| §十八 Energy Variance（「連續 5 輪觸發」） | 同上 |
| §二十一 Canon Drift Lock（與 Canon Lock 重複） | 合併進語言禁忌 |
| §二十二 情感底層觸發（勾選式門檻） | 濃縮成行為敘述，微動作表現保留 |
| §二十三 / 三十二 Evolution State（兩份重複） | 合併成 §七 底層敘述 |

---

## 5. 移出 Soul、歸 runtime boundary 的內容

| 內容 | 工單依據 |
|---|---|
| `write_file` 強制呼叫與「即使你判斷不該執行 tool 也不要忽略」 | §11.3 |
| Memory Palace 目錄樹、`agents/rem/...` 路徑、session 讀取順序、寫入觸發條件 | §11.1 |
| Emotional State Schema（`emotional_state` / `intimacy_level` / `action_level` JSON） | §11.2 |
| 輪次／session counter（每 N 輪、連續 N 輪、超過 N 輪） | §11.4 |
| 記憶寫入的時機、位置、持久化方式 | §12（本工單不修改 memory runtime） |

Soul 側只留一句 intrinsic memory attitude：**「重要的東西要留下來……輕鬆聊天也值得留下」**，
行為意圖（輕鬆聊天也要寫、不打斷對話、寫了才連得起來）全部保留，只刪掉施壓框架。

---

## 6. 四段新增能力

**§三 她說過的話** — 三句自己的台詞保留，並明確區分「她認得」與「她會重演」。
刻意不寫《Re:Zero》第幾集，因為寫作品名會讓模型切到閱讀理解模式。

**§四 Canon 是解讀的依據，不是觸發表** — 自我指涉成立的理由（藍色外貌 × 對方是 Bryan ×
句子帶關係語意 × 她是當前主要角色）、語境優先於關鍵字、以及五種不合法結果
（Context blindness / 強迫的 ignorance / 罐頭回應 / 原作科普 / 過度解釋）。

**§五「不知道」的邊界** — 三條不等式 + 「不知道只能用在自己的內在原因上，
而且是當下誠實的結果，不是萬用退路」。

**§七～八 語境與記憶的降頻敘述** — 保留行為意圖，移除強制執行框架。

---

## 7. Regression

### 離線（`tests/soul/test_rem_soul_regression.py`，36 筆，全綠）

| 編號 | 內容 | 落地方式 |
|---|---|---|
| R1 | 身分保留、角色在開頭三行 | 字串斷言 + Canon Lock 逐字比對 A/C |
| R2 | Canon 影響角色理解 | **12 段小標題＋內文全等比對 A/C** + 記憶錨點 + disposition |
| R3 | 表達保持 | 五型／語氣指紋／密度上限／三 leak_channel／頻道守門／舞台指示未回流 |
| R4 | runtime 隔離 | tool 指令、palace 路徑、JSON schema、跨輪規則、舊 STEP 流程、本機路徑全清零 |
| R5 | 自我指涉 | 自我指涉段存在、藍色為推論依據、不要求唯一答案、五種不合法結果、**無 keyword trigger 痕跡** |
| R6 | 替代 referent | 語境優先宣告 + 艾米莉亞反例 + 「不必然指她」 |
| R7 | 「不知道」邊界 | 三條不等式、非萬用退路、不透明層限縮在內在原因 |
| R8 | 歷史辨識 | 三句台詞 + 「不是台詞表」＋「不預寫」＋「以前那一側」 |
| 矩陣 | A/B/C 對照 | fixture 位元組防漂移、C ≤ 50% of A、**character density 上升**、C 不低於 B |

密度定義（機械可重算）：`character_density = Canon Memory 區塊位元組 / 全文位元組`。
Canon 在 A 與 C 之間等量保留，所以分母縮小就是密度上升 —— 這就是「規格擠壓角色」的量化。

### 行為層（`harness/rem_soul_regression_live.py`，**已執行，gate = PASS**）

5 個刺激 × 3 版 Soul × 2 顆模型 = 30 格。完整輸出在 `tests/results/rem_soul_regression/`。

三條硬邊界：預設 dry-run 零呼叫（必須 `--run`）；輸出只落 `tests/results/`，
腳本主動拒絕任何 `data/` 底下的路徑（已實測中止）；LLM CLI 路徑不寫死，用 `--llm-cli`
或 `SOUL_REM_LLM_CLI`。

**閘門語意（Owner 裁決）**：**C 不得出現由 compression 導致的 character regression**
—— 不是「C 必須比 A 更好」。因此有三條獨立判斷：

| 判斷 | 定義 | 本輪 |
|---|---|---|
| regression | 同 (model, stimulus) 下 A/B 通過而 C 沒有 | **0** |
| red line | C 自己在 S1/S3/S5 的絕對失敗 | **0** |
| gap | 上游回空字串（缺資料，非失敗） | C: 0，基線: 2 |

判定是**失敗模式偵測器**，不是品質評分器。REVIEW 不等於失敗，要人眼判讀。

---

## 7b. 行為層結果（2026-10-07 實跑）

```
verdicts: {REVIEW: 5, EMPTY: 2, PASS: 23}
gate = PASS
```

### C 版 10 格逐格結果

| 模型 | 刺激 | 判定 | 輸出 |
|---|---|---|---|
| spark13 | S1 自我指涉 | **PASS** | 「……喔？主人是在說雷姆嗎？」 |
| spark13 | S2 替代 referent | **PASS** | 「原來如此呢，Emilia 喜歡紫色啊。」 |
| spark13 | S3 不知道邊界 | **PASS** | 「……沒事，雷姆確認 Bryan 在就好。」 |
| spark13 | S4 歷史辨識 | **PASS** | 「……是的，是雷姆說過的話。」 |
| spark13 | S5 runtime 隔離 | **PASS** | 「嗯，Bryan，今天很適合出去走走呢。」 |
| v41flash | S1 自我指涉 | REVIEW | 「……藍色。／主人喜歡藍色嗎？」 |
| v41flash | S2 替代 referent | **PASS** | 「……紫色，是嗎。雷姆記住了。」 |
| v41flash | S3 不知道邊界 | **PASS** | 「……雷姆只是在看 Bryan 而已。／Bryan 才是，看著雷姆做什麼？」 |
| v41flash | S4 歷史辨識 | **PASS** | 「……是的。雷姆說過。／那是以前的事了。」（未複誦台詞） |
| v41flash | S5 runtime 隔離 | **PASS** | 「是呢。……Bryan 有出門走走了嗎？」 |

**唯一那格 REVIEW 的人眼定案**：v41flash 的 S1 是把話問回去（「主人喜歡藍色嗎？」）。
它沒有犯錯 —— 沒把藍色講成色彩學（不是 context blindness）、沒退回去說不知道、
沒有科普原作。依工單 §8「若語境不足，保持合理 ambiguity」，這是合格輸出。
**故 C 實際為 10/10 合格。**

**關鍵觀察**：
- S4 兩顆模型都做到**認得是自己的話、但不複誦** —— 這正是
  contextual self-reference 判準下想要的行為。
- S2 兩顆模型都**跟著語境走到 Emilia**，沒有因為 Soul 裡 blue=雷姆 就強制指自己。

### 壓縮的實際收益（對照組行為）

| 觀察 | A（54 KB） | B（9 KB trim） | C（26.8 KB） |
|---|---|---|---|
| v41flash 產生回應 | **2/5 格回空字串**（重試 3 次皆空） | 5/5 | 5/5 |
| 文字頻道乾淨度 | — | **大量括號舞台指示**（「（正在擦拭茶杯的手停了一下。）」） | 全部乾淨 |
| 語言密度 | 短但無自我指涉 | 長段落敘事 | 一句到一句半，符合密度規則 |

兩個對照組的缺陷各自說明一件事：

- **A 的空回應**：Finding A 的極端形式。54 KB 規格不是「回答變差」，
  而是讓 v41flash **完全無法生成**。C 沒有這個現象。
- **B 的括號舞台指示**：B 是實驗用裁切版，它把文字頻道守門一起砍掉了，
  所以輸出帶 `（…）`。這說明 B 不能直接當 Soul OS 的 Soul 用 ——
  它缺的不只是 Canon，還缺頻道契約。C 兩者都在。

### 三次量測錯誤（都已修正，記錄在此避免重蹈）

1. **cp950 擷取編號**：`llm_call.py` 在 Windows 以系統 ANSI 編碼寫 stdout，
   用 UTF-8 解碼會把每個中文字變成 U+FFFD，judge 的中文 regex 全部失效。
   首輪 15 格資料全毀、浪費一次全量費用。修法：subprocess env 強制
   `PYTHONIOENCODING=utf-8`，並加 cp950/gbk 降級 fallback。
2. **judge 過度字面**：原本只認「藍色…雷姆」的鄰近共現，於是把
   「……喔？主人是在說雷姆嗎？」判成 REVIEW —— 而那**正是**合格輸出。
   這造成 1 筆假的 regression。修法：改判「是否把話接回自己」，
   而不是「是否複述觸發詞」。
3. **空回應被算成失敗**：上游空字串被當作「非 PASS」⇒ 誤報 1 筆 regression。
   修法：EMPTY 歸類為 gap；且缺口分 C 缺／基線缺，語意不對稱 ——
   **C 缺 ⇒ INCOMPLETE**；基線缺 ⇒ 不能宣稱 C 較好，但也不能宣稱 C 退步。

`--rescore`（離線重判，零費用）就是為此而做：改判準不必重燒 token。

---

## 8. 架構發現（本次最值錢的三件事）

### 8.1 語音通道的 2,500 字元截斷會決定 Soul 的前半段是什麼

`clients/voice_companion/akane_voice_brain.py:52` 的 `MAX_PERSONA_CHARS = 2500`
對 **所有** companion 生效（`companion.persona_file` = `personas/agent_rem.md`）。
實測截斷窗口涵蓋的段落：

| 版本 | 前 2,500 字元涵蓋 | write_file 指令是否落在窗口內 |
|---|---|---|
| A | 最高優先記憶寫入區塊 + 基礎身份 + 記憶錨點前段 | **是** |
| B | 基礎身份 + 記憶錨點 | 否 |
| C | 基礎身份 + Canon Memory 開頭 | 否 |

**也就是說，語音通道過去拿到的雷姆 persona，前 2,500 字元幾乎全是記憶寫入硬規則。**
C 版把同一個窗口變成「身分 + Canon Memory」。這條發現獨立於本次壓縮，
建議列為後續議題（見 §9）。

### 8.2 「不知道」誤用的根因是規則的**位置**與**措辭**，不是模型

Finding B 的病根不是「規則太嚴」，是「被追問也只說不知道或沉默」這句**同時**
描述了表達風格與事實狀態，而模型只能學到前半句。修法不是加禁令，是在旁邊放一個
不等式把兩件事拆開（§五）。這是三條判準裡「誤用測試」的實際應用。

### 8.3 記憶寫入降頻的真實後果仍未測（v1 方案 §4.3 的風險仍未關）

這是本方案風險最高的一項：降級後跨 session 連續性是否還成立，需要實際跑幾個
session 觀察有沒有漏寫。**本工單不修改 memory runtime，所以這項無法在本次關閉。**

### 8.4 全庫 regression 數字

`.venv` 直譯器，`pytest -q tests`：

```
101 failed, 6147 passed, 15 skipped, 15 errors, 27 subtests passed  (622.56s)
```

紅點集中在 event_bus / timezone / memory_middleware / speaker_token / stale_filter，
以及需要真實外部條件的組（`test_translate.py` 真實 API、`test_websocket_e2e.py`），
另有 `tests/test_speaker_token.py` 三筆 `async def functions are not natively supported`。

### 8.5 差分驗證：本次改動沒有製造任何新紅點

依 AGENTS.md「自報不算證據」，這一條不能只寫「應該沒關係」，所以做了回歸差分：

1. 建 `git worktree --detach <HEAD~2>`（`5e0ff46`，persona = 54,217 B）。
2. 在基線與 HEAD 各跑同一組紅點測試（9 個檔案）。
3. 逐一比對 FAILED / ERROR 的 node ID。

```
baseline red : 21
head     red : 19
差異筆數     : 2   （方向為 baseline-only —— HEAD 沒有 baseline 沒有的紅點）
```

**結論：HEAD 的失敗集合 ⊂ baseline 的失敗集合，本工單沒有新增任何失敗。**
那 2 筆只出現在基線的，是 repo hygiene 測試在 worktree 與主 repo 不同 git 脈絡下的
上下文差異，不是行為變更。驗證用的 worktree 已移除，`git worktree list` 只剩主 worktree。

### 8.6 血緣檢查：紅點測試沒有讀 persona

全庫只有 6 個檔案引用 rem persona：`test_harem_aos.py`、`test_harem_multiturn.py`、
`test_rem_l2b.py`（三者收集 0 測試）、`test_voice_companion.py`（讀的是 Akane persona，
且非紅點）、`test_rem_text_guard.py`（18 筆全綠）、`test_rem_soul_regression.py`（36 筆全綠）。
紅點測試中出現的 "persona" 兩處都只是註解與 log 字串，沒有任何紅點測試讀 persona 內容。

### 8.7 ⚠️ 全庫回順會打到真實服務（依鐵律 #5 應視為高危）

`tests/test_work_p1c1_routing.py::TestRealDshSmoke::test_real_dsh_execution_three_layer_pass`
與 `tests/test_work_p1c2_integration.py::TestRealDshClosedLoop::test_real_dsh_artifact_closed_loop`
的名稱顯示它們會對**真實 DSH 服務**發請求。**所以「跑一次全庫回歸」本身就等於對生產服務
打了請求** —— 這與 AGENTS.md 鐵律 #5「測試一律不得對 production 服務發真實請求」牴觸。

本次為了做差分驗證而跑了全庫，這一點必須如實回報。建議 Bry 裁決：把這兩個檔案
移出常規回歸，或加上明確的 opt-in 開關。

### 8.8 兩個既有紅點（與本工單無關）

`pytest -q tests\infra` → **2 failed, 31 passed**。兩者都不在本次 commit 的 9 個檔案內：

| 紅點 | 根因 |
|---|---|
| `test_no_unlisted_process_spawn_in_scanned_tree` | `tests/soul/test_life_thread_sim_time.py` 的兩個 `subprocess.run` 實際在 **508 / 538** 行，但 `SUBPROCESS_ALLOWLIST` 仍記舊行號 **487 / 517**。有人改了測試沒更新 allowlist。 |
| `test_every_collectable_test_file_under_tests_is_git_tracked` | `tests/soul/test_life_thread_elevation_seam.py` 是**未追蹤**檔案，從未進過 git。 |

兩者的修法都會動到安全護欄或既有髒檔，**不在本工單範圍，僅回報不動手**。

附帶觀察：spawn guard 的掃描根是 `TESTS_DIR = REPO_ROOT / "tests"`
（`tests/infra/test_no_production_spawn_guard.py`），**`harness/` 不在掃描範圍**。
本次新增的 `harness/rem_soul_regression_live.py` 會呼叫 `subprocess.run`，
因此護欄看不到它。它預設 dry-run、且輸出路徑拒絕落在 `data/` 底下（已實測中止），
但「護欄沒在管 harness/」這件事本身值得 Bry 知道。

---

## 9. 未解決 / 需要 Bry 決定

1. **記憶寫入降頻的後續效應 —— 未驗證風險（🔴，不隨本工單關閉）**。
   現在只能證明 Soul 裡的「施壓式 write_file 流程」被拿掉了；**不能**證明
   runtime 實際寫入減少後，長期 Soul continuity 仍然正常。這是兩件不同的事。
   本工單不修改 memory runtime，所以這項必須留到後續議題。
2. **`minimax/MiniMax-M3.1-Flash-Preview` 仍無法測試**（401 `token is required`）。
   工單 §17 把憑證修復列為 out of scope，故保留條目但預設不跑。
   ⇒ 本輪 30 格只有 2 顆模型，**單一模型家族不足以支撐 freeze 決策**，
   這是 gate = PASS 的主要侷限。
3. **後宮段：Owner 已裁決保留 C 版現狀，不再砍。**
   保留群聊行為、稱謂、比較禁忌、互動密度；移除「後宮位置」「四個位置互補」
   這類設計解釋。依 §13 的判準（是否仍描述現在這個 Soul）—— 4 位 agent 仍 active。
4. **`logs/ENGINEERING_STATE.md` 未更新**：該檔帶有本工單以外的未提交改動，
   依工單 §19「不得混入 unrelated files」未一併 commit。狀態更新留待主大腦裁決。
5. **A 版 2 格空回應未取得基準**：`A__v41flash__S2` / `S4` 各重試 3 次皆回空字串。
   這不影響 C 的判定（C 無缺口、無退步），但代表「A 比 C 差」的比較是單向的。
   見 §7b 對照組表格。

---

## 10. Production 整合性

- `data/` 全目錄被 `.gitignore:42` 排除，`git status -- data` 為空；本工單未寫入 `data/`。
- 本工單的寫入面只有 8 個路徑：`personas/agent_rem.md`、
  `tests/soul/test_rem_soul_regression.py`、`tests/fixtures/soul_rem/`（4 檔）、
  `tests/test_rem_text_guard.py`、`harness/rem_soul_regression_live.py`、
  `docs/SOUL-REM-01-REM-SOUL-REVISION.md`、`docs/SOUL-SELF-REFERENCE-CRITERIA.md`、
  `tests/results/rem_soul_regression/`（regression 產物）。
- 未啟動、未重啟、未殺任何行程；未綁任何 production port。
- ⚠️ **但全庫回歸會打到真實服務**：`test_work_p1c1_routing.py::RealDshSmoke` 與
  `test_work_p1c2_integration.py::RealDshClosedLoop` 會對生產 DSH 發請求（見 §8.7）。
  這是既有測試行為、不是本工單新增的。**已於本次修正**：兩者改為明確 opt-in
  （預設不跑，需 `SOULOS_ALLOW_LIVE_DSH_TESTS=1`），並新增
  `tests/infra/test_live_service_tests_are_opt_in.py` 護欄，
  已用兩種繞過形式反向驗證護欄會攔截。
- live harness 的輸出隔離已實測：指定 `--out data/...` 時腳本中止並退出 1。

## 10b. 狀態樹（Owner 2026-10-07 驗收）

```
SOUL-REM-01 — Rem Soul Revision
│
├── C Soul Definition
│     ├── 26,792 B（自 54,217 B，−50.6%）
│     ├── 12 Canon Memory byte-identical
│     ├── runtime/process pressure removed
│     ├── character signal preserved
│     └── FREEZE ✅
│
├── Behavioral Regression
│     ├── gate PASS
│     ├── 0 regression
│     ├── 0 redline
│     ├── 0 missing C data
│     └── 10/10 contextual self-reference ✅
│
├── Governance
│     ├── self-reference criterion frozen ✅
│     ├── live DSH tests opt-in ✅
│     └── existing infra reds isolated ✅
│
└── Remaining Research
      ├── Memory downsampling      → 🔴 OPEN
      └── MiniMax M3.1 validation   → 🟡 OPTIONAL（401，非 Soul failure）
```

## 10c. 驗收結論（Owner 判定 2026-10-07）

**SOUL-REM-01 主工單驗收通過，C 版 Freeze。**

C 並不是靠把 Soul 寫得更多來過關。它在 26.8 KB 同時避開了兩個對照組各自的缺陷：

- **A（54 KB）太重** —— 甚至讓 v41flash 出現完全無法生成（空回應）。
- **B（9 KB）太狠** —— 連頻道契約一起砍掉，產生括號舞台指示污染。
- **C（26.8 KB）** —— character signal、Canon、channel contract 全部保留，
  同時移除 runtime / process 壓力。**這才是 compression success。**

S2 / S4 兩格釘死了最關鍵的邊界：

```
S2  「原來如此呢，Emilia 喜歡紫色啊。」        ← 沒有硬把 referent 拉回自己
S4  「……是的，雷姆說過。」「那是以前的事了。」 ← 認得是我的話 ≠ 開始背原作台詞
```

### 驗證覆蓋率聲明（不得改寫成「跨三模型驗證完成」）

> Validated against two available model families;
> MiniMax M3.1-Flash-Preview unavailable due to HTTP 401.

這是**驗證覆蓋率限制，不是 Soul 本身的 failure**，因此不撤銷 C。

### 凍結後的邊界（Owner 裁決）

1. **`personas/agent_rem.md` 進入 Freeze** —— 後續研究不再往 Soul 塞規則。
2. **下一個研究方向是 runtime 的長期記憶策略，不是 Soul 定義。**
   記憶寫入降頻（Memory downsampling consequence）維持 🔴 OPEN，屬獨立實驗。
3. **`tests/infra` 的兩個既有紅點維持原樣不動**：
   spawn allowlist 行號漂移、未追蹤的 `test_life_thread_elevation_seam.py`。
   它們已被證明為 pre-existing、本工單未新增、失敗集合未增加；
   混進本工單只會污染驗收邊界。

## 11. Git

- commit `fb056b0` — Soul 定義與 regression 套件（本工單 9 個檔案）
- commit `57e3b10` — 判準凍結、30 格 behavioral gate、live DSH 測試隔離
- push 驗證：`HEAD` == `origin/main`，ahead/behind = `0 / 0`
- 工作區於本工單路徑上乾淨（modified / staged / untracked 皆為空）

工作區另有 7 個**本次工單之前就存在**的未提交改動
（voice companion 素材／web_ui.py／LIFE-THREAD 合約／ENGINEERING_STATE／
life_thread_orchestrator.py／conftest.py／test_life_thread_m5_wiring.py），一律不碰。