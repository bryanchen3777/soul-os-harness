# 工單：ELEVATION-RECALL-HARDENING-1 — 回憶通道硬化（隔離永不召回 ＋ 詞元級檢索 ＋ 接線判準量測）

> **狀態**：待派工（決策完備版）
> **日期**：2026-10-19
> **前置**：`ba19efd`（`query_recall` 實作，10/10 綠燈）＋ `docs/ELEVATION-RECALL-CONTRACT.md` ＋ `704f851`／`89397d5`（SOUL-BUG-010 overlay 單點收斂）＋ `c327958`（SOUL-BUG-010-B 生命週期自動老化上線）
> **Owner 授權**：2026-10-19 方向 A（ELEVATION-RECALL-WIRING-1）接入授權；本工單為其**第一階段**（通道硬化），第二階段（prompt 注入）待本工單產出的接線判準達標後另開票。

---

## 0. 為什麼這張票不是「把 query_recall 接進 prompt」

主大腦派 explore 做了唯讀考古（報告存於本工單 §A），用 **59 筆真實 user 訊息 × 生產圖譜**量測，得到三個決定性事實：

| 事實 | 證據 | 後果 |
|---|---|---|
| **可回憶池只有 2 顆** | 生產 18 顆 dormant 中 **16 顆是 `pattern`**，被 `elevation_recall.py:104`（`node_type not in PROJECTABLE_NODE_TYPES`）結構性排除 | 池 = 2 顆 `value` |
| **那 2 顆正是被隔離的污染節點** | 18 個 quarantine `node_id` 與 18 個 dormant `node_id` 完全相同；2 顆可回憶節點正文是日文散文（「台本の余白に、Bryの口癖を…」「秋の朝は光が柔らかくて…」） | **接線 = 召回污染品**，直接違反 SOUL-BUG-010 隔離裁決的「禁止捏造文字」 |
| **子字串檢索與現有內容分佈不相容** | 5 種 query 策略對那 2 顆命中率**全為 0/59**；最長公共子字串上限 2 字元，最佳重疊是日文假名假陽性「ない」＝否定詞 | 「整句子字串」這條路在生產資料上**回憶不到任何東西** |

補充量測：**詞元級匹配在中文內容上是有效的**——jieba 詞元聯集與 CJK 2-gram 對 18 顆 dormant 的命中率皆為 **12/59 = 20.34%**（命中 9／8 顆 unique 節點），遠高於整句子字串的 0/59。⇒ **0/59 的根因不是匹配器不好，是那 2 顆節點是日文、訊息是繁中。**

⇒ **結論**：本票的交付物是「**一條正確、可量測、隨時可接線的通道**」，不是「接線」。理由：
1. 通道目前有**真實漏洞**（隔離污染節點可被召回）——這是**當下就在的缺陷**，不是未來的。
2. 通道目前**永遠回不到東西**（池 2 顆且全是污染品；子字串 0/59）——現在接線只會得到一個看起來在工作、實際永遠空轉的元件。
3. 生產**自然老化 = 0 顆**（18/18 dormant 全是 quarantine；167 筆 transition 全停在 `weakening`），且 **0 顆 superseded**。⇒ 「有東西可回」需要時間（防線二 30 天門檻，現行節點最大年齡約 15.7 天，約 2 週後才可能自然老化出第一批）。
4. 本票要同時交出「**什麼時候可以接線**」的**數據判準**（§D），讓第二階段不靠日期靠事實。

---

## 1. 目標

把回憶通道修到三個狀態：
- **正確**：被隔離的污染節點**在任何檢索路徑下都不可被召回**（硬守衛，單點收斂）。
- **可用**：提供**詞元級**檢索入口（零 LLM、deterministic），使其在**中文內容**上有實測命中率。
- **可量測**：提供一個唯讀量測工具，輸出「回憶池成熟度」與「真實命中率」，並給出**第二階段接線的 go/no-go 判準**。

**一句話**：本票不讓回憶「上線對話」，本票讓回憶**在被接上對話之前先是真的**。

