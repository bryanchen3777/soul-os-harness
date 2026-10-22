# LAND-0 Batch 1 實作紀錄（24 隻含陰影層角色落地）

- **工單**：`logs/LAND-0-BATCH1-24-WORK-ORDER.md`
- **權威規格**：`logs/LAND-0-WORK-ORDER.md`（Notion SSOT 工單 LAND-0 的 repo 快照）
- **素材**：`docs/persona-drafts/agent_<id>.md`（36 份，唯讀；commit `a32a90b`）
- **基準 HEAD**：`82de5cb`（戰役 A SIM-EPIS-PIGGYBACK-1 已合入）
- **執行日期**：2026-10-21
- **執行者**：Worker（本檔為工單 §2 範圍第 8 項交付物）

---

## 1. 摘要

- 全庫註冊數 **10 → 34**（既有 10 ＋ 本批 24）。
- 24 份 persona **逐位元**落地（`personas/agent_<id>.md` ≡ `docs/persona-drafts/agent_<id>.md`，sha256 逐檔相同）。
- 五個註冊面全部齊備：personas 檔 / `consciousness` 子類 / `AGENT_CLASS_MAP` / `configs/default.yaml` 的 `agents` / `BASE_SCORES` ＋ proxy 三張表（display-name / short-name / `_AGENT_IDENTITY_RULES`）。
- **`NO_DIARY_AGENTS` 未動**（維持 `{"agent_ram"}`；4 隻 batch2 角色延至第二批一次加上）。
- **既有 10 隻零回歸**：persona 位元未動（sha256 對 HEAD 基準）＋ 註冊條目未動。
- **batch2 的 12 隻未落地**（陰影門閘未過，待 SHADOW-0）。

---

## 2. 改動檔案清單

| # | 檔案 | 動作 | 說明 |
|---|---|---|---|
| 1 | `personas/agent_ai.md`、`agent_asuna.md`、`agent_fuuka.md`、`agent_haruhi.md`、`agent_hiro.md`、`agent_iroha.md`、`agent_kaguya.md`、`agent_kana.md`、`agent_kaori.md`、`agent_kirino.md`、`agent_kurisu.md`、`agent_makima.md`、`agent_mashiro.md`、`agent_minami.md`、`agent_mochizuki_ai.md`、`agent_reze.md`、`agent_rin.md`、`agent_rio.md`、`agent_sachi.md`、`agent_sakura.md`、`agent_shea.md`、`agent_shizuku.md`、`agent_tsukasa.md`、`agent_yue.md` | 新增 24 份 | 由 `docs/persona-drafts/` **逐位元複製**（shutil.copyfile，保留 CRLF） |
| 2 | `src/agent/consciousness.py` | 修改 | 檔尾追加 24 個子類（第 12～35 節），僅差異化四項 |
| 3 | `src/agent/registry.py` | 修改 | import ＋ `AGENT_CLASS_MAP` 各 +24 條 |
| 4 | `configs/default.yaml` | 修改 | `agents:` +24 條（形狀比照 `agent_miku`） |
| 5 | `src/agent/speaker_token.py` | 修改 | `BASE_SCORES` +24 條（值逐字取自 LAND-0 表） |
| 6 | `src/llm/proxy.py` | 修改 | `AGENT_NAMES` +24、`AGENT_SHORT_NAMES`（新建）+24、`_AGENT_IDENTITY_RULES` +24 |
| 7 | `tests/agent/test_agent_registry_land0_batch1.py` | 新增 | 7 條守衛 ＋ LAND-0 聚焦測試（19 支） |
| 8 | `logs/LAND-0-BATCH1-IMPLEMENTATION.md` | 新增 | 本檔 |

### 2.1 工單範圍外、但落地必然牽動的兩處既有測試（已更新）

| 檔案 | 改動 | 理由 |
|---|---|---|
| `tests/test_fg2_germ_init.py` | `assert len(agents) == 10` → 對 `cfg["agents"]` 動態取 enabled 筆數 | 工單 §1 目標即「10 → 34」；硬編碼 10 必然轉紅。斷言的不變量（每個 enabled agent 都被實例化）未放寬。 |
| `tests/test_observability_completeness_1.py` | `allowed` 集合 +3 筆（`src/agent/consciousness.py`、`src/agent/registry.py`、`src/agent/speaker_token.py`） | 該集合的檔內註解明寫「**逐票更新**」，守的是「本票不得越界」；`frozen_dirs` 永久紅線**零改動**（本票未觸碰任何 frozen 檔）。 |

