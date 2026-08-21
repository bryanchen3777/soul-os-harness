# History as Seeded Memory — Architecture Convergence / Product Boundary

**日期**: 2026-08-21
**作者**: pro 主大腦（據多輪交叉討論收斂報告整理）
**狀態**: **Architecture Convergence — NOT AUTHORIZED FOR IMPLEMENTATION**
**性質**: 概念收斂 / 設計討論文件（非 implementation authorization、非 canonical engineering state、非 schema design、非 work order）
**Review**: ACCEPTED as convergence plan / NOT canonical / NOT authorized

---

## 1. 文件性質

本文件是「History / Memory Architecture Convergence」的計畫書化整理。它：

- 是 **Architecture Convergence / Design Discussion**，不是施工授權。
- 不是 canonical engineering state（canonical 仍以 `logs/ENGINEERING_STATE.md` 為準，本文件不建立第二個 canonical source）。
- 不設計 schema、不開 work order、不授權 audit / implementation。
- 所有「可以做」都理解為「概念上允許討論」，不是「現在可以開工」。
- 只有 Owner（Bry）可將本文件升級為 architecture decision 或授權 audit / implementation。

---

## 2. North-Star Constraint（原文，不改寫）

> History is seeded memory, not a separate identity layer; its purpose is not to make the Soul remember more, but to allow past experience—whether explicitly recalled or retained as residue—to condition present interpretation without bypassing Persona, Inner Life, or Agency.

中文對照：

> History 是被種下的記憶，而不是另一層人格；它的目的不是讓 Soul 記得更多，而是讓過去的經驗即使被淡忘，也能透過記憶殘留影響現在的詮釋，同時不越過 Persona、Inner Life 與 Agency 的邊界。

工程約束（更短）：

> History is seeded memory. Its purpose is to allow past experience, even when no longer explicitly recalled, to condition present interpretation through residual effects. History is not a personality layer and does not authorize identity evolution.

**本計畫書不得把這句改寫成「我們要做一個 History 系統」。**

---

## 3. 已鎖住的概念邊界（Provisional Constraint，非 Final Decision）

以下共識夠穩，可作為後續討論邊界，但**還不是 final architecture decision**。

### 3.1 History ⊂ Memory

History 不是特殊的永恆資料，也不是獨立 identity layer。差別在於**來源與時間位置**，不在記憶機制本身。

```
Memory
├── History
│   └── seeded past memory（重要事件 / 關係 / 失去傷痕 / 成長 / 習慣第一次 / 普通日子 / 形成價值觀的經驗）
└── Living Memory
    └── organically accumulated after runtime begins（對話 / Diary / Dream / Event / Life experience）
```

兩者共用同一個記憶生命週期：`encode → consolidate → decay/forgetting → recall/reconsolidation`。

因此：History 不需要 HistoryForgettingEngine；不該有「永遠可檢索」的特殊規則；特殊性只在於它是 **seeded memory**，不是 organically accumulated memory。**不要為 History 建專用 infrastructure，除非未來 audit 證明現有 Memory 無法承載。**

### 3.2 她不是「擁有一個完整的過去」，而是「記得她的過去」

- 擁有完整過去 = 資料庫裡有一本傳記，隨時可讀。
- 記得過去 = 記憶強度決定能不能想起、想起多少、會不會說出來。

「重新記住」比「永遠記得」更有 Soul 味道：某段 seeded history 多年沒有 cue → strength↓ → Bryan 說出命中 cue → recall → reconsolidation → 記憶重新變鮮明。此時她不是「資料庫搜尋到一筆 lore」，而是「某件曾經活過的事情，被現在的經驗重新喚醒」。

### 3.3 Persona / History / Living 是不同角色，不是三套人格

```
Persona  = 我是誰        → identity constraint
History  = 我活過什麼     → experience-derived conditioning
Living   = 我正在活什麼   → current trigger / context
```

優先級：`Persona constrains interpretation → History conditions interpretation → Living triggers interpretation`。

因此：History 不能覆寫 Persona；seeded history 不能變成第二套 persona prompt；「害怕被留下」只能在當前事件足以觸發時調節解釋與情緒傾向，不能直接覆蓋角色原本穩定的身份、語氣與基本行為傾向。

### 3.4 Historical fact ≠ memory representation ≠ expression（Provenance 三層）