---

## 2. 範圍（你擁有這些檔案的寫入權；其他檔案一律不得動）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `src/inner_life/lifecycle_overlay.py` | **一行 additive**：節點 dict 多帶一個隔離旗標 |
| 2 | `src/inner_life/elevation_recall.py` | 節點過濾管線共用化 ＋ **隔離永不召回硬守衛** ＋ 新增詞元級檢索入口 |
| 3 | `src/inner_life/recall_readiness.py`（**新增**） | 回憶池成熟度 ＋ 真實命中率的**純函式**計算（供 CLI 與測試共用） |
| 4 | `scripts/recall_readiness_cli.py`（**新增**） | 唯讀量測 CLI（沙盒-only，結構上不可能寫生產） |
| 5 | `tests/test_elevation_recall_hardening.py`（**新增**） | 守衛測試 ＋ 變異實驗 |
| 6 | `docs/ELEVATION-RECALL-CONTRACT.md` | **只加附錄**（§8），契約主文 §0–§7 **逐字不動** |

---

## 3. 做法（決策已定，執行者照做）

### 3.1 overlay：多帶一個隔離旗標（**一行**）

`src/inner_life/lifecycle_overlay.py:142-146` 現況：

```python
142:        merged = dict(node)
143:        merged["lifecycle_state"] = event["lifecycle_state"]
144:        if "superseded_by" in event:
145:            merged["superseded_by"] = event["superseded_by"]
```

`_reduce_events`（`:97-117`）**已保留整筆 event dict**（`:116` `latest[node_id] = event`）⇒ 資訊沒丟，只在 merge 層丟。

**改法**（在 `:145` 之後新增）：

```python
        # 隔離旗標（additive，不製造新 lifecycle state）：
        # quarantine 事件（event == "quarantine"）是 SOUL-BUG-010 對
        # 截斷污染節點的處置 —— 它們**不得**被回憶召回（否則等於把
        # 污染正文重新帶回對話面）。自然老化得到的 dormant 不帶此旗標。
        if event.get("event") == "quarantine":
            merged["quarantined"] = True
```

- **additive**：只在事件帶 `event == "quarantine"` 時才加 key；其他節點的 dict **一個 key 都不多**（避免影響 `project_emergent` 與既有消費端）。
- **不製造新 lifecycle state**：`lifecycle_state` 的四態語義不動，`quarantined` 只是**來源標記**。
- ⚠️ 若同一節點先 quarantine 後又 transition，`_reduce_events` 是 last-wins ⇒ 旗標會消失。**現況 0/185 節點有多筆事件**，此風險不成立；守衛測試要鎖住「以最新事件為準」。

### 3.2 recall：共用過濾管線 ＋ 隔離硬守衛 ＋ 詞元級入口

`src/inner_life/elevation_recall.py` 重構為三層：

**(a) 共用管線**（新增私有函式，兩條檢索路徑都走它 ⇒ 守門不散落）：

```python
def _recallable_hits(agent_id, state_filter, *, store_dir) -> List[dict]:
    """回憶可命中節點的**唯一**過濾管線。
    順序與條件（逐字沿用現況，**不得改動語意**）：
      1. agent_id 守門：非空 str；"default"（world node）永不回憶
      2. state_filter 守門：缺省＝{dormant, superseded}；含任何非法態 ⇒ []
      3. node_type ∈ PROJECTABLE_NODE_TYPES（pattern 不回憶）
      4. node_id / content 守門
      5. 🔴 隔離守門：node.get("quarantined") is True ⇒ 跳過
      6. lifecycle_state ∈ 過濾態
    """
```

- 第 5 步是**本票的核心**：隔離節點在**任何**檢索路徑下都不可召回。守門只寫在這一處（符合 SOUL-BUG-010 裁定 1「守門不得散落到第 N 個消費端」）。
- 排序維持 `created_ts asc + node_id asc`（現況不動）。

