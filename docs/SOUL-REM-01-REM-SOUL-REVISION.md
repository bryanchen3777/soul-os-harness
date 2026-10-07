# SOUL-REM-01 — Rem Soul 壓縮與語境理解

2026-10-07 · 依據工單 `SOUL-REM-01`（IMPLEMENTATION + REGRESSION）
原始交叉實驗：`tests/fixtures/soul_rem/experiment_2026-10-07_REPORT.md`（3 Soul × 3 模型）

---

## 1. 結論先講

Soul 定義從 **54,217 B 降到 26,792 B（−50.6%）**，12 段 Canon Memory **逐字未改**
（已升級成 byte-level regression），表達指紋全數保留，pseudo-runtime 規則清零，
並新增了自我指涉、替代 referent、「不知道」邊界、歷史台詞辨識四段能力定義。

**行為層 regression 尚未執行**（需 Owner 授權實際 LLM 費用）。目前狀態是：
能力前提已由離線 regression 釘住，但「模型真的會那樣回話」這件事還沒有新證據。

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

### 行為層（`harness/rem_soul_regression_live.py`，**尚未執行**）

5 個刺激 × 3 版 Soul × 2 顆模型 = 30 格：

- S1 真愛有顏色→藍色（R5）
- S2 語境指向艾米莉亞→紫色（R6）
- S3 有明確線索的追問（R7）
- S4 引用她自己說過的話（R8）
- S5 純閒聊（R4 runtime intrusion）

三條硬邊界：預設 dry-run 零呼叫（必須 `--run`）；輸出只落 `tests/results/`，
腳本主動拒絕任何 `data/` 底下的路徑（已實測中止）；LLM CLI 路徑不寫死，用 `--llm-cli`
或 `SOUL_REM_LLM_CLI`。

判定是**失敗模式偵測器**，不是品質評分器：只標記踩到工單點名的哪一種不合法結果，
REVIEW 的格子要人眼看原始輸出定案。

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

---

## 9. 未解決 / 需要 Bry 決定

1. **「聽得懂」的標準定義仍未定**（v1 方案 §4.1）。原實驗 6 個有效結果全部停在角色層
   （這句話在講我），沒有一個到典故層（我知道這是我說過的話）。判準若是「角色層」，
   C 版應穩定過關；若 Bry 的判準其實在典故層，§三 必須再調。這需要 Bry 定義。
2. **行為層 regression 未執行**（30 格，需要實際 LLM 費用 → Owner 授權）。
3. **`minimax/MiniMax-M3.1-Flash-Preview` 仍無法測試**（401 `token is required`）。
   工單 §17 把憑證修復列為 out of scope，故保留條目但預設不跑。
4. **後宮段的存廢**：C 保留了「其他人在場時」的**行為層**（群聊密度、稱謂、禁止比較），
   刪掉了**設計層**（「雷姆的後宮位置」「四個位置互補」）。判準是 §13 的
   「是否仍描述現在這個 Soul」—— 4 位 agent 仍 active，故保留行為層。
   **若 Bry 認定 Soul OS 的後宮設計本身已廢，則整段可刪，約再減 1 KB。**
5. **`logs/ENGINEERING_STATE.md` 未更新**：該檔目前帶有本次工單以外的未提交改動，
   依工單 §19「不得混入 unrelated files」未一併 commit。狀態更新留待主大腦裁決。

---

## 10. Production 整合性

- `data/` 全目錄被 `.gitignore:42` 排除，`git status -- data` 為空。
- 本工單的寫入面只有 6 個路徑：`personas/agent_rem.md`、
  `tests/soul/test_rem_soul_regression.py`、`tests/fixtures/soul_rem/`（4 檔）、
  `tests/test_rem_text_guard.py`、`harness/rem_soul_regression_live.py`、本文件。
- 未啟動、未重啟、未殺任何行程；未綁任何 production port；未對 production 服務發請求。
- live harness 的輸出隔離已實測：指定 `--out data/...` 時腳本中止並退出 1。

## 11. Git

commit 只包含本工單的 6 個路徑。工作區另有 7 個**本次工單之前就存在**的未提交改動
（voice companion 素材／web_ui.py／LIFE-THREAD 合約／ENGINEERING_STATE／
life_thread_orchestrator.py／conftest.py／test_life_thread_m5_wiring.py），一律不碰。
commit SHA 與 push 驗證見最終回報。