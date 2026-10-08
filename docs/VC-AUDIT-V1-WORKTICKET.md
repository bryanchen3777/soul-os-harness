# VC Audit v1 — REST / LIVE 延遲與可靠性 A/B 審計工單

| 欄位 | 值 |
|---|---|
| Branch | `vc/audit-improve` |
| Base | `f1ed63d` |
| Owner | Bryan |
| 撰寫 | 主大腦 2026-10-07 |
| 狀態 | 待Owner 核准後執行 |

---

## 0. 本工單的三個已拍板決定

1. **A/B 一律在隔離 instance 埠 8790 執行。**生產 8765 / 8766 / 8767 完全凍結。
2. **三階段推進。**本輪終點 = 可重跑 harness + 一次 10+10 pilot；pilot 不得當正式驗收結論。
3. **既有 33,973 行 log 只當歷史背景**，報告措辭固定為
   `Historical production evidence, not experimental control.`

---

## 1. 目的

建立 Voice Companion（VC）第一輪可重現、可比較的 runtime 證據，回答一個工程問題：

> 在其餘條件不變時，`fish_audio.mode` 從 `rest` 改成 `live`，能不能把 `first_audio_ms` 從
> 當前 p50 ≈ 4.7 秒拉下來？

本輪**不以加更多 logging 為主要工作**。現有觀測已足夠（見 §3）。

---

## 2. 🔴 對上一版工單的三處修正（執行者必須知道）

### 2.1 `log encoding 有問題` — 這是誤判，**不是缺陷**

上一版工單 §11「Log Encoding 修正」**整節作廢，不得執行**。

實測：檔案是**完全正確的 UTF-8**，中文一字不差。

```
display_name=雷姆
msg type=text text=測試一下，聽得到嗎
```

亂碼來自量測端用 PowerShell `Get-Content`（走系統 ANSI code page cp950），
把好檔案讀壞了。**VC logger 沒有 bug，禁止修改。**

正確讀法（寫進報告的附錄）：

```powershell
$fs=[System.IO.File]::Open($p,'Open','Read','ReadWrite')   # 檔案被進程鎖住，必須 share read
$sr=New-Object System.IO.StreamReader($fs,[System.Text.Encoding]::UTF8)
```

**不受此修正影響的結論**：`mode` / `chunks` / `first_audio_ms` / `total_ms` 全是 ASCII
數值欄位，cp950 與 UTF-8 解讀結果完全相同。§3 的所有數字**全數成立**。

### 2.2 `first_audio_ms 強烈依賴 reply 長度` — 這是錯的，已推翻

原假設是「rest 模式整句合成完才出聲，所以回覆越長越慢」。實測反駁：

| reply_chars | n | min | p50 | max |
|---|---|---|---|---|
| 1–30 | 40 | 1506.7 | 3656.5 | 21001.9 |
| 31–60 | 40 | 1623.3 | 5153.3 | 29376.7 |
| 61+ | 5 | 2976.6 | 6002.1 | 12155.5 |

有單調趨勢，但**同一個長度區間內first_audio 仍相差 14–18 倍**。
回覆長度只解釋約 1.5 倍差異，區間內變異是 14–18 倍。

**⇒ REST 的 first_audio 主要由 Fish REST 端的回應時間／排隊決定，不是由我們送多少字決定。**
這反而**強化**了 LIVE 假設（LIVE 走常駐 WebSocket，應可繞開 per-call 開銷與排隊）。

後果：reply 長度**不是**主要干擾變因，A/B 比較比原先預期乾淨。

### 2.3 延遲是「厚尾」不是「整體慢」— KPI 必須照此設計

n=85 的完整分佈（sorted, ms）：

```
1558, 2257, 2623, ... 5980, 6036, 6080, 6138, 6227,
7136, 7524, 7681, 8122, 8727, 8757, 8988, 9375,
11247, 12217, ... 16345, 18718, 21098, 29449
```

