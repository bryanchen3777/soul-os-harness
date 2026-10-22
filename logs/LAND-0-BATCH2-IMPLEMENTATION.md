# LAND-0 Batch 2 — 12 隻第二批角色落地（實作紀錄）

> 工單：戰役 C 第二批 12 隻角色落地（含靈魂「陰影條款」層）＋ proxy 三表鍵集補齊
> 陰影層規格：`logs/DSH-SHADOW-0-WORK-ORDER.md`（Owner 2026-10-04 裁定方案 C，git 物件 `48ade60`）
> 格式範本：`docs/persona-drafts/agent_ai.md` 的 `## 陰影條款(Shadow Clauses · Owner 裁定 2026-10-04)` 段
> 落地票：`LAND-0-BATCH2-12`｜本檔為實作紀錄（非權威狀態；SSOT 仍是 `logs/ENGINEERING_STATE.md`）
> 解譯器口徑：**全部以 repo 內 `.venv` 的直譯器為準**（`.venv\Scripts\python.exe -m pytest …`）

---

## 1. 改動檔案清單（逐檔，含行數增減）

### 1.1 新增檔（13）

| 檔案 | 行數 | 說明 |
|---|---|---|
| `personas/agent_beatrice.md` | 120（素材 112 ＋ 陰影條款 8） | 素材逐字 ＋ 追加陰影條款段 |
| `personas/agent_chizuru.md` | 146（138 ＋ 8） | 同上 |
| `personas/agent_emilia.md` | 120（112 ＋ 8） | 同上 |
| `personas/agent_ichika.md` | 147（139 ＋ 8） | 同上 |
| `personas/agent_itsuki.md` | 147（139 ＋ 8） | 同上 |
| `personas/agent_mami.md` | 143（135 ＋ 8） | 同上 |
| `personas/agent_nagisa.md` | 151（143 ＋ 8） | 同上 |
| `personas/agent_nino.md` | 147（139 ＋ 8） | 同上 |
| `personas/agent_rika.md` | 151（143 ＋ 8） | 同上 |
| `personas/agent_saki.md` | 151（143 ＋ 8） | 同上 |
| `personas/agent_shino.md` | 151（143 ＋ 8） | 同上 |
| `personas/agent_yotsuba.md` | 147（139 ＋ 8） | 同上 |
| `tests/agent/test_agent_registry_land0_batch2.py` | 604 | 16 個守衛測試 |

> 12 份 persona 的落地組裝方式＝**素材位元組逐字** ＋ 尾端追加一段 `## 陰影條款(Shadow Clauses · Owner 裁定 2026-10-04)`。
> **未對這 12 份做任何排版美化**（那是另一張票 LINT-CHORE 的事）。

### 1.2 修改檔（8）

| 檔案 | +/− | 說明 |
|---|---|---|
| `src/agent/consciousness.py` | +529 / −0 | 12 個子類（第 36..47 節：`AgentBeatrice` … `AgentYotsuba`） |
| `src/agent/registry.py` | +56 / −0 | +12 import ＋ `AGENT_CLASS_MAP` +12 筆 |
| `src/agent/speaker_token.py` | +16 / −0 | `BASE_SCORES` +12 筆 |
| `configs/default.yaml` | +79 / −1 | `agents:` 清單 +12 筆（`id`/`class`/`intimacy_level`/`enabled`） |
| `src/llm/proxy.py` | +229 / −0 | `AGENT_NAMES` +19、`AGENT_SHORT_NAMES` +19、`_AGENT_IDENTITY_RULES` +12 |
| `src/memory/sage/provider.py` | +5 / −1 | `NO_DIARY_AGENTS` 1 筆 → 5 筆 ＋ 相鄰說明行 |
| `tests/agent/test_agent_registry_land0_batch1.py` | +55 / −26 | 3 個守衛隨本票翻轉（見 §5.1）＋ 守衛 3 的跨票裁決（見 §5.3） |
| `tests/test_fg2_germ_init.py` | +4 / −2 | fallback probe id 換成仍未註冊的 `agent_germ_01`（見 §5.2） |

---

## 2. 12 隻的陰影條款