**(b) 契約路徑 `query_recall`（語意不變）**：
- **簽章逐字不動**：`query_recall(agent_id, query, state_filter=None, *, store_dir=None)`。
- **比對語意不動**：仍是「`query` 整句子字串匹配 `content`」。
- 只做一件事：把節點取得改走 (a) 的共用管線（因此獲得隔離守衛）。
- 回傳形狀不動（六鍵）、失敗靜默不動（`except Exception` → `[]`）。

**(c) 新增詞元級入口 `query_recall_by_tags`（additive，不改契約）**：

```python
def query_recall_by_tags(
    agent_id: str,
    tags: Sequence[str],
    state_filter: Optional[Set[str]] = None,
    *,
    store_dir: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """詞元級回憶查詢（契約 §8 附錄新增，**不**取代 query_recall）。

    比對語意：把 query 與 content 各自**正規化**後，任一 tag 命中即算命中（union）。
      - 正規化：lower()、去除所有空白與標點（`str.translate` + `re.sub`）、全形轉半形
      - tag 守門：非空 str、正規化後長度 ≥ 2、去重、**上限 12 個**（取傳入順序前 12）
      - 不做語義排序、不呼叫 LLM、deterministic
    過濾條件與隔離守門與 query_recall **完全相同**（共用 (a)）。
    """
```

- **為什麼是「新增」而不是「改契約」**：契約 §2.1 的「子字串匹配」在生產資料上被實測證偽（0/59），但**契約主文不動**（避免為了實作而改既有契約）。新增並存入口 = 0 契約變更、兩個路徑可比對。
- **union 而非 intersection**：實測 union 對中文內容命中率 12/59 = 20.34%；intersection 會把命中率壓到近 0。
- **上限 12**：避免長句產生過多低訊噪 token（量測基準用的 jieba 詞元數遠低於此，屬保守上限）。
- 失敗靜默與 `query_recall` 對稱。

**(d) 呈現層 `format_recall_hit` 逐字不動**（契約 §3.1 語氣隔離）。

### 3.3 新增 `src/inner_life/recall_readiness.py`（純函式，零 IO 副作用）

三個**純函式**（IO 注入，便於測試）：

```python
def pool_summary(nodes: List[dict]) -> dict:
    """回憶池成熟度：by_state / by_node_type / quarantined 數 / recallable（= 非隔離且
    四層且 dormant|superseded）/ 需要的欄位鍵名固定。"""

def hit_rate(nodes: List[dict], probes: List[Tuple[str, List[str]]],
             state_filter=None) -> dict:
    """真實命中率：分子＝至少命中 1 顆同 agent 可回憶節點的 probe 數；
    分母＝probe 總數；單位＝probe（probe = (agent_id, tags)）。"""

def go_no_go(summary: dict, hit: dict) -> dict:
    """第二階段接線判準（門檻見 §D）。純判定，輸出 reason 欄位。"""
```

### 3.4 新增 `scripts/recall_readiness_cli.py`（唯讀量測 CLI）

**必須遵守本 repo 已建立的沙盒化紀律**（見 `AGENTS.md` 行程安全鐵律 12 與 `scripts/eval_lifecycle_cli.py` 的做法）：

- **來源唯讀 ＋ 唯一寫入目標＝自建 temp 沙盒**；**不提供任何就地寫入旗標**。
- **不啟動伺服器、不綁任何埠、不發網路請求**、不 import `subprocess`/`requests`/`httpx`/`socket`/`uvicorn`/`fastapi`。
- 讀取：生產 `data/elevation/elevation_nodes.jsonl`（唯讀）＋ `data/elevation/elevation_lifecycle_events.jsonl`（唯讀）＋ `data/conversations/*.json` 的最近 N 筆真實 user 訊息（唯讀，N 預設 200，可調）。
- 探針詞元：重用 `src/memory/v1/loader.py:679` 的 `derive_query_tags`（**SSOT，不要重寫一份**）對每筆 user 訊息取詞元。
- 輸出：池成熟度 ＋ 命中率 ＋ **go/no-go 判準結果** ＋ 繁中報告寫到 `%TEMP%`。
- **不寫任何 repo 檔案**（報告寫 `%TEMP%`）。

