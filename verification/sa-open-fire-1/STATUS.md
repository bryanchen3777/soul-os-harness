# SA-OPEN-FIRE-1 — 驗收狀態：BLOCKED_PORT（閉門部分完成，實彈拒絕）

- 工單：SA-OPEN-FIRE-1（rem/primary 單發「你好。」；全批次最多 2 次計費呼叫）
- 基線：`58e811b`
- 結果：**BLOCKED_PORT** —— 只完成閉門部分，不冒稱實彈通過。

## 阻斷（兩條獨立，每條各自停火）

1. **BLOCKED_PORT**：`0.0.0.0:18765` 正被外來 PID `31704`
  （`python.exe`，父 `9988 python.exe`，祖父 `3388 svchost.exe`，
   Session 0，命令列皆空，2026-11-11 15:46 建立；快照見
   `preflight-blocked.json`）佔用。依票面 §5：拒啟、上報，不探測外來
   listener、不反覆啟動、不殺行程、不 `task_stop`、不換埠、不旁路直打 LLM。
2. **EVIDENCE_GAP（取證通道缺口）**：離線已證明現行 dialog 只回 `str`
  （`src/soul_access/wiring.py:358`，見 `check-report.json`），無 raw＋usage
   通道。票面 §4：缺 raw 即不合格停手，不以文字答案或估算替代；
   擴改線上鏈路須另票裁決，本票保持閉門。

## 計費與門禁終態（讀回值）

- 計費嘗試數：**0**（本輪 1 次額度未啟用，第 2 次額度保留未動；無 `sent.json`）。
- 實例門：**closed**（本票無 opener；探針 `--fire` 預設亦經雙重阻斷停火）。
- 共享門：**closed**（`release/sa.json` 讀回 `{"status": "closed"}`，本票零寫入）。
- 密鑰：一律只記變數名 `OLLAMA_API_KEY`（見 `request.template.json`；明文從未讀取）。

## 證據（本目錄）

| 檔案 | 內容 |
|---|---|
| `check-report.json` | 探針 `--check` 唯讀預檢：埠佔用＋str-only 證明＋門禁讀回，billed=0 |
| `preflight-blocked.json` | netstat＋CIM 鏈＋UTC 時刻 |
| `request.template.json` | 固定可重播請求＋sha256（`sent.json` 不存在＝從未送出） |
| `STATUS.md` | 本檔 |

## 測試（`.venv` Python 3.11.15）

- 新增 `tests/soul_access/test_t13_open_fire.py`：21 項（重送／併發／超額／
  缺 raw／逾時已耗／ finally 雙門／閉門零轉發零記憶／str-only 缺口／
  CLI 預設閉門／key_ref 脫密／雙向變異 6 項）。
- 守衛／回歸／CLI 沙盒對帳見工單回報（本票不動 `src/`、`configs/`、`data/**`）。