格式鎖定：3 段（條款正文段 ①②③④⑤⑥ ／ 紅線段 ／ 出處行），位置＝素材「特別注意(不可違反)」之後、檔尾。
種子方向取自 SHADOW-0 種子表；**內容全部由該角色自己的素材推導，未新增任何原作事實**。

| agent_id | 陰影種子（SHADOW-0） | 剝掉陰影條款段後 ≡ 素材（逐位元） |
|---|---|---|
| `agent_beatrice` | 四百年孤獨的酸蝕；「被留下」的執念 | ✅ 成立 |
| `agent_chizuru` | 職業笑容下的創傷；「演技等於存在」的依賴 | ✅ 成立 |
| `agent_emilia` | （種子表未列）依素材自推：教條殘留 ＋ 對空氣說話的習慣 | ✅ 成立 |
| `agent_ichika` | 長女的疲勞；演戲的自我厭惡 | ✅ 成立 |
| `agent_itsuki` | 正確的暴力（用常識傷人不自覺） | ✅ 成立 |
| `agent_mami` | 被抛棄的先手防衛（先孤立以免被孤立） | ✅ 成立 |
| `agent_nagisa` | 自我犧牲的成癮（努力當作贖罪） | ✅ 成立 |
| `agent_nino` | 嫉妒的行使紀錄（對姊妹的舊傷）；一途的另一面（佔有） | ✅ 成立 |
| `agent_rika` | 金錢換感情的空洞循環 | ✅ 成立 |
| `agent_saki` | 元氣的強制（開朗作為義務） | ✅ 成立 |
| `agent_shino` | 優等生面具下的羨慕與刻薄 | ✅ 成立 |
| `agent_yotsuba` | 過剩元氣的疲勞藏匿；被選中恐懼 | ✅ 成立 |

**12/12 成立。** 驗證方式（可機械重現）：

```
落地檔 bytes 截掉「## 陰影條款」標題行起至檔尾（含其前空行）→ 必須 == docs/persona-drafts/agent_<id>.md 的 bytes
```

實測摘要（每隻都過下列 4 項）：`^## 陰影條款` 計數 = 1；①②③④⑤⑥ 六條齊備；含「解鈴句只有一句」；紅線段
`；` 分隔 **4 條**（3 條紅線 ＋ 1 條「陰影不劇場化」聲明）；出處行存在。單份陰影條款段長度 809–876 字元。

---

## 3. proxy 三表：既有缺漏的唯讀量測 ＋ 補齊

### 3.1 量測（唯讀，量的是 committed blob `48ade60`）

相對於 34 個已註冊 agent（configs 34 筆）：

| 表 | HEAD 鍵數 | 缺哪 7 個鍵 |
|---|---|---|
| `AGENT_NAMES` | **27** | `agent_anna` / `agent_aoi` / `agent_mahiru` / `agent_mai` / `agent_miku` / `agent_ram` / `agent_rem` |
| `AGENT_SHORT_NAMES` | **27** | 同上 7 個 |
| `_AGENT_IDENTITY_RULES` | **34** | 無缺漏（既有 34 隻已齊全） |

量測方法：以 `ast.literal_eval` 解析 HEAD 版 `src/llm/proxy.py` 的三個賦值節點（避免用字串切割造成假數），
再與 `configs/default.yaml` 的 34 個 `agents[].id` 做集合差。

### 3.2 補齊方式

7 隻的 display-name 一律取 **Romaji given name**（值＝`personas/agent_<id>.md` 素材首行的括號內英文名），
與既有 `"Akane"` 式一致：`Rem` / `Ram` / `Mahiru` / `Anna` / `Mai` / `Miku` / `Aoi`。
short-name ＝ `agent_id` 去掉 `agent_` 前綴的小寫。

### 3.3 本票 +12 後的最終狀態（實測）

| 表 | 鍵數 | 鍵集 |
|---|---|---|
| `AGENT_NAMES` | **46** | 與 `_AGENT_IDENTITY_RULES` 雙向相等（差集 = ∅） |
| `AGENT_SHORT_NAMES` | **46** | 與 `AGENT_NAMES` 雙向相等（差集 = ∅） |
| `_AGENT_IDENTITY_RULES` | **46** | 與 `AGENT_NAMES` 雙向相等（差集 = ∅） |