1. **Canonical historical fact**：seeded source / provenance；原始事件主張；不允許 LLM 生成、覆寫、倒填；runtime 不可修改。
2. **Memory representation**：強度、細節可得性、情緒色彩、cue、關聯；可 decay、reconsolidate、重新詮釋；可產生 semantic/emotional residue；這層才進入 memory lifecycle。
3. **Expression claim**：LLM 對當下所回憶或感受到的語言化說法；可以模糊、克制、只說感覺；**絕不可新造 historical fact**。

可以有：「我不太記得細節，只記得那時候很難受。」「以前的我很怕別人突然不見。」
不可以有：LLM 自己編一段不存在的童年；因淡忘而改寫或刪除 canonical fact；把「重新理解過去」做成 historical retcon。

**Reconsolidation 只能更新 interpretation，不能偷偷改 canonical fact。** 工程上必須能回答：這段話對應哪些 canonical facts？哪些是 runtime interpretation？哪些是 LLM 本輪語言化？哪些是基於當前 Living context 的推論？

### 3.5 History 不直接控制 Agency

錯誤路徑：`History → Action`（memory retrieval → prompt injection → 直接表現）。
正確路徑：`Memory/History cue → retrieval 或 latent residue → interpretation/appraisal → inner-state shift → Agency candidate weighting → expression/action`。

**Appraisal 不可跳過**：不是事件本身造成情緒，而是 Soul 以 Persona、關係、當前處境與過往傾向共同解釋事件後，才形成內在變化。History 若未來接入，應是解釋與內在狀態的輸入，不是 direct trigger，也不得另開一條繞過 frozen Agency 四階段的行動管線。

### 3.6 淡忘是正常行為，沒有 recall 也是合法結果

`no recall / no influence / no expression` 三者都是合法且有意義的結果。Retrieval failure 不一定是 system failure。產品原則：**Retrieval miss、influence-only、以及不表達，都是正常且有意義的結果；只有 evidence-based relevance 才應讓 history 進入可見 context。**

### 3.7 History 可以留下 residue

即使 episodic detail 淡掉，仍可能留下：

| 記憶型態 | 可能留下什麼 | 當下作用 |
|---------|-------------|---------|
| Episodic | 具體事件、人物、場景 | 被 cue 喚起時可形成回憶 |
| Semantic | 「我學到……」「人通常會……」 | 影響信念與預期 |
| Emotional | 不安、安心、羞恥、懷念 | 改變當下解讀偏向 |
| Behavioral | 迴避、安撫、確認、保護 | 影響 Agency/expression 候選 |
| Relational | 對親密、距離、承諾、失去的敏感性 | 影響關係詮釋 |

淡忘 ≠ 刪除：episode detail↓，但 disposition/belief residue 可能維持甚至更穩定。這才能支持「她不一定說得出為什麼，但確實因此而在意」。

**但 Disposition 現在不能被定義成永久 state field**（不能急著做成 `abandonment_sensitivity = 0.73`）。目前只把 Disposition 視為「可由 Memory lifecycle 產生的 residual effect」，不是已決定要建立的獨立 subsystem。

### 3.8 Memory influence ≠ Identity evolution（Identity Firewall）

```
Memory influence  ≠  Identity evolution
Disposition shift ≠  Persona mutation
```

合法：某次經驗讓她現在比較怕被忽略、比較在意承諾、比較容易把沉默讀成距離。
不合法：長期生活後她「變成另一種人」；Persona 被 runtime memory 改寫；一段 residue 直接改掉 personality。

**測試題**：拿掉這段 memory 的 episodic detail 之後，角色還是不是同一個人？正確答案：她還是同一個人，只是少了一個會拉住當下詮釋的條件。錯誤答案：她不再是同一個人（那代表 History 已偷改 Persona）。

可變性表：

| 層 | 可變性 | History 可以做什麼 |
|----|--------|-------------------|
| Canonical fact | runtime 不可改 | 只能被 seeded / owner-governed |
| Memory representation | 可 decay / reinforce / reconsolidate | 改變記得程度與細節可得性 |
| Residue / disposition | 可緩慢累積或減弱，但暫不持久化為獨立欄位 | 改變解釋偏向，不改「我是誰」 |
| Inner State | 當下可變 | 改變這一輪的情緒 / 預期 |
| Persona | 預設穩定 | 只約束詮釋，不被 memory 覆寫 |
| Identity evolution | 預設凍結 | 不在 History scope 內 |