| 區間 | 筆數 | 佔比 |
|---|---|---|
| < 2000 ms | 1 | 1% |
| 2000–6000 ms（密實核心） | 64 | 75% |
| 6000–10000 ms | 5 | 6% |
| 10000–20000 ms | 13 | 15% |
| > 20000 ms | 2 | 2% |

兩個獨立問題：

- **地板**：最好情況 1558 ms，**從未低於 1.5 秒**
- **厚尾**：**21/85 = 25% 超過 6 秒**，最大 29449 ms

**⇒ 只報 p50 會嚴重誤導。平均值完全不可用。**

---

## 3. 已確認事實（供執行者引用，不得重新推論）

| 事實 | 證據 |
|---|---|
| REST 是實際 production path | 啟動橫幅 `tts_mode=rest`；`[TTS] round mode=rest` 85/85 |
| 串流未真正發生 | `chunks=1` 84/85、`chunks=2` 1/85 |
| 首音延遲 | min 1558 / p50 4717.8 / p90 14853.6 / max 29376.7 ms；**0/88 低於 1000 ms** |
| reply 長度非主因 | §2.2 表格 |
| wake word 在 web 路徑不生效 | `_has_wake_word` 僅存在於 `akane_live.py:203`；`web_server.py` 未 import該模組，自有 `on_text`（`:598`）無閘門 |
| code-level cancellation concern 成立 | `asyncio.to_thread(_generate)` 使同步 LLM/TTS 不因 task.cancel() 終止 |
| **但** runtime orphan thread **未**證實 | 必須維持此區分，不得寫成已發生事故 |
| log mtime ≠ service liveness | log 檔被進程鎖住（handle 開著）但無新寫入 |

---

## 4. 🔴 新增限制：本 A/B 只走打字路徑，不含 VAD/ASR

歷史 85 回合中**只有 10 筆是打字刺激**，其餘走麥風 + ASR。

`web_server.py:598` `on_text` 是「打字 fallback：不走 VAD/ASR，直接進 LLM 串流 → TTS」。

本 A/B 用打字刺激是**刻意的正確選擇**——它移除 VAD/ASR 作為干擾變因。

**但必須在報告中聲明**：

- 本 A/B 量到的是 **text-fallback path** 的延遲
- **ASR latency 不存在於本次量測中**
- 使用者實際感知的端到端延遲 =本數值 + VAD/ASR，會比這裡高
- 不得宣稱本 A/B 證明了「使用者感知的總延遲」

---

## 5. 階段劃分與終點

```
Phase 1 — Harness            0 API 成本，0 進程風險
Phase 2 — Pilot A/B          REST 10 turns → LIVE 10 turns，真實 Fish Audio + Ollama
Phase 3 — 正式 A/B（不在本輪）  50 turns，僅在 Phase 2 證明管線有效後升級
```

**本輪 branch 終點**：

> 完成可重跑的 A/B harness，並完成一次 10+10 pilot；**不得把 pilot 當正式驗收結論**。

---

## 6. 環境邊界（硬約束）

```
生產8765 / 8766 / 8767     完全不碰，不重啟、不綁定、不送請求
         │
         ▼
┌──────────────────────────┐
│  VC A/B Audit Instance    │
│  Port     : 8790         │
│  Config   : profiles/rem.json（與生產同一份，保證 persona/prompt/model 一致）
│  Log      : 獨立路徑，不得寫進 logs/vc_agent_rem.log
│  啟動器   : .venv\Scripts\pythonw.exe -u -m clients.voice_companion.web_server
│             ├─ 必須 RedirectStandardOutput / Error（pythonw 無 console，失敗時零輸出）
│             └─ 禁止直接呼叫底層 uv pythonw.exe（會靜默死亡）
└──────────────────────────┘
     ├─ A: --tts rest
     └─ B: --tts live
```

**A/B 必須是兩次獨立 instance run，不是同一 process 內動態切 mode。**