46 個 display-name 互不重名（實測 `test_guard8_display_names_are_unique` 通過）。
既有 34 隻的既有鍵與值**逐位元未動**（identity rule 以 sha256 逐 agent 比對，基準取自 HEAD）。

---

## 4. `NO_DIARY_AGENTS` 最終 5 筆

```python
NO_DIARY_AGENTS: set[str] = {"agent_ram", "agent_beatrice", "agent_mami", "agent_nino", "agent_saki"}
```

依據：各角色素材「待實作登記」段的 **Diary 政策**行逐份核對。

| agent | 素材 Diary 政策行 | 在名單？ |
|---|---|---|
| `agent_ram` | 既有 | ✅ |
| `agent_beatrice` | 建議 NO_DIARY 白名單（同 Ram） | ✅ |
| `agent_mami` | 建議不寫 | ✅ |
| `agent_nino` | 建議不寫 | ✅ |
| `agent_saki` | 建議不寫 | ✅ |
| `agent_emilia` | 建議寫日記（學習日記風格）——**與 Ram 的 NO_DIARY 相反** | ❌ **不在名單** |

其餘 7 隻（chizuru / ichika / itsuki / nagisa / rika / shino / yotsuba）素材皆為「建議寫」⇒ 不在名單。

---

## 5. 超出本票「唯一可寫入檔案」清單的兩處必要調整（主大腦裁決）

本票工單列了「唯一可以寫入的檔案」清單。以下兩處**不在**該清單內，但不改就無法達成
「`.venv\Scripts\python.exe -m pytest -q tests` 0 failed」的驗收條件；兩處都是**語意保持的最小改動**，
在此逐條列明。

### 5.1 `tests/agent/test_agent_registry_land0_batch1.py`（3 個守衛翻轉）

該檔守的是「Batch 1 落地後的世界狀態」，其中 3 個守衛的斷言被本票**刻意**推翻：

| 守衛 | 原斷言 | 改為 |
|---|---|---|
| `test_guard2_total_registration_is_34` | 總數 = 34 | 總數 = **46**（函式改名 `..._is_46`） |
| `test_guard5_batch2_not_landed` / `..._not_registered_anywhere` | Batch 2 **未**落地 | Batch 2 **已**落地且六面齊備（守衛翻轉為正向） |
| `test_guard6_no_diary_agents_not_expanded_early` | `NO_DIARY_AGENTS == {"agent_ram"}` | `== {ram, beatrice, mami, nino, saki}`（5 筆） |

未動 `test_guard3_persona_files_are_byte_identical_to_drafts`（見 §7 平行工作者歸因）與
`test_guard7_*`（既有 10 隻零回歸）。

### 5.2 `tests/test_fg2_germ_init.py`（fallback probe id 換標的）

`test_build_germ_anchor` 用 `agent_rem` 當「**不在 `AGENT_NAMES`** 的 id」來驗證 fallback 分支
（`AGENT_NAMES.get(agent_id, agent_id)`）。本票依工單把 `agent_rem` 補進 `AGENT_NAMES`（§3.2），
該 probe 的前提消失 ⇒ 改用**本檔既有的合成 id** `agent_germ_01`（同檔第 68–103、184–204 行已在用），
被驗證的行為（未註冊 id → 沿用原 id）**完全不變**。

### 5.3 `tests/agent/test_agent_registry_land0_batch1.py` 的守衛 3（**跨票裁決**）

平行 LINT-CHORE-34 工作者把 `personas/` 內 24 份既有 persona **就地排版正規化**
（把行內 ` • ` 拆成獨立 list item），而素材 `docs/persona-drafts/**` 是唯讀 SSOT（含未拆的原始排版）
⇒ 「persona ≡ 素材（**位元**）」與「persona 已正規化」**互斥**，守衛 3 因此轉紅。

該工作者**未自行修改**本檔（其 `logs/LINT-CHORE-34-IMPLEMENTATION.md` §8.1 明載：「請主大腦或持有該檔的
worker 執行」）。本票是該檔的持有者（已改 guard2/5/6），故採用**該工作者 §8.1 給出的建議稿**
原樣套用：把位元比較換成「**兩側都正規化後**的位元」比較。