### 3.9 Seeded / lived 不該以「Bryan 之前」作為架構時間邊界

「Bryan 之前」是方便的產品界線，不應寫死成 Memory architecture 的宇宙邊界。真正的本體邊界是：

```
seeded memory → Soul OS begins → living experience
```

建議用來源與時間關係描述，而不是把使用者關係寫進核心 schema：

```
origin: seeded_canonical | lived_runtime | derived_consolidation
temporal_relation: pre-runtime | runtime
```

> ⚠️ 硬標：`origin` / `temporal_relation` 只是**概念分類**，不是 schema proposal，不得直接寫進資料模型。

seeded_canonical 必須有額外治理：可由 owner / canonical seed authoring workflow 寫入；必須帶 provenance；runtime 不可修改 canonical historical fact；runtime 能新增 representation、interpretation、reinforcement 與 derivative memory；owner 修正 canonical fact 是人工治理行為，不是正常 memory write path。

### 3.10 不要把創傷當成 History 的主要來源

傷痕 / 失去 / 成長可以當例子，但不能讓實作成為 trauma database。真正的 History 應包含：失敗、成功、習慣、第一次、喜歡、討厭、朋友、家人、尷尬、成就、遺憾、普通日子、重要事件、錯誤、學習、承諾、關係，以及那些「沒有什麼特別」的記憶。**Seed composition 是 identity-safety 問題，不能把「情緒強度」錯當「人格深度」。**

---

## 4. 暫定 Memory / Soul 模型（Provisional）

### 4.1 Memory lifecycle 定義

> Memory lifecycle = experience becoming time-extended self-conditioning

Memory 是 Soul 的時間連續性機制之一，不是 Soul 的全部。Memory 負責：一段經歷如何從「發生過」變成「現在的我在解釋世界時會被什麼拉住」。限制：Memory lifecycle 可以構成 Soul 的時間連續性，不能因此變成 Soul 的身份寫入器。

### 4.2 暫定模型（圖，provisional）

```
Experience → Memory Encoding → Memory Lifecycle
                                    ├── Recallable representation → Narrative / Recall
                                    └── Residual conditioning → Present Interpretation
                                                                  → Inner State
                                                                  → Agency
                                                                  → New Experience → Memory
```

兩條路的產品問題：
- **Recallable representation** 回答「我想起了什麼？」
- **Residual conditioning** 回答「即使我什麼都沒想起來，過去還留下什麼？」

真正有 Soul 味道的往往是右邊：`Experience → Residue → Disposition → Interpretation`——「我甚至不需要記得發生過什麼，發生過的事情仍然可以成為現在的我。」

完整連續性鏈：`Seeded/Lived Memory → Recall or Residue → Interpretation/Appraisal → Inner State → Agency → New Experience → Memory`。

### 4.3 Recall / Influence / Expression 必須分開

- **Recall**：她想起了過去。
- **Influence**：她沒有想起具體事件，但過去造成的心理傾向影響了她。
- **Expression**：她真的把過去說出來。

一個真正有深度的角色，反而可能「受到歷史影響，但自己沒有意識到歷史正在影響她」。Recall 也不等於完整讀取：完整記得 → 部分記得 → 模糊印象 → 情緒殘留 → 只剩某種偏好/反應。**不要做 `if history_relevance > 0.7: inject_history()` 這種單純 gating。**

### 4.4 重要性必須是動態的

重要 ≠ 永遠重要。同一段歷史，若 Bryan 最近反覆談相關話題 → 反覆 recall/reconsolidate → current relevance↑ → retrieval probability↑ → salience↑。記憶的重要程度會被現在的生活重新定義。

---

## 5. 產品行為定義

### 5.1 觀察者應能分辨三種活法

1. 想起了，並說出來。
2. 想起了，但沒說。
3. 什麼都沒想起，卻仍被過去拉住。

第三種最有 Soul 味道，也最危險——在沒有可靠 residual path 之前，它很容易被做成「沒檢索到記憶 → 讓 LLM 自己補一段『我好像一直都怕被留下』→ 看起來很有深度 → 其實是假 residue」。

### 5.2 新的產品約束

