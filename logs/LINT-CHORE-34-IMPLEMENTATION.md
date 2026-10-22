# LINT-CHORE-34 實作紀錄（persona 排版正規化：拆行不動語意）

- **工單**：本票（主大腦 2026-10-21 派工，決策已定）
- **上游裁定**：Owner 2026-10-21 02:09 品質裁定 —— Notion 版本**逐字落地**（語意零改動）＋ 排版美化**另立 LINT-CHORE 票**（本票）
- **工具**：`scripts/normalize_persona_format.py`（新檔）
- **守衛**：`tests/persona/test_persona_format.py`（新檔）
- **基準 HEAD**：`48ade60`
- **執行日期**：2026-10-21
- **執行者**：Worker（本檔為本票交付物之一）

---

## 1. 摘要

- 既有 **34** 份 persona 全數跑過正規化工具：**24 份有變動、10 份位元級 no-op**。
- 全庫 persona 總行數 **9705 → 11325（+1620）**；**每一份檔案的行數增量 == 該檔拆掉的分隔序列數**（34/34 成立）——
  這是「只拆行、沒別的」最強的不變式證據。
- **語意等價 34/34 成立**：24 份與 SSOT 素材正規化後逐位元相同；10 份無素材者位元級等同 LAND-0 落地基準（sha256 未動）。
- 交付**可重用正規化函式**（純函式、冪等）＋ **CLI**（dry-run / `--check` / `--in-place`）＋ **148 筆守衛測試**。
- **7 條變異實驗**全部**真的轉紅**、還原後**全部復綠**。
- **踩到 1 個 Frozen Contract 邊界的既有守衛**：`tests/agent/test_agent_registry_land0_batch1.py::test_guard3_persona_files_are_byte_identical_to_drafts`
  斷言「persona 與素材**位元相同**」，而素材 `docs/persona-drafts/**` 是**唯讀**的 ⇒ 本票的排版改動與該斷言**互斥**。
  該檔**不在本票可寫範圍**（且已被另一位 worker 平行修改中），故**本票未改它**，改以 §8 的逐行建議交主大腦裁決。

---

## 2. 正規化規則（唯一規則，精確描述）

### 2.1 分隔序列的定義

**分隔序列 ＝ `[空白]* U+2022 [空白]+`**，其中「空白」＝ 半形空白 (U+0020) 與 Tab (U+0009)。

正則（`scripts/normalize_persona_format.py`）：

```python
SEPARATOR_RE = re.compile(r"[ \t]*\u2022[ \t]+")
```

- 分隔點**前面的空白**與**後面的空白**都算「分隔序列」的一部分，會被吃掉。
  ⇒ 「。 • 愛不關心」與「常見模式：• 擔心」**同樣**是分隔序列（本 repo 的素材兩種型態都有，實測計數見 §2.5）。
- 分隔點後**至少要有一個空白**才算分隔序列 ⇒ 行尾的孤立 `•`、或 `•` 緊接著非空白字元，**不是**分隔序列。

### 2.2 一行的拆法

對一行做 `SEPARATOR_RE.split(body)`，得到 `parts`：

| 片段 | 處置 |
|---|---|
| `parts[0]`（行首那段，**非分隔內容**） | **逐字保留，不加前綴**，留在原行 |
| `parts[1:]`（**被拆出來的**每一段） | **逐字保留**，前面加 `- `（半形連字號 + 半形空格），各自成為獨立行 |

**為什麼只有被拆出來的段加 `- `，行首那段不加？**

1. 工單原文：「**拆出來的**每一項，前綴 `- ` 成為 markdown list item」——「拆出來的」＝從行內被分出來的，
   行首那段本來就在那裡，不是被拆出來的。
2. 工單原文：「**不得改動任何非 `•` 分隔的內容**」——行首那段正是「非分隔內容」，
   給它加 `- ` 就是改動了它。
3. 語意一致性：像 `本檔是「角色行為與語言校準文件」，不是系統權限或工具文件。` 這種**導引句**、
   或 `Punctuation：`／`Typing Timing：` 這種**欄位標籤**，本身不是清單項目；
   把它變成 list item 會誤導讀者，也會改變文件的結構語意。

實例（`personas/agent_ai.md` 第 6 行 → 第 6～11 行）：