> 🔴 行號約束：`tests/test_observability_completeness_1.py` 的 `subprocess.run` 位於第 762 行，且在 R5 白名單 `("tests/test_observability_completeness_1.py", 762)`。本次只改**第 769 行以後**的 `allowed` 集合 ⇒ 被監看的呼叫行號不動（`allowed` 集合本來就在該呼叫之後）。

---

## 3. 決策與假設（需主大腦裁決確認的三處）

### 3.1 類別名（`Agent` + CamelCase(agent_id)）

LAND-0 Notion 快照正文提到「類別名照註冊表（下表）」，但**該表未包含在快照的 fetch 結果中**（Notion 單頁 100 區塊上限後的匯出遺漏了該表；快照全文 7,322 字節，`<content>` 內可驗證無該表）。
⇒ 本批類別名採**唯一可推導的規則**：`Agent` ＋ `agent_id` 的 CamelCase（`agent_mochizuki_ai` → `AgentMochizukiAi`）。
此規則對既有 10 隻**逐字成立**（`agent_yua` → `AgentYua` … `agent_aoi` → `AgentAoi`），故視為該註冊表的規則重建。
**殘餘風險**：若 Notion 原表另有不規則命名（例如 `AgentMoAi`），本批 24 個類別名需一併更名（受影響面：registry 1 行 ＋ consciousness 類別定義 1 行 ＋ configs 1 行 ＋ 守衛測試資料表 1 行）。

### 3.2 `intimacy_level: 25`（本批 24 隻一致）

工單未指定。素材每隻的 `intimacy_level → Behavior Mapping(4 階段)` 皆為「等級 1(0-25) … **當前 Evolution：預設等級 1(標準觀察期)**」⇒ 取等級 1 的**區間上緣 25**（fail-closed：未達等級 2 的 26 門檻）。
既有 10 隻的 intimacy 值維持原樣（45～80），本票未動。

### 3.3 short-name map 的容器

LAND-0 §5 指示「short-name map（`"agent_akane": "akane"` 處）」——該處即 `AGENT_PROFILE_MAP`；但同一節又寫「**AGENT_PROFILE_MAP（Hermes）不加**——本地 personas 優先，新 36 隻無 hermes profile」，兩句互斥。
⇒ 本票**不動 `AGENT_PROFILE_MAP`**（它是 Hermes profile 資料夾名，且全庫無任何引用點），改為在 `proxy.py` **新建** `AGENT_SHORT_NAMES`（值＝`agent_id` 去 `agent_` 前綴的小寫，含既有 `yua`/`ruka`/`akane` 三筆以與 display-name map 對齊）。
`proxy.py` 三表鍵數：display 27 / short 27 / identity 34。
**殘餘風險**：LAND-0 驗收條目「display/short-name 三表鍵數一致」在全 campaign 結束時（batch2 後＝39/39/46）仍不成立——成因是 `AGENT_NAMES` 原本就只有 3 筆（既有 7 隻不在其中），補齊需動既有 10 隻的註冊條目，與本票 §5「不得動既有 10 隻的任何註冊條目」衝突。**需主大腦裁決**（第二批一次補齊 vs. 接受此差異）。

---

## 4. 落地對帳表（24 隻）