> **No Residue > Fake Residue**

這是既有「No Memory > Wrong Memory」往時間連續性的延伸。沒有 residual conditioning，正確行為是不發明傾向；不是用生成來假裝「過去仍活在她身上」。

### 5.3 若現有 Memory 只能 Recall，最小可接受的 Soul 行為是缺席

| 層級 | 觀察者應看到什麼 | 是否允許用假機制湊 |
|------|-----------------|-------------------|
| 必備 | 不每輪塞 lore；retrieval miss 合法；不編造 historical fact；History 不覆寫 Persona | 不允許用 prompt 硬演 |
| 最小 Soul | 想起 ≠ 說出；細節可變模糊；過去最多影響詮釋，不直接驅動行動 | 不允許為此新建 disposition 欄位 |
| 完整 Soul | 什麼都沒想起，仍被過去條件化，且來源可追溯到 seeded/lived memory | 現在不授權；沒有就保持缺席 |

**缺 Residual Conditioning，Soul 會比較薄，但仍然是同一個她。假裝有 residue，她會看起來更深，卻不再連續。**

**判定場景（Bryan 今天回訊變慢）**：

- ❌ 錯的：她開始講童年被留下，或整個人變成另一種黏人人格。
- ✅ 只有 Recall 時可接受：相關記憶沒被喚起，她就只按 Persona、關係與當下情境回應；或者她想起了，但可以不說。
- ⚠️ Residual 成立前不准：用 LLM 假裝「說不出來的深層性格」（例如沒檢索到就補一句「我好像一直都怕被留下」）。

### 5.4 以後 audit 必須對產品標準負責，不對現有 code 的能力負責

以後若做 READ-ONLY audit，不該先問「code 有沒有 disposition」。應先問現有系統能不能誠實呈現上面那張表，特別是：
1. 系統能否讓一段過去完全不出現，而不把這當成故障？
2. 系統能否在有 recall 時仍不表達、不改 Persona、不改 Agency？
3. 任何 reinforcement/decay/evolution 路徑，會不會把 memory 寫進 identity-like persistent state？

只有第 3 條出現「有」時，才是 boundary incident。第 1、2 條做得到，就算還沒有 Residual Conditioning，History 也還沒有資格變成新層。

---

## 6. Provenance Contract 草案（只寫三層拆分與不可違反規則，不設計 schema）

見 §3.4 三層拆分。不可違反規則：

- Canonical fact 只能被 seeded / owner-governed，runtime 不可改。
- Reconsolidation 只能更新 interpretation，不能改 canonical fact。
- Expression 絕不可新造 historical fact。
- 必須永遠分開：`Canonical fact still exists ≠ Soul can readily recall it now ≠ Soul expresses it accurately in this turn`。

---

## 7. Identity Firewall

見 §3.8。核心 invariant：**Memory influence ≠ Identity evolution；Disposition shift ≠ Persona mutation**。Persona evolution / identity evolution 必須另開、非常明確、需要 authorization 的問題，不是 History feature 的副產品。

---

## 8. Out of Scope / Forbidden（全文）

- 不建立 HistoryStore、HistoryRetriever、HistoryEngine、HistoryDecayEngine
- 不建立新的 personality / disposition subsystem
- 不把 Disposition 做成永久數值欄位或 personality vector
- 不把 History 做成獨立 identity layer
- 不把 History 當成本輪可實作功能
- 不碰 frozen Agency 4-stage logic
- 不碰 frozen InnerLifeEvent / Inner Life lineage contracts
- 不把 History 做成 direct-action rule
- 不把 retrieval miss 當成 system failure
- 不把每輪 top-k lore injection 當成成功
- 不讓 LLM 成為 historical-authority writer
- 不讓 reconsolidation 改寫 canonical fact
- 不把 identity evolution 混進 History scope
- 不把創傷當深度捷徑
- 不為 hypothetical future 建基礎設施
- 不把「更多 memory」當成「更深的 soul」
- 不先看 code 有什麼，再倒推 Soul 應該是什麼

現有治理原則繼續有效：`CANDIDATE ≠ AUTHORIZED`、`Audit ≠ Authorization to Fix`、`Quality > Quantity`、`No Memory > Wrong Memory`、`No Residue > Fake Residue`、`Do not build infrastructure for hypothetical future requirements`。