### 禁止事項

1. **不得殺任何行程。**停止 8790 只能對 8790 自己的 process 做，且必須先確認該 PID 持有 8790 socket。
2. **不得綁 8765 / 8766 / 8767 / 8000。**
3. **不得對生產服務發任何請求。**
4. **不得寫入 `data/**`。**
5. 不得修改 Soul、persona canonical identity、Memory contract、SAGE、Temporal Cognition。
6. **不得修改 VC logger 的 encoding**（§2.1）。
7. 不得直接提高 `MAX_TOKENS` 並宣稱 empty-output 已修復。
8. 不得以單次成功取代 A/B evidence。

---

## 7. Corpus（全部改為雷姆語境）

上一版 S1–S5 寫「茜」，但被測對象是 `agent_rem`。persona 不匹配會改變回覆長度與風格。
（wake word 不會擋，見 §3。）

刺激長度對齊歷史實測 `input_chars` p50 = 9。

| ID | 刺激 | input_chars | 設計目的 |
|---|---|---|---|
| C1 | 雷姆，你在嗎？ | 7 | 極短 baseline |
| C2 | 雷姆在做什麼呢 | 7 | 短問句 |
| C3 | 測試一下，聽得到嗎 | 9 | 歷史最常見刺激（出現 2 次） |
| C4 | 好 那我摸摸頭 | 7 | 短陳述 + 動作 |
| C5 | 以前你失去記憶昏倒的時候的事情啊。 | 17 | 長陳述（歷史 input_chars 上限） |
| C6 | 你今天過得怎麼樣？ | 10 | 一般問答 |
| C7 | 你覺得一個人真正喜歡另一個人的時候，自己會知道嗎？ | 26 | **reasoning-sensitive**（對應 E1/E2 失敗族） |
| C8 | 我。這是聽說的呀。 | 9 | 歷史長延遲樣本（7463 ms） |
| C9 | subaru | 6 | 最短邊界（歷史 8677 ms） |

**Pilot 規模**：每組 10 turns = C1–C9 各 1 次 + C7 加跑 1 次。
**兩組必須用完全相同、完全相同順序的 corpus。**

---

## 8. Empty-output 分組（與 A/B 同時統計）

**不得把`finish_reason='length'` 當作 empty-output 的完整定義。**
已知三種形狀，其中第三種 `stop` + `content=0` 不能用「1024 太小」解釋。

| 組 | 定義 | 已知樣本 |
|---|---|---|
| E1 | `length` + reasoning_seen | reasoning_chunks=418, elapsed 4264 ms |
| E2 | `length` + reasoning_seen（長） | reasoning_chunks=1009, elapsed 17908 ms |
| E3 | **`stop` + reasoning_seen** | reasoning_chunks=212, elapsed 7706 ms |
| E4 | `stop` + 無 reasoning | 尚未觀察到 |
| E5 | 其他 | — |

E1+E2+E3 必須分開報，**禁止合併成單一「token 不夠」結論**。
每組需記錄：`finish_reason`、`reasoning_seen`、`reasoning_chunks`、`chunks`、`elapsed_ms`、
`model`、`input_chars`、`reply_chars`。

---

## 9. KPI（依§2.3 修訂）

**第一優先**

- `first_audio_ms` 的 **完整分佈**（不是只有 p50）
- **tail_fraction = P(first_audio > 6000 ms)** — 歷史基準 25%
- `min first_audio_ms` — 歷史基準 1558 ms（**從未低於 1.5 s**）

**第二優先**

- `total_turn_ms`
- `zero_audio_rate`
- `empty_llm_rate`（依 §8 分組）
- TTS error rate
- barge-in cancellation latency

**第三優先**

- p50 / p90 / max（**輔助**，不可單獨引用）

**禁止**：只報平均值。禁止用 p50 差異下結論。

### 判定準則（Pilot 階段）