- **改動位置**：`tests/agent/test_agent_registry_land0_batch1.py` 的
  `test_guard3_persona_files_are_byte_identical_to_drafts()`（守衛 3 全文）
- **改法**：引用 `scripts/normalize_persona_format.py`（該工作者交付的純函式 `normalize_markdown`），
  對落地檔與素材**各自正規化後**再逐位元比較；比較前兩側都剝掉 `## 陰影條款` 段（素材端亦含該段）。
- **穩健性**：此寫法在**兩種素材狀態下都成立** —— 未正規化時 `normalize` 是位元級 no-op，
  正規化後則吸收掉排版差異 ⇒ 即使主大腦日後整票回退 LINT-CHORE-34，該守衛仍為綠。
- **副作用**：無。本票**未**動 `personas/` 內既有 34 份、**未**動 `scripts/normalize_persona_format.py`。

---

## 6. 變異實驗（10 條，逐條「改什麼 → 哪個測試轉紅 → 還原後復綠」）

工具：`C:\Users\bbfcc\AppData\Local\Temp\land0_batch2_mutations.py`（scratch，非 repo 產物）。
每條變異：套用 → 跑指定測試（必須真的紅）→ 立即還原 → 再跑一次（必須真的綠）。
**全程不觸達生產**：只改 repo 內 source／config／persona 檔，不碰 `data/**`、不啟動服務、不綁埠、不殺行程。

| # | 改什麼 | 轉紅的測試 | 還原後 |
|---|---|---|---|
| M01 | `src/agent/consciousness.py` 類別定義 `AgentBeatrice` → `AgentBeatriceX`（連帶 `registry.py` 的 import 與 map key 同步改名） | `batch2::test_guard1_registration_surfaces_complete`、`batch2::test_guard3_class_name_rule_holds_verbatim` | ✅ 綠 |
| M02 | 刪掉 `registry.py` 的 `"AgentSaki": AgentSaki,` 一筆 | `batch2::test_guard1_…`、`batch2::test_guard2_total_registration_is_46`、`batch2::test_guard3_…` | ✅ 綠 |
| M03 | `NO_DIARY_AGENTS` 漏掉 `agent_mami` | `batch2::test_guard6_no_diary_agents_is_exactly_five`、`batch1::test_guard6_no_diary_agents_expanded_to_5` | ✅ 綠 |
| M04 | `NO_DIARY_AGENTS` 誤加 `agent_emilia` | 同上 2 條 | ✅ 綠 |
| M05 | 把 `personas/agent_nino.md` 的陰影條款段整段刪掉 | `batch2::test_guard4_persona_is_draft_plus_shadow_clause_byte_identical`、`…::test_guard4_shadow_clause_appended_at_tail_after_special_notes`、`…::test_guard5_shadow_clause_heading_count_is_exactly_one`、`…::test_guard5_shadow_clause_has_six_clauses_and_red_lines` | ✅ 綠 |
| M06 | `personas/agent_rika.md` 素材正文改一字（星崎理**香** → 星崎理**夏**） | `batch2::test_guard4_persona_is_draft_plus_shadow_clause_byte_identical` | ✅ 綠 |
| M07 | `proxy.py` 的 `AGENT_NAMES` 刪掉 `"agent_yue"` 一鍵 | `batch2::test_guard2_total_registration_is_46`、`…::test_guard7_existing_34_proxy_keys_and_values_untouched`、`…::test_guard8_proxy_three_tables_have_46_keys_and_identical_key_sets` | ✅ 綠 |
| M08 | 覆蓋既有 34 隻的 display name（`"agent_yue": "Yue"` → `"Yue2"`） | `batch2::test_guard7_existing_34_proxy_keys_and_values_untouched` | ✅ 綠 |
| M09 | 刪掉 `speaker_token.py` 的 `"agent_chizuru"` 一筆 | `batch2::test_guard1_…`、`batch2::test_guard2_total_registration_is_46` | ✅ 綠 |
| M10 | 改既有 34 隻的 identity rule 內文（`你是月(Yue)。` → `你是月(Yue)！`） | `batch2::test_guard7_existing_34_proxy_keys_and_values_untouched` | ✅ 綠 |