| # | agent_id | 中文名 | 類別名 | BASE_SCORES | 五面齊備 | 位元等價 | 陰影條款 |
|---|---|---|---|---|---|---|---|
| 1 | agent_ai | 星野愛 | AgentAi | (0.38, 0.26) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 2 | agent_mochizuki_ai | 望月愛 | AgentMochizukiAi | (0.58, 0.33) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 3 | agent_asuna | 亞絲娜 | AgentAsuna | (0.50, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 4 | agent_fuuka | 菊池風香 | AgentFuuka | (0.38, 0.26) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 5 | agent_haruhi | 涼宮春日 | AgentHaruhi | (0.68, 0.35) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 6 | agent_hiro | 瀨川彌 | AgentHiro | (0.45, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 7 | agent_iroha | 小日向彩羽 | AgentIroha | (0.62, 0.35) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 8 | agent_kaguya | 四宮輝夜 | AgentKaguya | (0.48, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 9 | agent_kana | 有馬佳奈 | AgentKana | (0.62, 0.32) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 10 | agent_kaori | 白崎香織 | AgentKaori | (0.65, 0.33) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 11 | agent_kirino | 高坂桐乃 | AgentKirino | (0.55, 0.32) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 12 | agent_kurisu | 牧瀬紅莉栖 | AgentKurisu | (0.40, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 13 | agent_makima | 瑪奇瑪 | AgentMakima | (0.42, 0.28) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 14 | agent_mashiro | 月ノ森真白 | AgentMashiro | (0.42, 0.28) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 15 | agent_minami | 七海深奈実 | AgentMinami | (0.65, 0.35) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 16 | agent_reze | 蕾賽 | AgentReze | (0.45, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 17 | agent_rin | 遠坂凜 | AgentRin | (0.40, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 18 | agent_rio | 双葉理央 | AgentRio | (0.45, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 19 | agent_sachi | 海野幸 | AgentSachi | (0.58, 0.33) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 20 | agent_sakura | 間桐櫻 | AgentSakura | (0.50, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 21 | agent_shea | 希雅 | AgentShea | (0.65, 0.33) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 22 | agent_shizuku | 八重樫雫 | AgentShizuku | (0.42, 0.28) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 23 | agent_tsukasa | 由崎司 | AgentTsukasa | (0.52, 0.30) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |
| 24 | agent_yue | 月 | AgentYue | (0.38, 0.25) | ✔✔✔✔✔✔✔✔ | ✔ | 1 |

「五面齊備」欄實際為 **8 個勾**：personas 檔 / consciousness 子類 / AGENT_CLASS_MAP / configs agents / BASE_SCORES / proxy display-name / proxy short-name / proxy identity rules。

---

## 5. 註冊面鍵數實測

| 面 | 鍵數 | 期望 |
|---|---|---|
| `personas/agent_*.md` | 34 | 34 ✔ |
| `configs/default.yaml` 的 `agents:` | 34 | 34 ✔ |
| `AGENT_CLASS_MAP` | 34 | 34 ✔ |
| `BASE_SCORES` | 34 | 34 ✔ |
| `_AGENT_IDENTITY_RULES` | 34 | 34 ✔ |
| `AGENT_NAMES`（display-name） | 27 | 既有 3 ＋ 本批 24 ✔ |
| `AGENT_SHORT_NAMES`（short-name，新建） | 27 | 既有 3 ＋ 本批 24 ✔ |
| `AGENT_PROFILE_MAP`（Hermes） | 10 | **不動**（LAND-0 §5）✔ |

---

## 6. 變異實驗（7 條，全部轉紅）

| # | 變異 | 轉紅的守衛 | 紅燈實測 |
|---|---|---|---|
| 1 | `personas/agent_ai.md` 首行 `# SOUL.md` → `# SOUL.MD`（改一字） | 守衛 3（位元等價） | `AssertionError: agent_ai: 落地檔與素材位元不同` → `1 failed` |
| 2 | `personas/agent_ai.md` 覆蓋成無陰影條款的素材（`agent_beatrice.md`） | 守衛 4（陰影門閘） | `AssertionError: agent_ai: ^## 陰影條款 計數 = 0（應為 1）` → `1 failed` |
| 3 | 新建 `personas/agent_beatrice.md`（落地 batch2 之一） | 守衛 5（batch2 未落地） | `AssertionError: batch2 agent_beatrice 已被落地` → `1 failed` |
| 4 | `src/agent/speaker_token.py` 刪掉 `BASE_SCORES` 的 `agent_ai` 一筆 | 守衛 1（五面齊備） | `AssertionError: agent_ai: BASE_SCORES 不符` → `1 failed` |
| 5 | `src/memory/sage/provider.py` 的 `NO_DIARY_AGENTS` 提前加 `agent_beatrice` | 守衛 6 | `AssertionError: NO_DIARY_AGENTS 被提前擴充：{'agent_beatrice', 'agent_ram'}` → `1 failed` |
| 6 | `configs/default.yaml` 既有 `agent_yua` 的 `intimacy_level` 80 → 81 | 守衛 7 | `AssertionError: 既有 agent_yua intimacy_level 被改：81 != 80` → `1 failed` |
| 7 | `src/llm/proxy.py` 覆蓋成戰役 A（`82de5cb`）之前的版本（接線＋三表全沒） | 既有戰役 A 測試 `test_guard4c_proxy_wiring_is_off_by_default` | `AttributeError: module 'src.llm.proxy' has no attribute '_format_piggyback_block'` → `1 failed` |

每條變異的紅燈皆為**真實斷言失敗**（非「因攔截而紅」），且每次變異後皆以位元比對還原（`byte-identical ✔`）。

---

## 7. 測試結果

**口徑鐵律**：一律以 repo 內 `.venv` 直譯器（`.venv\Scripts\python.exe -m pytest`）。

### 7.1 聚焦（本票新增守衛）

```
.venv\Scripts\python.exe -m pytest -q tests/agent/test_agent_registry_land0_batch1.py
→ 19 passed in 1.06s
```

19 支＝7 條守衛（§3.7）＋ 4 個聚焦面（registry `get_agent_class` / speaker bus `bid`+`resolve` / configs `yaml.safe_load` / `load_persona` 本地優先 ＋ germ 不讀本地 persona）。

### 7.2 全庫回歸（baseline vs 落地後）

| 輪次 | 指令 | 結果 |
|---|---|---|
| **baseline**（HEAD `82de5cb`，本票改動以 `git stash` 暫存、其餘檔案歸零） | `.venv\Scripts\python.exe -m pytest -q tests` | **6866 passed, 24 skipped, 27 subtests passed, 0 failed**（778.80s / 12:58） |
| **落地後第 1 輪** | 同上 | 6897 passed, 24 skipped, **2 failed**（798.48s / 13:18） |
| **落地後第 2 輪（最終）** | 同上 | **6899 passed, 24 skipped, 27 subtests passed, 0 failed**（766.48s / 12:46，exit code 0） |

⇒ **相對 baseline：新增失敗 0、既有失敗 0。**（+33 passed＝本票 19 支新測試 ＋ 平行工單的 14 支新測試）

### 7.3 第 1 輪那 2 個紅燈的歸因（**皆非本票造成**）

兩筆紅燈的失敗訊息都**逐一列名**了違規檔案，兩者皆屬**另一張平行工單**（SIM-EPIS-ACTIVATE-READY）在本票執行期間新增的檔案：

1. `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked`
   —— offenders：`tests/inner_life/test_epistemic_scheduled_trigger.py`、`tests/inner_life/test_epistemic_tension_store.py`（該 worker 的新增測試檔，當時尚未 `git add`）。
   本票的新測試檔 `tests/agent/test_agent_registry_land0_batch1.py` **已 `git add`**（本票必須如此：repo 的 `TEST-REPO-HYGIENE-1` 要求 `tests/**` 下的可收集測試檔必須被 git 追蹤）⇒ 不在 offenders 內。
2. `tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched`
   —— `src_changes - allowed = {'src/inner_life/epistemic_tension_scheduler.py'}`（該 worker 的新增模組）；本票的 4 個 `src/` 檔（`consciousness`／`registry`／`speaker_token`／`proxy`）**全部已在 `allowed` 內**。

⇒ **相對 baseline，本票造成的新增失敗＝0。** 該 2 筆在該 worker 補上 `git add` 與 `allowed` 條目後即轉綠（第 2 輪驗證）。

### 7.4 變異實驗（7 條全紅）

（見 §6；每條變異的檔案皆以位元比對還原，`byte-identical ✔`）

### 7.5 聚焦測試口徑說明

LAND-0「測試」節寫的是「36 鍵聚焦」；本票只落地 24 隻（batch2 的 12 隻待 SHADOW-0）⇒ 聚焦測試覆蓋 **24 鍵**，第 2 批落地時補齊為 36 鍵。

---

## 8. Frozen Contract 與意外行為

- **Frozen Contract 零觸碰**：`src/agency/stages.py`／`trigger_handler.py`／`event_handler.py`／`dream_handler.py`／`diary_handler.py`／`src/inner_life/submission_gate.py`／`src/social/identity_firewall.py`／`src/social/producer_gate.py`／`src/world/middleware.py`／`src/memory/sage/writer.py`／`src/soul/decision.py` 全部未改（`git diff --name-only HEAD` 可驗）。
- **基底類別未改**：`AgentConsciousness` 與既有 10 個子類皆未動（24 個新子類全部追加在檔尾）。
- **戰役 A 接線未覆蓋**：`src/llm/proxy.py` 以 `82de5cb` 為基準做增量修改；`system_parts` 組裝、recall 區塊、epistemic／piggyback 區塊、`_format_piggyback_block` 全部保留（變異 7 實測：該接線被舊版覆蓋時既有測試即轉紅 ⇒ 守衛有效）。
- **未寫入 `data/**`、未重啟服務、未動任何行程**（本票只改 repo 檔案；註冊面皆於啟動時載入 ⇒ 生產零行為變化）。
  證據：`data/agents/` 內**只有既有 10 隻**的目錄，24 隻新角色**零資料目錄**（註冊面尚未被載入）；生產服務 trampoline PID 20716 的啟動時間為 **2026-10-21 17:22:11**（早於本票所有改動）⇒ 運行中的服務仍是**改動前**的程式碼。本票執行的 pytest 全部經 `tests/conftest.py` 的 autouse fixture 指向 tmp 資料根（零生產寫入）。
- **`NO_DIARY_AGENTS` 未提前擴充**（維持 `{"agent_ram"}`）⇒ 不存在指向未註冊 agent_id 的懸空引用。

### 8.1 ⚠️ 執行期間的平行改動（`src/llm/proxy.py`）

本票執行期間（19:05–20:1x）另一張平行工單 **SIM-EPIS-ACTIVATE-READY** 同時在改 `src/llm/proxy.py`（`_claim_background_action_slot` 與排程觸發接線）與 `src/inner_life/epistemic_action.py`，並新增 `src/inner_life/epistemic_tension_scheduler.py` 與兩個測試檔。

處理方式（**未覆蓋對方任何一行**）：

- 對方的 proxy.py hunk 位於 **1295–1373 與 4740–4818**；本票的 hunk 位於 **74–131（兩張名稱表）與 2242 起（identity rules）**⇒ 區段完全不重疊。
- 對方先在 working tree 落地後，本票的 `git stash pop` 因 proxy.py 衝突而中止（stash 的 untracked 部分已還原）；本票改以**逐段重新套用**的方式把三張表加回對方版本（`AGENT_NAMES` / `AGENT_SHORT_NAMES` / `_AGENT_IDENTITY_RULES`），**不覆蓋對方的接線**。
- 實測確認：`src/llm/proxy.py` 同時含有 `_format_piggyback_block`（戰役 A）與 `_claim_background_action_slot`（平行工單）。
- 對方隨後自行把 `src/inner_life/epistemic_tension_scheduler.py` 加入 `tests/test_observability_completeness_1.py` 的 `allowed` 集合（與本票的 3 筆並列，互不干擾）。

**殘餘風險**：兩個工單都改同一個檔案，最終合併時需以 `git diff` 逐段確認本票的三張表與對方的接線同時存在（本票已驗證，但**主大腦合入時仍需複核**）。

---

## 9. 本票未解決的殘餘問題

1. **batch2 的 12 隻**（beatrice／chizuru／emilia／ichika／itsuki／mami／nagisa／nino／rika／saki／shino／yotsuba）待 **SHADOW-0** 補齊陰影層後落地；屆時 `NO_DIARY_AGENTS` 一次擴為 5 筆（§0.4 裁定）。
2. **類別名**待 Notion 原「類別註冊表」核對（見 §3.1）。
3. **proxy 三表鍵數**在全 campaign 末端的收斂方式待裁決（見 §3.3）。
4. **新落地角色的主動發話閾值／意圖草稿未定稿** ⇒ 本批一律 fail-closed（不主動插話；回應使用者發話時草稿由 proxy 以使用者訊息覆寫）。人格細節待後續票補。
5. **排版美化**（`•`→`-` 等）依 Owner 裁決另立 LINT-CHORE 票。