### 3.5 契約附錄（`docs/ELEVATION-RECALL-CONTRACT.md`）

**只在檔尾追加 `## 8. 附錄 A（2026-10-19 實作票 ELEVATION-RECALL-HARDENING-1）`**，內容：
1. `quarantined` 旗標的語意（來源標記，非 lifecycle state；由 overlay 單點附加）。
2. `query_recall_by_tags` 的簽章與比對語意（union、去重、上限 12、正規化規則）。
3. 「隔離節點永不召回」這條**硬不變式**。
4. 實測依據（0/59 vs 12/59，59 筆真實 user 訊息，量測口徑寫清楚）。
5. §0–§7 **逐字保留不改**。

---

## 4. 驗收（完成的定義）

1. **隔離守衛生效**：任何 path 下都召回不到 `quarantined` 節點（`query_recall` 與 `query_recall_by_tags` 皆然）。
2. **自然老化節點仍可召回**：非隔離的 `dormant`／`superseded` 四層節點可被兩條路徑召回。
3. **契約路徑語意零改變**：`query_recall` 的簽章、比對語意、回傳形狀、排序、失敗靜默**逐項不變**（既有 `tests/test_elevation_recall.py` 10 支**必須零修改全綠**）。
4. **overlay additive 成立**：非隔離節點的 dict **不多任何 key**（既有 overlay 守衛零修改全綠）。
5. **量測工具可跑**：`recall_readiness_cli.py` 對生產圖譜產出池成熟度 ＋ 命中率 ＋ go/no-go，且**生產檔案位元組前後不變**。
6. **變異實驗全紅**（見 §5）。
7. **全庫回歸綠燈**（`.venv`）。

---

## 5. 變異實驗（雙向；沿用 `%TEMP%` 副本慣例，**repo 從不被修改**）

範本：`%TEMP%\mut010b.py` —— 把 `src/` + `tests/` + `pytest.ini` 複製到 `%TEMP%\<workdir>`，**只改副本**，用 repo 的 `.venv\Scripts\python.exe` 在副本 cwd 跑指定測試檔。**每條變異必須讓對應守衛轉紅**；轉綠即為盲腸守衛，必須回報並補強。

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| MUT-H1 | 拿掉共用管線的**隔離守門**（第 5 步） | 隔離節點召回守衛 |
| MUT-H2 | overlay 的 `quarantined` 旗標恆為 `False`（或恆不附加） | 同上（證明旗標真的被讀到，不是空轉） |
| MUT-H3 | `query_recall_by_tags` 的比對改回「整句子字串」 | 詞元級命中守衛 |
| MUT-H4 | 讓**契約路徑** `query_recall` 也回隔離節點 | 兩條路徑的隔離守衛（證明守門在共用管線，不在單一函式裡） |
| MUT-H5 | `go_no_go` 的門檻恆為 True（或恆 False） | go/no-go 判定守衛 |

**另需**（依 Bry 紀律：守衛必須證明自己有牙）：
- **受控延遲／受控污染重現**：用 `recall_readiness_cli.py` 對生產圖譜實測，報告**命中率的分子/分母/單位**（probe 數、命中 probe 數、命中的 unique 節點數），並說明若池中有 1 顆中文 dormant 節點，預期命中率會是多少（用 %TEMP% 合成節點量測，**不得寫生產**）。
- **既有測試零修改**：`git diff --stat -- tests/test_elevation_recall.py` 必須為空（契約路徑語意零改變的證據）。

---

## 6. 測試

