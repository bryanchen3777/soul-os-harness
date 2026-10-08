# VC Audit v1 — A/B Harness

對應工單：[`docs/VC-AUDIT-V1-WORKTICKET.md`](../../docs/VC-AUDIT-V1-WORKTICKET.md)

REST / LIVE TTS 路徑的延遲與可靠性受控對照。**Phase 1 = 只建 harness，不執行。**

## 檔案

| 檔案 | 用途 |
|---|---|
| `corpus.json` | 固定刺激語料 C1–C9 與 `pilot_10` 順序（工單 §7） |
| `evidence.schema.json` | 每回合 evidence 的 JSON Schema（工單 §10） |
| `launch_instance.py` | 隔離 instance 啟動器（埠 / log / config 三重隔離） |
| `runner.py` | A/B runner：送測、判回合邊界、解析 log、產 JSONL |

## 三重隔離（不修改任何生產原始碼）

1. **埠** — `--port 8790`，並硬性拒絕 `{8000, 8765, 8766, 8767}`。
   加上 `--host 127.0.0.1`（loopback ⇒ `validate_host_and_token` 不要求 token）。
   `web_server.check_port_available()` 亦會在啟動時二次確認埠未被佔用。
2. **log** — `setup_logging()` 的路徑是模組層常數 `LOG_DIR / f"vc_{agent_id}.log"`，
   **沒有 CLI 覆寫入口**。若不處理，以 rem profile 啟動的 audit instance 會寫進
   **生產同一個** `clients/voice_companion/logs/vc_agent_rem.log`，造成歷史 85 回合
   證據被污染、且 A/B 兩臂紀錄交錯無法對應。
   `launch_instance.py` 在呼叫 `main()` **之前**改寫 `ws.LOG_DIR` 指向 run 目錄。
   這是從外部重綁定常數，**不是修改 logger 行為**——編碼、格式、level 一律不動。
3. **config** — 由 profile 衍生 `audit_config.json`，**僅**覆寫 `web.port` 與
   `web.https`（白名單 `ALLOWED_CONFIG_OVERRIDES`）。衍生檔與前後差異一併落盤，
   作為「除埠與 TLS 外完全相同」的證據。

## 使用

### Phase 1：離線驗證（0 API、0 行程）

```powershell
.venv\Scripts\python.exe -m scripts.vc_audit.runner --run-dir <tmp> --dry-run
```

### Phase 2：實跑（**需 Owner 批准花費**）

```powershell
# A 臂
.venv\Scripts\python.exe -m scripts.vc_audit.runner `
    --arm rest --port 8790 --run-dir audit_runs\rest_20261007

# B 臂（同一份 corpus、同一順序）
.venv\Scripts\python.exe -m scripts.vc_audit.runner `
    --arm live --port 8790 --run-dir audit_runs\live_20261007
```

**A 與 B 必須是兩次獨立 instance run，不是同一個 process 動態切 mode。**

## 硬邊界

- 不得綁 `8000 / 8765 / 8766 / 8767`
- 不得 kill 或 restart 任何既有行程。runner 只終止**自己 spawn 的子進程**
  （`subprocess.Popen` 握有的 handle），不枚舉、不依名稱殺行程
- 不得對生產服務發任何請求
- 不得寫入 `data/**`
- 不得修改 Soul / persona / Memory contract / SAGE / Temporal
- 本目錄下**沒有** `test_*.py`，且 `pytest.ini` 的 `testpaths = tests`，
  因此 pytest **不會**收集本 harness（避免重演 `scripts/` 曾有測試被收集後
  對真實 API 發請求的事故）

## 兩條必須知道的前提

1. **逐回合序列化是硬性的。** 送一個 stimulus，等 `state=IDLE` 再送下一個。
   「run 內第 N 個 `[LATENCY]`」才能無歧義對應「第 N 個 stimulus」。
   **改成並行送測會讓 log 解析失效。**
2. **缺資料填 `null`，絕不用 0。** `0` 與「沒量到」語意不同；混用會讓統計
   把缺口當成極快。

## 已知 instrumentation gap

- `llm_first_token_ms` **目前無法量測**：VC 未記錄 first-token 時戳。
  runner 一律填 `null`。Phase 1.5 補。
- `speech_start / vad_end / vad_silence_wait`：完全未記錄。
  歷史 `t_start` 起算點在 VAD 判定語音結束之後，故 `silence_threshold_sec=0.8`
  這段約 800ms 等待**從未進入任何 KPI**。Phase 1.5 補。
- Phase 1 走 text-fallback 路徑，**結構上繞過 VAD + ASR**（工單 §4）。
  這條 A/B 不能、也不打算回答前端問題。