Pilot 只回答「管線有效嗎／方向對嗎」，**不產正式驗收結論**。
但須記錄以下訊號，供 Phase 3 決定是否升級：

| 訊號 | 意義 |
|---|---|
| LIVE 有任何一筆 `first_audio < 1000 ms` | **串流真的發生了**（REST 歷史最小 1558 ms，此為排除式證明） |
| LIVE `tail_fraction` 明顯低於 REST | 厚尾是 REST 端排隊造成 |
| LIVE `min` 仍在 1.5 s 以上 | 还有per-call 地板，REST 不是唯一瓶頸 |
| LIVE zero_audio / TTS error 上升 | 放棄 LIVE 方向 |

Pilot 結論只允許三種：`PIPELINE_VALID / PIPELINE_INVALID / INCONCLUSIVE`。

---

## 10. Evidence Schema（runner 輸出）

每次 pilot 產出一份 JSONL，每turn 一行：

```json
{
  "arm": "rest|live",
  "stimulus_id": "C1",
  "input_text": "...",
  "input_chars": 7,
  "t_start": "ISO8601",
  "llm_first_token_ms": null,
  "llm_total_ms": null,
  "first_audio_ms": 0.0,
  "total_turn_ms": 0.0,
  "reply_chars": 0,
  "tts_chunks": 0,
  "tts_bytes": 0,
  "zero_audio": false,
  "empty_llm": false,
  "finish_reason": null,
  "reasoning_seen": null,
  "reasoning_chunks": null,
  "barge_in": false,
  "tts_error": null
}
```

缺資料一律寫 `null`，**禁止用 0 代替缺失值**。
（`0` 與「沒量到」必須可區分——否則統計會把缺口當成極快。）

---

## 11. Spending Gate

Phase 1（harness）**不得產生任何外部 API 呼叫**。

Phase 2（pilot，20 turns 總量）會產生真實 Fish Audio + Ollama 費用，
**須 Owner 明確批准後才可執行**。執行前須回報預估呼叫次數與成本量級。

---

## 12. Frozen Contract 注意

- `agency/` 四階段、`TriggerEnvelope`、`InnerLifeEvent`、4 handlers、SAGE 寫入邏輯：**不得改動**
- `docs/LIFE-THREAD-ENGINE-CONTRACT.md`：本工單不改
- Soul 主線未提交 WIP（`src/soul/life_thread_orchestrator.py` 等約 402 行）：
  **必須先在 `main` 上 commit 或 stash，再切本 branch**，不得讓它們跟進 VC branch

---

## 13. 回報格式

交付 `VC Audit v1 Result`，至少包含：

- **A.** REST baseline（pilot）
- **B.** LIVE experiment（pilot）
- **C.** latency 比較（含完整分佈 + tail_fraction，不只 p50）
- **D.** empty-output taxonomy（E1–E5 分列）
- **E.** barge-in 觀察（維持 code-level / runtime 的區分）
- **F.** 服務存活性四分：process alive / service responsive / log progressing / request succeeding
- **G.** text-fallback path 聲明（§4）
- **H.** 下一步修正候選

結論只允許：`CONFIRMED / INCONCLUSIVE / REJECTED`（正式階段）、
`PIPELINE_VALID / PIPELINE_INVALID / INCONCLUSIVE`（pilot 階段）。

**禁止使用**：`probably fixed` / `seems better` / `looks stable`。

---

## 14. 成功定義

1. 存在一條可重複執行的指令，能在 8790 依指定 `--tts` 啟動 instance 並跑完固定 corpus。
2. 產出符合 §10 schema 的 JSONL，缺資料用 `null`。
3. REST 10 + LIVE 10 兩份證據齊備，且報告聲明其為 pilot。
4. **0 觸碰** 8765 / 8766 / 8767 / 8000、0 kill、0 寫入 `data/**`、0 logger encoding 改動。
5. 報告中不存在「probably fixed / seems better / looks stable」。