- 必須用 `C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe -m pytest`（**唯一合法解譯器**）。
- 必須指名範圍。新增 `tests/test_elevation_recall_hardening.py` 至少涵蓋：
  1. 隔離節點在 `query_recall` 不可召回。
  2. 隔離節點在 `query_recall_by_tags` 不可召回。
  3. 非隔離 dormant 四層節點可召回（兩條路徑皆然）。
  4. `pattern` 仍不可召回（契約不變）。
  5. `state_filter` 含非法態仍回 `[]`（契約不變）。
  6. 詞元級 union 命中：合成中文節點 + 合成 tags。
  7. 詞元級正規化：全形/標點/空白/大小寫變體仍命中。
  8. tags 上限 12 生效（傳 20 個只取前 12）。
  9. `query_recall` 語意零改變（同一組輸入，契約路徑與既有測試一致）。
  10. overlay additive：非隔離節點的 dict 鍵集合不變。
  11. 污染守衛：overlay 旗標不洩漏到其他消費端（`project_emergent` 輸出逐位元相同）。
  12. `go_no_go` 判準的各個邊界（池為空／命中率達標／未達標）。
- 必須一起跑：`tests/test_elevation_recall.py`（既有 10 支，**零修改**）＋ `tests/inner_life/test_soul_bug_010_lifecycle_overlay.py`（既有 20 支，**零修改**）＋ `tests/infra/`（守衛全綠）。
- **不要跑全庫回歸**（約 11 分鐘，主大腦另外跑）。

---

## 7. 不做（Out of Scope）

- ❌ **不做 prompt 注入**。本票**完全不碰** `src/llm/proxy.py`：不新增 block、不改 `_build_messages_group`（`:706`）／`_build_messages_private`（`:1376`）、不改 EMERGENT（`:793-795`／`:1439-1441`）、不改 RAG 拼接（`:3732-3765`）。第二階段另開票。
- ❌ **不做工具化**（MCP／ToolRegistry／Actuator 全線在生產休眠中：0 註冊端點、`scheduler.py:549` 未注入即空轉、主聊天路徑 `proxy.py:1710-1711` 丟棄 `tools` kwargs）。第二階段再評。
- ❌ **不讓 recall 回 `pattern` 類節點**（契約 §2.1 明文）。
- ❌ **不改契約主文 §0–§7**（只加附錄 §8）。
- ❌ **不做語意／向量檢索**、不呼叫 LLM、不做 FTS5。
- ❌ **不改** `src/soul/elevation`、`src/llm/proxy.py`、`src/memory/**`、`src/agency/**`、`src/soul/decision.py`、`src/soul/actuator.py`。
- ❌ **不動** `data/**`（唯讀）、**不動** 外部 `soul-elevation` repo。
- ❌ **不得使用 `subprocess`**（`tests/infra/test_no_production_spawn_guard.py` 的 R5 規則：每個 spawn 呼叫點都要列入 `(file, line)` 白名單且大小鎖死 22 —— **不得擴大白名單**）。測試請在**同一直行程**內直接呼叫被測函式。
- ❌ **不得殺任何行程**、不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求。
- ❌ **不得放寬任何既有斷言**；既有測試檔零修改。

---

## 8. Frozen Contract 注意

- `docs/ELEVATION-RECALL-CONTRACT.md` §0–§7：**只加附錄，不改主文**。
- `src/inner_life/emergent_projection.py` 的 `PROJECTABLE_NODE_TYPES` / `NON_PROJECTED_LIFECYCLE_STATES`：**0 改動**。
- `src/inner_life/lifecycle_overlay.py` 的**四態語義**：0 改動（只加一個來源標記 key）。
- `src/llm/proxy.py`：**0 改動**（本票不碰 prompt 鏈路）。
- 4 handlers／SAGE 寫入邏輯／InnerLifeEvent：**0 改動**。
- 外部 `soul-elevation` repo：**0 改動**。

---

## 9. 第二階段接線判準（go/no-go，本票要實作這個判定）

`go_no_go()` 的門檻（**門檻值由本票實作者照抄，判定邏輯照下表**）：

| 條件 | 門檻 | 理由 |
|---|---|---|
| 可回憶池中**非隔離**的 dormant／superseded 四層節點數 | **≥ 5** | 少於此，回憶幾乎永遠空轉，注入只是噪音 |
| 池中 superseded 節點數 | **≥ 1** | 否則「我以前覺得 A，但後來發現 B」這條線索永遠不會出現 |
| 真實 probe 命中率 | **≥ 15%** | 實測詞元級 union 對中文內容是 20.34%（12/59）⇒ 15% 是可達但需真有意義的下限 |
| 命中的是**非隔離**節點 | 100% | 硬不變式，違規即 no-go |