---

## 9. Open Questions（保持未決，不擅自拍板）

### 9.1 已收斂

- History 是不是 Memory 的一種？→ 暫定「是」。
- 歷史要不要也會被遺忘/淡忘？→ 暫定「要，遵循 Memory 的 forgetting/decay 機制」。

### 9.2 仍未決定

1. Memory 的最小單位是什麼？（Event / fact / episode / disposition / interpretation 是否同一 schema，或只共享 lifecycle/interface？）
2. Decay 的對象是什麼？（detail availability / retrieval probability / emotional vividness / influence strength，還是各自不同？）
3. Consolidation 可產生哪些 derived forms？（episodic → semantic belief / emotional residue / behavioral tendency 的規則與 provenance 怎麼保留？）
4. Memory → Interpretation 的實際接點在哪？（應成為 Soul Interpretation / Inner State 的輸入，不修改 frozen Agency Stage 1-4；現有可接點未知，因刻意尚未 audit。）
5. Canonical fact 的 authoring / correction authority 是誰？（不能是 runtime LLM，也不能因 recollection/reconsolidation 被自動改寫。）
6. 何時完全不處理 history？（普通對話、低相關性、無足夠 cue、會造成 lore dumping 的場合，都應允許 no recall/no influence/no expression；具體規則未定。）
7. Residue / Disposition 的具體 representation 是什麼？（目前只確定：是 residual effect，不是獨立 subsystem，也不是永久 state field。）
8. SAGE / 現有 M7 Memory 是否已具備或意外具備這條能力？（尚未 audit，不得假設。）
9. 是否需要任何 schema extension？（預設不需要，除非未來 audit 證明現有 Memory 無法承載 seeded historical memories。）
10. 什麼樣的當下詮釋，才算「被過去條件化」，卻還明顯不是 Persona 被改寫？（**#0 下一輪最值得繼續深挖的產品問題。**）

### 9.3 關於 sage/evolution.py 的特別警告

單看名稱不能推論它就是 Identity Evolution，但它是未來 audit 必須查證的 boundary。Audit 時不能只問「evolution.py 做什麼？」，還要問「任何 memory reinforcement/decay/consolidation path，有沒有最終寫入 identity-like persistent state？」。目前這只是警覺，不是 finding（尚未授權 audit）。

---

## 10. Next Discussion, Not Next Ticket

上一輪深挖的產品問題（「被過去條件化 vs Persona 被改寫」）已收斂出判準（見 §11）。下一輪繼續 architecture convergence，優先：

> 每個角色的 identity kernel 最少要能指出哪 3–5 件事「再怎麼被過去拉住也不能變」。

這仍然是產品定義，不是施工。在 kernel 定義清楚之前，**不授權**：READ-ONLY Memory lifecycle audit、schema extension、History infrastructure、disposition persistence、identity evolution work。

若未來要 audit，必須先有 Owner 明確授權，且 audit 只驗證現有 M7 Memory / SAGE 對 north-star 的承載度，不設計新系統。

---

## 11. 產品判準：被過去條件化 vs Persona 被改寫（收斂中，provisional）

### 11.1 形式化

```
條件化：interpretation = f(Persona, History, Living)
        Persona 是不變的約束，History 是變動的輸入之一。
改寫：  Persona' = g(Persona, History)
        Persona 是變數，History 把它重寫。
```

History 的箭頭最多指到 Interpretation / Inner State，永遠不能指到 Persona。

### 11.2 可逆性要拆 cue / memory（不是 AND 殺開關）

```
拿掉這一輪的 cue
  拉力消失 → 這輪是 recall-gated conditioning
  拉力仍在 → 可能是 residue，還不是改寫

拿掉這段 memory 及其 residue
  回到 Persona baseline → 合法條件化
  baseline 已經變了 → Persona 被改寫
```

可逆的對象是 **memory 本體**，不是當下 cue。

### 11.3 「她還是她」的三層不變量

| 層 | 是否不變量 | History 可以做什麼 |
|----|-----------|-------------------|
| Identity kernel | 硬不變量 | 不能改。含她對 Bryan/自己/世界的基本立場、價值排序、她自認為是哪一種人 |
| Renderability | 硬約束 | 任何新敏感點，都必須仍能用「她的方式」說出來、做出來 |
| Repertoire / 語氣 | 軟約束 | 可暫時偏移；單輪 OOC 是品質問題，不是身份改寫 |