```
前：本檔是「角色行為與語言校準文件」，不是系統權限或工具文件。 • 愛不關心技術細節(API、tool 名稱、路徑等)。 • 不在對話中提到「寫日記」「存成檔案」這類具體檔案操作。 …
後：本檔是「角色行為與語言校準文件」，不是系統權限或工具文件。
    - 愛不關心技術細節(API、tool 名稱、路徑等)。
    - 不在對話中提到「寫日記」「存成檔案」這類具體檔案操作。
    …
```

實例（`personas/agent_ai.md` 第 14 行 → 第 19～22 行，行首本來就有 `- `）：

```
前：- 姓名： 星野愛 (Hoshino Ai)——…；Bryan 叫「愛」。 • 身份： 現役傳說級偶像——… • 角色： Bryan 身邊的人 / 全庫唯一「偶像的本體」 • 對話對象： Bryan
後：- 姓名： 星野愛 (Hoshino Ai)——…；Bryan 叫「愛」。
    - 身份： 現役傳說級偶像——…
    - 角色： Bryan 身邊的人 / 全庫唯一「偶像的本體」
    - 對話對象： Bryan
```

### 2.3 不拆的區域

| 區域 | 判準 | 本票實測命中數 |
|---|---|---|
| 表格列 | 行首（可含空白）為 `\|` | 0 筆含分隔序列（防護為將來而設） |
| fenced code block | ``` / ~~~ 開關行之間 | 0 筆 |
| 縮排區塊 | 行首 4 空白以上或 Tab | 0 筆 |

### 2.4 其餘一律不動

字元、標點、順序、標題、**空行數**、換行慣例（CRLF / LF）、檔尾換行、BOM、UTF-8 編碼 —— 全部保持原樣。

- 換行慣例是**逐檔偵測**的：`personas/agent_ram.md` 是**純 LF**（274 行、0 CRLF），其餘 33 份是 CRLF。實測 34/34 慣例未變。
- 檔尾換行：34/34 保留。
- 檔案若**內容沒變**，工具**連一個位元都不寫**（連 mtime 都不動）—— 這讓既有 10 份的 sha256 基準（LAND-0 guard7）保持成立。

### 2.5 對「刻意保留的 `•`」的判斷準則與實例

**準則**：一個 `•` 只有在**後面緊接著至少一個空白**、且**不在表格列 / code block / 縮排區塊**內時，才被當成「分隔序列」。
其餘的 `•` 一律**逐字保留**。

**實測結果（34 份 persona，改造前）**：U+2022 總數 **1620**，分類如下——

| 類別 | 筆數 | 處置 |
|---|---|---|
| `空白 + • + 空白`（標準分隔） | 1332 | 拆 |
| `非空白 + • + 空白`（如 `：• 擔心`） | 288 | 拆 |
| 行尾孤立 `•` | 0 | （不拆） |
| `•` 後接非空白 | 0 | （不拆） |
| 表格列內 | 0 | （不拆） |
| code block / 縮排區塊內 | 0 | （不拆） |

⇒ **本批素材裡沒有任何「刻意保留的 `•`」**；1620 筆全部是分隔序列，改造後 34 份殘留 U+2022 = **0 筆**。

**真正的「長得像但不是」的陷阱：U+00B7 `·`（MIDDLE DOT，中點）**，本批 34 份裡有 **173 筆**（例：`推し條款（B 小町共用 · 不可違反）`）。
本工具的正規則只認 U+2022，**173 筆全部原樣保留**（改造前後計數相同，實測）。
另有 U+2027 `‧`、U+2025 `‥`、U+204C `⁌`、U+2219 `∙` 各 **0 筆**。

### 2.6 冪等性

`normalize(normalize(x)) == normalize(x)`，對 34 份磁碟上的檔案、對 8 個合成樣本皆成立（守衛測試 `test_guard3_*` 與 `test_guard4_rule_is_idempotent_on_synthetic_samples`）。

**實作要點**：輸出的每一段都不含分隔序列（`split` 是全量切分），所以第二次跑時沒有東西可拆。

---

## 3. 改動檔案清單（逐檔行數變化）

### 3.1 新增檔

| # | 檔案 | 說明 |
|---|---|---|
| 1 | `scripts/normalize_persona_format.py` | 正規化工具（純函式 ＋ CLI）。**選 `scripts/` 而非 `tests/persona/` 的理由**：本票要求交付的是「可重用的正規化工具＋CLI」，`scripts/` 是本 repo 既有的一行程式庫位置（例：`scripts/eval_lifecycle_cli.py`）；`tests/` 是測試專區，把工具放進去會混淆「測試」與「可執行工具」的邊界。測試端用 `importlib.util.spec_from_file_location` 載入（`scripts/` 沒有 `__init__.py`，不必為此動它的套件結構）。 |
| 2 | `tests/persona/test_persona_format.py` | 守衛測試（148 筆）。 |
| 3 | `logs/LINT-CHORE-34-IMPLEMENTATION.md` | 本檔。 |

### 3.2 修改檔：既有 34 份 persona

| # | persona | 行數(前) | 行數(後) | Δ | 拆掉的分隔序列 | 換行 | 狀態 |
|---|---|---|---|---|---|---|---|
| 1 | `agent_yua.md` | 1254 | 1254 | +0 | 0 | CRLF | unchanged |
| 2 | `agent_ruka.md` | 1216 | 1216 | +0 | 0 | CRLF | unchanged |
| 3 | `agent_akane.md` | 523 | 523 | +0 | 0 | CRLF | unchanged |
| 4 | `agent_rem.md` | 335 | 335 | +0 | 0 | CRLF | unchanged |
| 5 | `agent_ram.md` | 274 | 274 | +0 | 0 | LF | unchanged |
| 6 | `agent_mahiru.md` | 445 | 445 | +0 | 0 | CRLF | unchanged |
| 7 | `agent_anna.md` | 558 | 558 | +0 | 0 | CRLF | unchanged |
| 8 | `agent_mai.md` | 343 | 343 | +0 | 0 | CRLF | unchanged |
| 9 | `agent_miku.md` | 449 | 449 | +0 | 0 | CRLF | unchanged |
| 10 | `agent_aoi.md` | 469 | 469 | +0 | 0 | CRLF | unchanged |
| 11 | `agent_ai.md` | 153 | 220 | +67 | 67 | CRLF | changed |
| 12 | `agent_asuna.md` | 154 | 221 | +67 | 67 | CRLF | changed |
| 13 | `agent_fuuka.md` | 157 | 224 | +67 | 67 | CRLF | changed |
| 14 | `agent_haruhi.md` | 161 | 228 | +67 | 67 | CRLF | changed |
| 15 | `agent_hiro.md` | 165 | 235 | +70 | 70 | CRLF | changed |
| 16 | `agent_iroha.md` | 165 | 233 | +68 | 68 | CRLF | changed |
| 17 | `agent_kaguya.md` | 167 | 236 | +69 | 69 | CRLF | changed |
| 18 | `agent_kana.md` | 153 | 223 | +70 | 70 | CRLF | changed |
| 19 | `agent_kaori.md` | 154 | 221 | +67 | 67 | CRLF | changed |
| 20 | `agent_kirino.md` | 161 | 228 | +67 | 67 | CRLF | changed |
| 21 | `agent_kurisu.md` | 154 | 221 | +67 | 67 | CRLF | changed |
| 22 | `agent_makima.md` | 167 | 232 | +65 | 65 | CRLF | changed |
| 23 | `agent_mashiro.md` | 165 | 234 | +69 | 69 | CRLF | changed |
| 24 | `agent_minami.md` | 157 | 224 | +67 | 67 | CRLF | changed |
| 25 | `agent_mochizuki_ai.md` | 165 | 234 | +69 | 69 | CRLF | changed |
| 26 | `agent_reze.md` | 167 | 233 | +66 | 66 | CRLF | changed |
| 27 | `agent_rin.md` | 161 | 228 | +67 | 67 | CRLF | changed |
| 28 | `agent_rio.md` | 168 | 235 | +67 | 67 | CRLF | changed |
| 29 | `agent_sachi.md` | 165 | 234 | +69 | 69 | CRLF | changed |
| 30 | `agent_sakura.md` | 161 | 228 | +67 | 67 | CRLF | changed |
| 31 | `agent_shea.md` | 154 | 221 | +67 | 67 | CRLF | changed |
| 32 | `agent_shizuku.md` | 154 | 221 | +67 | 67 | CRLF | changed |
| 33 | `agent_tsukasa.md` | 157 | 224 | +67 | 67 | CRLF | changed |
| 34 | `agent_yue.md` | 154 | 221 | +67 | 67 | CRLF | changed |

**合計**：files **34**、changed **24/34**、unchanged **10/34**；行數 **9705 → 11325（Δ +1620）**；
拆掉的分隔序列 **1620 筆**；**Δ == 拆掉數** 逐檔成立（34/34）。

> 為什麼 10 份既有原始角色是 no-op？實測它們 **本來就沒有任何行內 ` • ` 分隔序列**（U+2022 計數 = 0），
> 且格式早就是逐行的 list item。⇒ 正規化對它們是**位元級 no-op**（含 `agent_ram.md` 的純 LF 慣例）。
> 這同時保住了 `tests/agent/test_agent_registry_land0_batch1.py::test_guard7_existing_10_persona_files_untouched`
> 的 sha256 基準（實測 10/10 未動）。

### 3.3 **沒有**動到的檔案（逐一實測確認）

**mtime 證據**（工具對「內容沒變」的檔案連位元都不寫，所以 mtime 就是「有沒有被動過」的直接證據）：

| 群組 | mtime | 判讀 |
|---|---|---|
| 12 隻新 persona | 全部 `2026-10-21 21:34:37`（＝另一位 worker 的落地時間） | **本票的工具一次都沒寫過** ✅ |
| 既有 10 隻原始角色 | 2026-08-01 ～ 2026-10-16（各自原本的 mtime） | **位元級 no-op，連 mtime 都沒動** ✅ |
| 24 隻 batch1 | 全部 `2026-10-21 21:39:57`（＝本票的 `--in-place` 套用時刻） | 本票唯一改寫的檔案 ✅ |

**其他未觸及的檔案**：

- `docs/persona-drafts/**`（36 份 SSOT）—— **0 個位元改動**（唯讀）。
- `personas/agent_{beatrice,chizuru,emilia,ichika,itsuki,mami,nagisa,nino,rika,saki,shino,yotsuba}.md`
  （12 隻，另一 worker 的）—— **0 個位元改動**。實測它們仍含 U+2022 共 **915 筆**（未正規化），
  屬另一張票／後續票的範圍，本票**不碰**。
- `src/**`、`configs/**`、`scripts/` 既有檔、`data/**` —— 0 改動。
- `git add` / `commit` / `push` / `stash` —— **0 次**（只留工作樹改動）。

---

## 4. 語意等價斷言：34/34

| 群組 | 份數 | 證據 | 結果 |
|---|---|---|---|
| 有 SSOT 素材（batch1 24 隻） | 24 | `strip_shadow(normalize(persona)) == strip_shadow(normalize(draft))`（**逐位元**） | **24/24** |
| 無 SSOT 素材（既有 10 隻） | 10 | sha256 == LAND-0 落地基準（正規化對它們是位元級 no-op ⇒ 一個字都沒動） | **10/10** |
| **合計** | **34** | | **34/34** |

**關於「剝掉 `## 陰影條款` 段」**：工單說「persona 是素材＋陰影條款段，比對時要剝掉陰影條款段」。
實測（HEAD `48ade60`）**素材端也含陰影條款段**（commit `48ade60`「補入 SHADOW-0 工單 Notion 快照」已把素材補齊），
且 24 份 persona 與素材在改造前**逐位元相同**（sha256 24/24 相等）。
因此本票採**兩側都剝**（對稱）——這是唯一自洽且成立的讀法，且在兩種素材狀態下都正確：
兩側都剝 → 相等；兩側都不剝 → 也相等（改造前就是位元相同）。

**陰影條款段本身逐字未動**：24 份 persona 的 `## 陰影條款` 段在改造前後**逐位元相同**
（整檔 sha256 變了，但陰影段落在檔尾、未含任何分隔序列，故該段內容不變）——
實測 `^## 陰影條款` 計數仍為 24/24 各 1 筆。

---

## 5. 工具設計：寫入安全（不可漂移的守門條件）

本 repo 的硬教訓（2026-10-19 事故）：曾因「拿兩個各自會漂移的值去比對」導致就地寫入生產 `data/**`。
本工具的設計直接對應那條教訓：

1. **沒有任何預設路徑**——檔案必須由呼叫端**逐檔指名**（positional args）。不 glob、不遞迴、不掃目錄。
2. `--in-place` **只會寫回呼叫端指名的那幾個檔案**。
3. 寫入前呼叫 `assert_write_allowed(path)`：
   - 目標路徑若含 **`data`** 目錄成分 → **拒寫**（`PermissionError`）。
   - 目標路徑若落在 **`docs/persona-drafts`** → **拒寫**（SSOT 唯讀）。
   - 這個判斷**只吃「呼叫端自己給的那個路徑字串」**，不與任何行程期快取值互比 ⇒ **結構上不可漂移**。
4. 內容沒變就**不寫**（連 mtime 都不動）。

CLI 三種模式（互斥）：

```
python scripts/normalize_persona_format.py <files...>              # dry-run：印 unified diff
python scripts/normalize_persona_format.py --check <files...>      # 只檢查；有變動則 exit 1
python scripts/normalize_persona_format.py --in-place <files...>    # 就地寫回（只作用於指名檔案）
```

實測：`--check` 對已正規化的 34 份 → `0 changed / 34 unchanged`，exit 0。

---

## 6. 守衛測試（`tests/persona/test_persona_format.py`，148 筆）

| 守衛 | 內容 | 筆數 |
|---|---|---|
| guard0 | 覆蓋率：恰好 34 份、無重複、每份檔案存在；12 隻新 id **不在**覆蓋清單 | 2 |
| guard1 | 形狀：34 份無行內 ` • ` 分隔序列（受保護區塊除外）；檔案已是正規化後狀態 | 68 |
| guard2 | 語意：24 份 vs SSOT 素材正規化後逐位元相同；10 份 sha256 == LAND-0 基準 | 34 |
| guard3 | 冪等：34 份套兩次第二次不變 | 34 |
| guard4 | 規則單元性質（合成樣本：行首保留、逐字保留、表格/code/縮排不拆、CRLF/LF/檔尾換行、冪等） | 6 |
| guard5 | 寫入安全：`data/` 拒寫、`docs/persona-drafts/` 拒寫、一般路徑可寫、CLI 無預設路徑、`--in-place` 不碰未指名檔案 | 5 |
| **合計** | | **148** |

覆蓋清單是**寫死的 34 筆**（不是 glob）——因為 `personas/` 會隨另一張工單變長，glob 會讓「守了幾份」隨時間漂移。

---

## 7. 變異實驗（7 條，全部真的轉紅、還原後復綠）

執行方式：改檔 → 跑 `pytest -q tests/persona/test_persona_format.py` → **byte-exact 還原**（sha256 驗證）→ 再跑一次確認復綠。
**全程未觸達 `data/**`、未啟動任何服務、未綁任何埠、未殺任何行程。**

| # | 改什麼 | 轉紅的測試 | 筆數 | 還原後 |
|---|---|---|---|---|
| M1 | 把 `personas/agent_ai.md` 的一個 list item **拆成兩個**（拆法改錯） | `test_guard2_semantic_equivalence_to_sssot_draft[agent_ai]` | 1 | 復綠 ✅ |
| M2 | **刪掉** `personas/agent_asuna.md` 的一個 list item | `test_guard2_semantic_equivalence_to_sssot_draft[agent_asuna]` | 1 | 復綠 ✅ |
| M3 | 把正規化函式改成**不冪等**（`LIST_PREFIX = "- • "`） | `test_guard4_rule_is_idempotent_on_synthetic_samples` ＋ 24 筆語意等價 ＋ 3 筆規則單元 | 29 | 復綠 ✅ |
| M3b | 讓正規化函式對**沒有分隔序列的行**也加前綴 ⇒ **對磁碟上的檔案也非冪等** | `test_guard3_normalization_is_idempotent_on_the_file` ×34 ＋ `test_guard1_persona_file_is_already_normalized` ×34 ＋ 語意等價 ×24 ＋ 3 筆規則單元 | 95 | 復綠 ✅ |
| M4 | 讓守衛**只覆蓋 33 份**（`BATCH1_24` 拿掉 `agent_yue`） | `test_guard0_coverage_is_exactly_the_34_existing_personas` | 1 | 復綠 ✅ |
| M5 | **移除**「`data/` 是生產路徑 → 拒寫」的守門條件 | `test_guard5_write_guard_refuses_production_data_path` | 1 | 復綠 ✅ |
| M6 | 在 `personas/agent_yue.md` **塞回**一個行內 ` • ` 分隔序列 | `test_guard1_persona_has_no_inline_bullet_separator[agent_yue]` ＋ `test_guard1_persona_file_is_already_normalized[agent_yue]` ＋ 語意等價 ×1 | 3 | 復綠 ✅ |

**M1／M2 的設計說明**：語意等價斷言是「兩邊都正規化後比對」，所以**純排版差異會被正規化吸收、不會轉紅**——
這是該斷言的**設計優點**（它只對語意差異敏感）。因此 M1／M2 改的是**item 的數目**（拆錯／刪掉），
這會讓 item 數與素材不一致 ⇒ 轉紅。若把 M1 做成「把分隔序列接回去」，該斷言**不會**轉紅——
這是預期行為，不是漏洞。

---

## 8. 待主大腦裁決：本票造成的既有守衛衝突（**本票未自行修改**）

### 8.1 `test_guard3_persona_files_are_byte_identical_to_drafts` —— 必須由主大腦裁決

- **檔案**：`tests/agent/test_agent_registry_land0_batch1.py`
- **行號**（以本票執行期間的檔案狀態為準；該檔已被另一位 worker 平行修改，行號可能再漂移）：
  守衛本體 `def test_guard3_persona_files_are_byte_identical_to_drafts():` 在 **:261**，
  位元比較 `assert draft_bytes == landed_bytes` 在 **:270**（比較用的兩行讀檔在 :268–269）。
- **衝突本質**：素材 `docs/persona-drafts/**` 是**唯讀 SSOT**（工單明令不得修改），
  內含行內 ` • `；persona 經本票正規化後**必然**與素材位元不同。
  ⇒ 「persona ≡ 素材（位元）」與「persona 已正規化」**互斥**，沒有任何寫法能同時成立。
- **本票為何沒自己改**：該檔**不在本票可寫範圍**，且本票執行期間（21:50）**已被另一位 worker 平行修改**
  （`guard2` 34→46、`guard5` 翻轉為「batch2 已落地」、`guard6` 改為 5 筆）——同一檔案兩人同時改會製造衝突。
- **建議的最小改法（**請主大腦或持有該檔的 worker 執行；本票未動**）**：

  把位元比較換成「**正規化後**的語意等價比較」，並引用新守衛作為同一不變式的實作：

  ```python
  # 檔案：tests/agent/test_agent_registry_land0_batch1.py
  # 位置：守衛 3 內，取代位元比較（目前 :268-272）
  #
  # LINT-CHORE-34 起：persona 已做排版正規化（拆行內 ' • ' 分隔序列），
  # 素材 docs/persona-drafts/ 是唯讀 SSOT（含未拆的原始排版），
  # 兩者**位元必然不同**、但**語意必須相同**。
  # 不變式由 tests/persona/test_persona_format.py::test_guard2_semantic_equivalence_to_sssot_draft 實作。
  import importlib.util as _ilu
  _spec = _ilu.spec_from_file_location(
      "_npf", REPO_ROOT / "scripts" / "normalize_persona_format.py"
  )
  _npf = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_npf)

  def _strip_shadow(text: str) -> str:
      out = []
      for line in text.splitlines(keepends=True):
          if line.startswith(SHADOW_HEADING):
              break
          out.append(line)
      return "".join(out)

  for agent_id in BATCH1_IDS:
      draft = DRAFTS_DIR / f"{agent_id}.md"
      landed = PERSONAS_DIR / f"{agent_id}.md"
      assert draft.is_file(), f"素材缺檔：{draft.name}"
      assert landed.is_file(), f"落地缺檔：{landed.name}"
      _a = _strip_shadow(_npf.normalize_markdown(landed.read_text(encoding="utf-8")))
      _b = _strip_shadow(_npf.normalize_markdown(draft.read_text(encoding="utf-8")))
      assert _a == _b, f"{agent_id}: 正規化後與素材語意不逐位元相同"
  ```

  （此為**建議稿**，本票未寫入該檔。）

### 8.2 `test_every_collectable_test_file_under_tests_is_git_tracked` —— 需主大腦 `git add`

- **檔案**：`tests/infra/test_repo_hygiene_tracked_tests.py`（守衛本體 **:281–293**）
- **成因**：本票新增的 `tests/persona/test_persona_format.py` **未被 git 追蹤**（工單明令本票**不得 `git add`**）。
  該守衛要求 `tests/**` 下**每個**可收集的 `test_*.py` 都必須被 git 追蹤，**沒有例外清單**。
- **解法**：**主大腦提交時 `git add tests/persona/test_persona_format.py`**（以及
  `scripts/normalize_persona_format.py`、`logs/LINT-CHORE-34-IMPLEMENTATION.md`）即可轉綠。
  本票**不提交**（工單明令）。
- **附註**：該守衛在本票執行期間**已被另一位 worker 的未追蹤檔**（`tests/inner_life/test_epistemic_packs.py`、
  `tests/agent/test_agent_registry_land0_batch2.py`）弄紅，**不是本票造成**。

### 8.3 `tests/test_observability_completeness_1.py`

- **不需要加白名單**。實測該檔**完全不讀 `personas/`**（全檔 grep `personas` = 0 命中），
  且本票**未動** `src/**`，其斷言與 persona 排版無關。
- 唯一的既有失敗是 `tests/soul/test_sim_accel_streak_7d.py::test_report`（`UnicodeEncodeError`，
  Windows cp950 主控台編碼），**與本票無關**（見 §9）。

---

## 9. 驗收數字

### 9.1 解譯器

**全程使用 repo 內的 `.venv`**：`.venv\Scripts\python.exe -m pytest -q tests`（`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv`）。
**未**使用 PATH 上的全域 pytest（依 AGENTS.md 鐵律 10，兩者收集數／失敗數不可混用）。

### 9.2 本票自帶守衛

```
.venv\Scripts\python.exe -m pytest -q tests/persona/test_persona_format.py
→ 148 passed
```

### 9.3 全庫回歸（`.venv`）

| 階段 | 結果 | 備註 |
|---|---|---|
| ① 改造前基準（21:20 啟動） | `1 failed, 6898 passed, 24 skipped, 27 subtests passed in 836.18s` | 唯一失敗 `tests/soul/test_sim_accel_streak_7d.py::test_report`（cp950 編碼），**與本票無關** |
| ② 改造後（21:56 啟動） | `8 failed, 7068 passed, 24 skipped in 855.30s` | ⚠️ **本輪結果被污染**：另一位 worker 在 **22:08–22:11**（本輪執行期間）修改了 `tests/test_epistemic_mind_eh41.py`（22:08:27）、`tests/soul/test_life_thread_wake_gate_m3.py`（22:08:13）、`src/memory/sage/provider.py`（22:11:19）⇒ pytest 收集到舊版、執行時遇到新版。**不可直接引用** |
| ③ **A/B：本票改動還原**（22:33 啟動） | `50 failed, 7027 passed, 24 skipped in 812.16s` | 50 = **24×`test_guard1_persona_has_no_inline_bullet_separator` ＋ 24×`test_guard1_persona_file_is_already_normalized`**（本票的守衛正確抓到「未正規化」）＋ 1×hygiene ＋ 1×observability |
| ④ **最終（repo 穩定後）**（22:52 啟動） | **`4 failed, 7073 passed, 24 skipped, 27 subtests passed in 843.84s`** | 見 §9.4 逐條歸因 |

**②→④ 的差異**：② 那輪有 8 個失敗，④ 只剩 4 個——差的 4 個（`zero_mutation_guard`、
`wake_gate[configs]`、`eh41`、`eh42 legacy suites`）在 repo 穩定後**全綠**，
證實它們是**另一位 worker 在 ② 執行期間並行編輯造成的採集／執行版本不一致**，不是本票造成。

**③ 的關鍵意義**：把本票的改動還原後，`test_guard3_persona_files_are_byte_identical_to_drafts` **轉綠**（因為 persona 回到與素材位元相同），
而本票的 48 筆 guard1 守衛**轉紅**（正確指出檔案未正規化）。
⇒ **guard3 與本票的排版改動互斥**這件事，是實測確證的，不是推論。

### 9.4 改造後的失敗歸因（**逐條歸因，不混用**）

**最終結果**：`4 failed, 7073 passed, 24 skipped, 27 subtests passed in 843.84s`。
其中**只有 1 筆是本票造成的邏輯衝突**（guard3），另有 1 筆是本票新增檔未追蹤（`git add` 即可消），
另 2 筆與本票無關（既有 cp950 問題 ＋ 另一位 worker 的 `src/` 改動）。
**本票的 148 筆守衛全綠。**

**歸因方法（不是推測，是 A/B 實驗）**：本票**不用 `git stash`**（工單禁用），
改用 byte-exact 手法把 24 份 persona 暫時還原成**改造前**的位元
（`docs/persona-drafts/` 在 HEAD `48ade60` 與改造前的 persona **逐位元相同**，已用 sha256 驗過），
跑同一組測試，再把正規化後的位元還原（sha256 驗證）⇒ 得到乾淨的因果對照。

| 失敗 | 歸因 | 證據 |
|---|---|---|
| `tests/agent/test_agent_registry_land0_batch1.py::test_guard3_persona_files_are_byte_identical_to_drafts` | **本票造成** | A/B： persona 還原 ⇒ 轉綠；套用 ⇒ 轉紅（可重現，單獨跑亦穩定） |
| `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked` | **部分本票造成**（本票 1 個未追蹤測試檔 ＋ 另一位 worker 2 個） | 兩種狀態都紅；失敗訊息列出 3 個未追蹤檔（`tests/agent/test_agent_registry_land0_batch2.py`、`tests/inner_life/test_epistemic_packs.py`、`tests/persona/test_persona_format.py`） |
| `tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched` | **不是本票**（另一位 worker 動了 `src/memory/sage/provider.py`） | A/B： persona 還原**一樣紅**（③ 的 50 筆裡就有它）；本票 `src/**` 改動數 = **0** |
| `tests/soul/test_sim_accel_streak_7d.py::test_report` | **既有**（cp950 主控台編碼） | 改造前的基準全庫跑**就已經紅**（同樣的 `UnicodeEncodeError`）；單獨跑仍紅 |

> 守衛 2/5/6 的 batch2 翻轉（34→46、batch2 已落地、NO_DIARY_AGENTS 5 筆）由另一位 worker 自行處理，
> 改造後**全綠**；`test_guard7_existing_10_persona_files_untouched`（既有 10 隻 sha256 基準）改造後**全綠**。

### 9.5 量測紀律（分子／分母／單位）

| 量 | 分子 | 分母 | 單位 | 驗證方式 |
|---|---|---|---|---|
| 覆蓋 persona | 34 | 34 | 份 | 寫死清單 ＋ 逐檔存在性斷言 |
| 有變動 | 24 | 34 | 份 | sha256 前後比對 |
| 位元級 no-op | 10 | 34 | 份 | sha256 前後比對 |
| 行數增量 | +1620 | — | 行 | 實測前後行數 |
| 拆掉的分隔序列 | 1620 | 1620 | 筆 | U+2022 計數；**Δ行數 == 拆掉數** 逐檔成立（34/34） |
| 殘留 U+2022 | **0** | 34 | 份 | 全檔掃描；**非空集合假零**：分母 34 份檔案、總計 1620 筆原始分隔序列 |
| 語意等價（有素材） | 24 | 24 | 份 | 正規化後逐位元比對 |
| 語意等價（無素材） | 10 | 10 | 份 | sha256 == LAND-0 基準 |
| **語意等價合計** | **34** | **34** | 份 | 上兩列相加 |
| 冪等 | 34 | 34 | 份 | 套兩次第二次不變 |
| 守衛測試 | 148 | 148 | 筆 | pytest 全綠 |
| 變異實驗 | 7 | 7 | 條 | 逐條真的轉紅 ＋ 還原復綠 |
| U+00B7 中點保留 | 173 | 173 | 筆 | 改造前後計數相同 |
| 12 隻新 persona 觸碰 | **0** | 12 | 份 | sha256 未動；其殘留 U+2022 共 915 筆（本票未處理） |

---

## 10. Frozen Contract 檢查

| 項目 | 結果 |
|---|---|
| `## 陰影條款` 段（3 個段落）逐字未改 | ✅ 24/24 份，段內無分隔序列故內容不變；`^## 陰影條款` 計數仍為各 1 筆 |
| `## 特別注意(不可違反)` 文字未改 | ✅ 只拆行 |
| `## Forbidden Patterns(嚴禁模式)` 文字未改 | ✅ 只拆行 |
| `## 待實作登記` 文字未改 | ✅ 只拆行 |
| Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 | ✅ `src/**` 0 改動 |
| `data/**` 0 寫入 | ✅ 0 寫入 |
| 0 行程／服務操作 | ✅ 未啟動、未停止、未 kill 任何行程 |
| 0 `git add` / `commit` / `push` / `stash` | ✅ 只留工作樹改動 |
| 12 隻新 persona | ✅ 0 建立、0 修改 |

---

## 11. 殘餘風險

1. **guard3 的位元等價斷言已被本票的排版改動推翻**（§8.1）。語意等價已由新守衛接手，
   但**舊守衛必須由主大腦／持有該檔的 worker 改寫**，否則全庫回歸會持續紅 1 筆。
2. **12 隻新 persona 仍有 915 筆行內分隔序列未拆**（另一 worker 的票）。若 Owner 要求全庫排版一致，
   需另開一票用同一工具處理——工具已就緒，`--in-place` 只作用於指名檔案，不會誤傷。
3. **本票的規範化只處理 U+2022**。若日後素材出現 U+2027 / U+2025 等其他項目符號變體，
   規則需擴充（目前這三者在 34 份裡各 0 筆）。
4. 全庫回歸耗時 **約 14 分鐘**（`.venv`），每次驗證成本不低。