四項**全過** ⇒ `go_no_go` 回 `{"verdict": "GO"}` ⇒ 第二階段可開工（prompt 注入決策）。
任一不過 ⇒ 回 `{"verdict": "NO-GO", "reasons": [...]}`。

---

## 10. 回報格式

1. 改動檔案清單（＋每檔行數變化）
2. §3 每個決策的實作位置（檔案:行號）與「與決策一致」的對照
3. 測試結果：跑了哪些檔案、幾 passed／幾 failed、耗時、**解釋器路徑**
4. 變異結果：MUT-H1～H5 各自轉紅／轉綠 ＋ 證據摘錄
5. 量測：池成熟度（分子/分母/單位）＋ 命中率（分子/分母/單位）＋ go/no-go 結果與 reason
6. 既有測試零修改證明：`git diff --stat -- tests/test_elevation_recall.py tests/inner_life/test_soul_bug_010_lifecycle_overlay.py` 必須為空
7. 生產檔位元組前後是否相同（`data/elevation/*.jsonl` 的 sha256）
8. 是否踩到紅線（應為「無」，但請自證）
9. **一個 commit**（在 `main` 上，訊息前綴 `feat(ELEVATION-RECALL-HARDENING-1):`），**不要 push**。commit message 不得含 BOM。

---

## 附錄 A — 主大腦派 explore 的唯讀考古報告（節錄關鍵事實）

（完整報告存於本工單建立者的對話紀錄；以下為實作者必須知道的結論）

- prompt 組裝鏈路 4 處定義：`src/llm/proxy.py:706`（群聊）／`:1376`（私聊）／`src/voice/build_system_prompt.py:451`（語音，非主路徑）／`clients/voice_companion/akane_voice_brain.py:1238`（客戶端）。
- EMERGENT block：群聊 `proxy.py:793-795`、私聊 `:1439-1441`。**禁入條款指「不寫進 EMERGENT 區塊內」；本票不碰 prompt，故此邊界問題延後到第二階段裁決。**
- 詞元來源 SSOT：`src/memory/v1/loader.py:679` `derive_query_tags(text) -> list`（jieba + `re.findall(r"\w+")`，濾 stopword／純數字／len≤1，lower 去重）。**本票直接重用它，不要重寫一份。**
- 既有檢索詞慣例：`src/memory/middleware.py:314-319`（draft 為空時 fallback 用 persona 靜態的 `memory_query_hint`）。
- `PROJECTABLE_NODE_TYPES` = {belief, value, trait, essence}（`emergent_projection.py:256` 註解「pattern 不投影」）。
- `query_recall` 的 `PROJECTABLE_NODE_TYPES` 過濾在 `elevation_recall.py:104`。
- `_reduce_events`（`lifecycle_overlay.py:97-117`）保留整筆 event dict（`:116`），**資訊在 reduce 層沒丟，只在 merge 層丟** ⇒ §3.1 一行即可。
- `apply_lifecycle_overlay`（`lifecycle_overlay.py:120-147`）現只覆寫 `lifecycle_state`（`:143`）與 `superseded_by`（`:144-145`）。
- 探針集來源：`data/conversations/*.json` 中 `role=="user"` 的訊息（限 owner agent）。
- 量測口徑：分子＝「至少命中 1 顆同 agent 可回憶節點的 probe 筆數」；分母＝probe 總數；單位＝筆訊息。
- 實測（59 筆）：整句子字串 0/59；jieba 詞元聯集 12/59 = 20.34%（命中 9/18 unique 節點）；SAGE top-6 2/59 = 3.39%；CJK 2-gram 12/59 = 20.34%（8/18 unique）。**對真正可回憶的 2 顆，全部策略皆 0/59。**