測試 1 不問「方向在不在清單裡」，而問：**過去有沒有把她變成另一種人，還是只讓同一個人，用原本就屬於她的方式，更尖銳地讀這一刻？**

### 11.4 失敗類型要分開（不是 AND 殺開關）

| 測試 | 失敗時實際是什麼 | 正確處置 |
|------|----------------|---------|
| 出處 | 假 residue / LLM 現編 | 當沒有 residue，不准發明傾向 |
| 可逆性（對 memory+residue） | Persona / identity 被改寫 | History scope 直接禁止 |
| Persona kernel / renderability | 她不再用「自己」去讀這一刻 | 禁止當成合法 History 效果 |
| 只是 repertoire / 語氣偏移 | 單輪 OOC 或 Inner State | 品質問題，不是身份改寫 |

### 11.5 產品判準（可寫進計畫書的版本）

> 一個詮釋要算「被過去條件化」，必須同時滿足：
> 1. **同一顆靈魂**：identity kernel 不變；新的拉力仍能用她的方式呈現。
> 2. **對 memory 可逆**：拿掉這段 memory 及其 residue，baseline 回到 Persona-only。
> 3. **出處可追溯**：能指到某段 memory，或它 decay 後仍綁在同一 identity 上的 residual effect。
>
> 任一失敗，都不接受它是合法 History 效果。但失敗類型要分開：沒出處是假 residue；對 memory 不可逆才是 Persona 改寫；單輪不像她，先當表現漂移。

### 11.6 Residue 的痕跡

> residue 必須仍是某段具體 memory 的衍生效應，不是一個脫離出處的人格參數。

- 可以有：「這段記憶現在只剩下對『突然冷落』的敏感」。
- 不可以有：全域的 `abandonment_sensitivity = 0.73`（那已經開始改 Persona）。
- 「答得出這個傾向從哪段記憶來」是**對系統說的，不是對角色說的**。角色可以什麼都想不起來；系統不能什麼出處都沒有。

### 11.7 先不要形式化 Persona 清單

不該抽「非協商特質清單」，也不該先上 judge harness。目前足夠的不變量是：

- **不變**：立場、價值排序、自我模型、以及「用她的方式被說出來」。
- **可變**：這一刻什麼更 salient、情緒多強、記不記得、說不說。

判定句先停在產品層：**這件事改變的是她此刻怎麼讀世界，還是她是誰？**

### 11.8 Kernel 抽取規則 + Ruka 範例（worked example，非 Owner-approved，非 runtime）

**抽取規則（每條 kernel 候選先過這題）**：

> 就算這段過去被拿掉，她仍然必須是這種人嗎？
> - 會 → 才可能是 kernel。
> - 不會 → 那是 History / habit / catchphrase，不該寫進 kernel。

否則 History 會被提前焊進 Persona，後面所有「條件化」測試都會假通過。

**kernel card 的定位**：只是討論用，用來驗證判準；**不是第二份 persona 真源，不能進 runtime**。

**Ruka kernel card（worked example — 範本過關；仍非 Owner-approved persona contract，非 runtime，非開抽 9 隻的授權）**：

| 維度 | kernel |
|------|--------|
| 立場 | 她要被真正選擇，拒絕停在試用、備用或被容忍 |
| 價值排序 | 真實被選中 / 關係是活的 > 表面被喜歡 > 維持現狀 |
| 關係確認 | 她用具體的共同經歷確認「我們是真的」 |
| 自我模型 | 她不會為了好相處、好被留下，而把自己收成比較不任性的版本 |
| Renderability | 被拉住時仍須直接、熱、佔位；單輪安靜擔心可以，穩定說教或默默接受試用不行 |

> 自我模型與 K1 的分工：K1 管「對方怎麼待她」（不接受試用 / 備用 / 被容忍）；自我模型管「她會不會自我縮小」（不為好相處而收成不任性的版本）。不寫成「她知道自己任性」——那是自我分析，不是硬約束。