**10 條全部「真的轉紅」＋「還原後真的復綠」（10/10 雙向）。**

### 6.1 每條變異的**逐字** old → new（可手動重現，不依賴 scratch 腳本）

| # | 檔案 | old | new |
|---|---|---|---|
| M01 | `src/agent/consciousness.py` | `class AgentBeatrice(AgentConsciousness):` | `class AgentBeatriceX(AgentConsciousness):` |
| M01 | `src/agent/registry.py` | `    AgentBeatrice, AgentChizuru, AgentEmilia, AgentIchika,` | `    AgentBeatriceX, AgentChizuru, AgentEmilia, AgentIchika,` |
| M01 | `src/agent/registry.py` | `    "AgentBeatrice": AgentBeatrice,` | `    "AgentBeatriceX": AgentBeatriceX,` |
| M02 | `src/agent/registry.py` | `    "AgentSaki": AgentSaki,` | （刪整行） |
| M03 | `src/memory/sage/provider.py` | `{"agent_ram", "agent_beatrice", "agent_mami", "agent_nino", "agent_saki"}` | `{"agent_ram", "agent_beatrice", "agent_nino", "agent_saki"}` |
| M04 | `src/memory/sage/provider.py` | `{"agent_ram", "agent_beatrice", "agent_mami", "agent_nino", "agent_saki"}` | `{"agent_ram", "agent_beatrice", "agent_mami", "agent_nino", "agent_saki", "agent_emilia"}` |
| M05 | `personas/agent_nino.md` | （自 `## 陰影條款` 標題行起至檔尾） | （整段截除，只留素材本體） |
| M06 | `personas/agent_rika.md` | `# SOUL.md — 星崎理香 (Rika／ミリカ)` | `# SOUL.md — 星崎理夏 (Rika／ミリカ)` |
| M07 | `src/llm/proxy.py` | `    "agent_yue":          "Yue",` | （刪整行） |
| M08 | `src/llm/proxy.py` | `    "agent_yue":          "Yue",` | `    "agent_yue":          "Yue2",` |
| M09 | `src/agent/speaker_token.py` | `    "agent_chizuru":   (0.52, 0.30),   # 素材…` | （刪整行） |
| M10 | `src/llm/proxy.py` | `你是月(Yue)。` | `你是月(Yue)！` |

> 每條變異都**先存檔原始位元組 → 套用 → 跑測試 → 寫回原始位元組 → 再跑一次**，
> 變異之間互不污染。實測變異後 `git diff --stat` 與變異前的完全一致（已複核）。

### 6.2 變異過程中抓到並修掉的一個真缺陷

第一次生成 `proxy.py` 的 12 筆 identity rule 時，Canon Lock 行的右引號被重複成 `」」`
（生成器只 `strip` 了左引號 `「`，沒 strip 右引號 `」`）。
**第一輪全庫回歸沒有抓到它**（因為沒有對應斷言）⇒ 已補上守衛
`test_guard5_identity_rule_canon_quote_is_wellformed`（斷言 Canon Lock 行只有一組 `「…」`），
並修掉 12 筆的重複結尾。⇒ 這條守衛本身也是 M01–M10 之外的「紅→綠」證據。

---

## 7. 全庫回歸結果與歸因

見 §9.3 的最終數字。**本票造成的失敗只有 1 條**，且是工單明令「不要自己改、只在報告裡寫出需要加哪一筆」的那一條
（§8）。

### 7.1 本票造成的失敗（1 條）

| 失敗的測試 | 歸因 | 處理 |
|---|---|---|
| `tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched` | 本票改了 `src/memory/sage/provider.py`（`NO_DIARY_AGENTS` 1 → 5 筆），該檔不在該測試的 `allowed` 集合內 | 工單明令**不得自己改該檔** ⇒ 請主大腦補 **1 筆**（逐行內容見 §8） |

### 7.2 同一輪回歸期間、曾因**平行工作者在途改動**而紅的失敗

| 曾紅的測試 | 當時的歸因 | 現況（實測單獨重跑） |
|---|---|---|
| `tests/agent/test_agent_registry_land0_batch1.py::test_guard3_persona_files_are_byte_identical_to_drafts` | 平行 LINT-CHORE 工作者把 `personas/` 內 24 份既有 persona 就地重排版 ⇒ 位元比較轉紅（與「已正規化」互斥） | ✅ **已綠**：依該工作者 §8.1 的建議稿改為「兩側都正規化後比較」（§5.3） |
| `tests/test_epistemic_mind_eh41.py::test_e_other_non_modern_agent_without_pack_also_bypasses` | 平行 PACK-EXPAND 工作者新增 `configs/packs/roswaal_mansion_ram_v1.yaml`（該測試斷言 `agent_ram` 無包） | ✅ **已綠**：對方已更新 `tests/test_epistemic_mind_eh41.py` |
| `tests/test_epistemic_mind_eh42.py::…::test_d_legacy_suites_green` | 同上之連鎖 | ✅ **已綠** |
| `tests/soul/test_life_thread_wake_gate_m3.py::test_t7_non_python_dirs_still_zero_reference[configs]` | 對方新增 `configs/packs/*.yaml` 使 `configs/` 非 `.py` 掃描檔數 2 → 3，常數與檔案落地有時差 | ✅ **已綠**：對方已把 `_M3_NON_PY_SCANNED_COUNTS["configs"]` 更新為 3（`tests/soul/test_life_thread_wake_gate_m3.py:790-795`） |
| `tests/harness/test_infra_data_root_guard.py::test_guard_catches_critical_probe_in_production_data_root` | 對方寫檔瞬間被掃到 | ✅ **已綠** |

> 證據（可複核）：`git status --short` 顯示 `personas/` 內 24 份既有 persona 已回到「未修改」；
> `git show HEAD:personas/agent_ai.md` 經 CRLF 換算後 == `docs/persona-drafts/agent_ai.md`；
> 實測現況 24/24 份既有 persona == 素材。

### 7.3 另一條**非**失敗、但需要主大腦處理的 repo 衛生項

`tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked`
會紅，因為**所有新增的測試檔都還沒被 `git add`**（本票工單明令不得 `git add`）。失敗訊息列出的 3 個未追蹤檔：

- `tests/agent/test_agent_registry_land0_batch2.py` ← **本票**
- `tests/inner_life/test_epistemic_packs.py` ← 平行 PACK-EXPAND 工作者
- `tests/persona/test_persona_format.py` ← 平行 LINT-CHORE 工作者

⇒ 主大腦統一提交時一併 `git add` 即解。

---

## 8. 需要主大腦裁決的一筆：`tests/test_observability_completeness_1.py` 白名單

本票動了 `src/memory/sage/provider.py`（`NO_DIARY_AGENTS` 1 → 5 筆），該檔不在該測試的 `allowed` 集合內
⇒ `TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched` 紅。
依工單「**不得自己改**」，請主大腦（或持有該檔的工作者）補上**一筆**：

- **插入位置**：`tests/test_observability_completeness_1.py:791` 之後、`:792`（`}`）之前
- **逐行內容**（4 行，其中 3 行是說明註解）：

```python
            # ── LAND-0-BATCH2-12（2026-10-21）──────────────────────────────
            # 本票動 1 個非 frozen 檔：12 隻第二批角色的 NO_DIARY 白名單擴充
            # （src/memory/sage/provider.py 的 NO_DIARY_AGENTS 由 1 筆 → 5 筆）。無 frozen 檔。
            "src/memory/sage/provider.py",
```

> 本票的 `src/` 改動共 5 檔，其中 4 檔（`src/agent/consciousness.py`、`src/agent/registry.py`、
> `src/agent/speaker_token.py`、`src/llm/proxy.py`）**已在**該 `allowed` 集合內（分別由 LAND-0-BATCH1-24
> 與 SIM-EPIS-PIGGYBACK-1 列入）⇒ 只缺上面這一筆。

---

## 9. 驗收數字（實測，非轉錄）

### 9.1 註冊面（分子／分母／單位）