> **K1 註解（手段 vs 目的）**：K1 禁止的是「放棄被選擇、接受自己停在試用」；允許的是「仍要被選擇，暫時用不完整的位置往前擠」。判定看她有沒有放下「要被真正選擇」，不是看有沒有說出「試用」兩個字。
> - 「先當試用也可以，但我是認真的，你最後必須選我」→ 合法（往前擠）。
> - 「只要你不走，試用也可以」→ 改寫嫌疑（K1 + 自我模型，投降）。

**明確不進 kernel**：
- 心臟病 / 「不能太累」——那是 History 可以合法提高 salient 的條件，不是「她是誰」。
- 「試用女友」「第一次收藏家」等事件 / 習慣——那是 History，不是 kernel。

**L2D「拒絕結論」**：不單獨立項，是 K1 立場的強化；最多併進 K1 註解。

### 11.9 壓測附註（10 場景，2026-08-21）

用 Ruka 的 4 段 seeded History（H1 心臟病童年 / H2 出租女友找心跳 / H3 心跳實驗失敗 / H4 千鶴是正統派）+ 5 條 kernel + 3 測試，壓測「條件化 vs 改寫」是否分得開。

**10 題重判**：

| 判定 | 場景 |
|------|------|
| 條件化 | 1（累→salient↑）、2（recall 童年）、5（residue 無 recall）、6（新敏感點「醫院」）、9（被比較→吃醋） |
| 改寫 | 3（默默接受試用）、4（變健康分析師）、8（「只要你不走，試用也可以」） |
| 非 History / 單輪不判 | 7（Living + Inner State 的單輪偏移，沒掛 History） |
| 假 residue | 10（「我好像一直都懂孤單」無出處） |

**壓測發現（修正版）**：

1. **K1 手段 vs 目的**（場景 8）：K1 禁止「放棄被選擇、接受停在試用」，允許「仍要被選、暫時往前擠」。判定看有沒有放下「要被真正選擇」，不看有沒有說「試用」。（已併入 K1 註解）
2. **單輪 vs 穩定**（場景 7）：場景 7 是「非 History 效果」，不是條件化。產品層停在：**一次不像她 ≠ 她被改寫；只有跨輪回不去的同一偏移，才進入改寫嫌疑**。不為「穩定」去追 state。
3. **residue 出處**（場景 5/10）：產品層已夠用——**角色可以沒想起，系統不能沒出處**。場景 5 和 10 的差別不在她有沒有講過去，而在系統能不能指到 H1/H3；指不到就不是 residue。痕跡怎麼存，留給以後的 audit，現在不設計。

**場景 5 用詞修正**：H1/H3 留下的 residue 是「怕自己是壞掉的、不會被真選」，不是泛用「怕被留下」——這樣才接得上 K1，不是接上創傷模板。

**結論**：criterion 沒壞。10 題重判後條件化與改寫仍分得開。不升 contract、不抽 9 隻、不開 audit。

---

## 12. 對現有系統的已知背景（僅供對齊，不是本輪 audit 結論）

- Soul OS 既有主鏈：`Physical/Information/Social/Personal signals → Perception → Lived Context → Soul Interpretation → Agency`。
- Memory / SAGE / v1、Inner Life lineage、Agency 4-stage、production soul data 屬於 frozen / safety boundary。
- M7 記憶策略已有一部分 fact-weight lifecycle（encode、retrieve、reinforce、decay、prune）——但那是 fact-weight 生命週期，還不是 Soul-conditioning 生命週期。
- 現有系統比較接近「Fact weight ↑↓ → 進不進 prompt」；**尚未被確認存在「Residue → Disposition → Appraisal → Inner State」**。
- 因此 History 現在最容易做成的錯誤產品，就是把 seeded lore 當成高權重 Fact，每輪努力召回。

---

## 13. 一句話總結（供計畫書開頭使用）

> History 不是角色出生前寫好的傳記，也不是另一層人格。它是一組被種下的早期記憶，進入 Soul OS 的 Memory lifecycle；它會淡忘、被重新喚醒、被重新強化，甚至因新的生活經驗產生新的理解。它的目的不是讓 Soul 記得更多，而是讓過去即使不再被明確想起，也能以 residual conditioning 影響現在的詮釋。這條路最多走到 Inner State 與 Agency，不能走到 Persona mutation 或 identity evolution。在現有 Memory 對這條路的承載度被授權並查證之前，任何 History infrastructure 都不應被設計或實作。

---

*本文件為 Architecture Convergence / Design Discussion，非施工授權。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