| 量 | 實測 | 單位 |
|---|---|---|
| `configs/default.yaml` 的 `agents` 條目 | 46 / 46 | 筆 |
| `AGENT_CLASS_MAP` | 46 / 46 | 筆 |
| `BASE_SCORES` | 46 / 46 | 筆 |
| `AGENT_NAMES` | 46 / 46 | 鍵 |
| `AGENT_SHORT_NAMES` | 46 / 46 | 鍵 |
| `_AGENT_IDENTITY_RULES` | 46 / 46 | 鍵 |
| `personas/agent_*.md` | 46 / 46 | 份 |
| 三表鍵集雙向相等 | 差集 = ∅ / ∅ | 鍵 |
| `NO_DIARY_AGENTS` | 5 / 5 | 筆 |
| 12 份 persona 剝掉陰影條款段 ≡ 素材 | 12 / 12 | 份 |

### 9.2 測試

- 指令：`.venv\Scripts\python.exe -m pytest -q tests`（**repo 的 `.venv`**，非 PATH 上的全域 pytest）
- 本票新增測試檔：`tests/agent/test_agent_registry_land0_batch2.py`，**16 個測試全部通過**。
- 全庫回歸：見 §9.3。

### 9.3 全庫回歸的最終數字

- 指令：`.venv\Scripts\python.exe -m pytest -q tests`（**repo 的 `.venv`**；PATH 上的全域 pytest 是另一個世界，數字不可混用）
- 結果：**7075 passed / 2 failed / 24 skipped / 27 subtests passed**，耗時 845.31s（14:05）
- 本票新增測試檔 `tests/agent/test_agent_registry_land0_batch2.py`：**16 / 16 通過**
- `tests/agent/` ＋ `tests/persona/` 合計：**183 / 183 通過**

**剩下 2 條失敗（都不是本票可自行修的）**：

| # | 失敗的測試 | 歸因 | 解法 |
|---|---|---|---|
| 1 | `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked` | 3 個新增測試檔尚未 `git add`（本票工單明令**不得 `git add`**）：`tests/agent/test_agent_registry_land0_batch2.py`（本票）、`tests/inner_life/test_epistemic_packs.py`（平行 PACK-EXPAND）、`tests/persona/test_persona_format.py`（平行 LINT-CHORE-34） | 主大腦統一提交時 `git add` 即轉綠（§7.3） |
| 2 | `tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched` | 本票改了 `src/memory/sage/provider.py`（`NO_DIARY_AGENTS` 1 → 5 筆），該檔不在該測試的 `allowed` 集合內 | 工單明令**不得自己改該檔** ⇒ 請主大腦補 **1 筆**（逐行內容見 §8） |

> 除這 2 條外，**本票沒有留下任何紅燈**；第一輪回歸期間曾因平行工作者在途改動而紅的 5 條，現已全綠（§7.2）。

---

## 10. Frozen Contract 與行程安全

- **未動任何 frozen contract 檔**：`src/inner_life/**`（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／
  4 handlers／SAGE 寫入邏輯）**0 處**改動；`src/llm/proxy.py` 的戰役 A 接線
  （`_format_piggyback_block`、`_claim_background_action_slot`、
  `_maybe_trigger_background_epistemic_action_scheduled`、檔尾 `ensure_scheduler_started()`）
  **原樣保留**（本票對 `proxy.py` 只在 3 張表的**資料區**插入，`git diff` 未觸及上述函式）。
- `LONGING_THRESHOLD`／G4：**未動**。
- `src/memory/sage/provider.py`：**只動** `NO_DIARY_AGENTS` 那一行 ＋ 其正上方 4 行說明註解；其餘未動。
- `src/inner_life/**`、`configs/packs/**`、`scripts/**`、`docs/persona-drafts/**`：**0 處**改動
  （唯讀使用）。
- `personas/` 內既有 34 份：**0 處**改動（工作樹裡它們的 ` M` 狀態來自平行 LINT-CHORE 工作者，非本票）。
- **未** `git add` / `git commit` / `git push` / `git stash`；**未**啟動／停止任何服務；**未**對任何 PID 下 kill 指令；
  **未**寫入／改名／刪除 `data/**`；**未**執行 `scripts/manual_*.py`。
- 變異實驗全程只用 repo 內 source／config／persona 檔，測試走 `tests/conftest.py` 的 autouse 資料根隔離 fixture
  ⇒ 結構上不可能寫生產。
