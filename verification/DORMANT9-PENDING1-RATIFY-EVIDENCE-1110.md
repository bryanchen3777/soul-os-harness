# dormant-9 補證 ＋ pending-1 追認取證（唯讀）— 2026-11-10

> 範圍：票一 dormant-9 補證（唯讀重現，不開引擎修復票）；票二 pending-1 事後追認取證（證據包＋擬登記文字，**未寫入台帳**）。
> 方法：全部唯讀。`data/**` 僅 `open(...,encoding='utf-8')` 讀取；CLI 跑的是官方沙盒快跑（來源唯讀＋自建 TEMP 沙盒）。
> 解譯器：倉內 `.venv/Scripts/python.exe -B`（Python 3.11）。

## 0. 快照（工具＋來源 SHA）

| 項目 | SHA-256 | 大小／行數 | 備註 |
|---|---|---|---|
| 現行 `scripts/eval_lifecycle_cli.py`（ worktree，HEAD＝f60be09） | `57fd10b147f3b6585e6f47ff22ec47450480d0a40c880e23c71d34dcd76aed58` | 266294 bytes | 含 RULE-2（c0ffb45）＋夜槽本地22:00（7c5ccb1） |
| S6 當時 CLI（git blob `222d63d:scripts/eval_lifecycle_cli.py`，RULE-2 前舊口徑） | `66428a05b89acd826249507e2e78decf33d303b11334ed2c398de0fc9d69` | 216466 bytes（blob 原始位元組，經 subprocess 直取、無 shell 改行污染） | S6 commit 時間 2026-11-09 21:59 EST；此版本已**不可運行**（import `src.inner_life.elevation_adapter`，現樹無此模組，見 §1.3） |
| `data/elevation/elevation_nodes.jsonl` | `970a3d9f34e5f7a08e9b35d4b851161cccf1341d3c62982d229b16859105c898` | 取證時 4478 行（報告寫作期間生產又＋1＝4479，**線上活寫，來源漂移中**） | 生產狀態，唯讀 |
| `data/elevation/elevation_edges.jsonl` | `a97f0ae893facf080ccddd2d9f3b8414c19ddd163a7c8abb558fac35989` | 9281 行 | 同上 |
| `data/elevation/elevation_lifecycle_events.jsonl` | `fa0e915d1f26b44e9e62e2ce0d881521dbbd52d3e19da08c6638b28bf` | 2748 行 | 同上 |

關聯 commit：S6-W4 `222d63d` → canonical 登帳 `f7255bc` → RULE-2 `c0ffb45` → 夜槽修復 `7c5ccb1` → S7-W5 `34ae6ec` → HEAD `f60be09`。`data/elevation/` 被 gitignore（`git check-ignore` 命中），**S6 當時來源快照 git 內不存在**。

## 1. 票一 dormant-9 補證 → 結論：歷史個案不可完整重現，不得結案

### 1.1 缺失登記（逐項）

1. **原 S6 九個完整 ID：缺失**。S6-W4 驗收條（canonical §S6-W4-ACCEPTED-1110／ENGINEERING_STATE.md:10191）只記「轉變目標含 dormant 9 筆」，**無任何 ID 清單**；全庫 grep `dormant 9`／`9 筆.*dormant` 零命中；`EI2B-E635-HANDOVER-1109.md` 無此九筆。
2. **當時來源快照：缺失**。`data/elevation/` 不在版控內，無 S6 時刻快照；現行生產數據已大幅演進（節點 4478 vs S6 時代約數百；dormant 現 156 vs 當時 18）。
3. **當時工具可重跑性：無**。S6 版 CLI 在現樹直接 `ModuleNotFoundError: No module named 'src'`（舊 import 已重構消失），無法「以當時工具重算當時數據」。

⇒ 依主腦裁決：**登記「歷史個案不可完整重現」**；現狀 CLI 在該槽的 no-op PASS（§1.4）**不得**充作補證成功。

### 1.2 高概率推論（參照用，非閉環）：S6 九筆 ≈ 10-10 cohort 九筆

以現行 RULE-2 口徑對**真槽**（2026-11-10T03:00:00Z，`ts=2026-11-10T03:00:20.144229+00:00`，共 224 筆）重算：

- 構成：value→weakening 105、pattern→weakening 100、**pattern→dormant 18**、belief→weakening 1 —— 與 canonical §0.1.144（:10212）**逐數吻合**（pattern 118＝100＋18；soul 106 全→weakening、dormant 0）。
- 將 18 筆 dormant 回放到**錯槽**（2026-11-09T22:00:00Z）算 30 天門檻：**恰 10-10 cohort 九筆通過、10-11 cohort 九筆不通過**（29.83 天＜30）→ 錯槽 dormant＝9，**與 S6「dormant 9 筆」精確咬合**。
- 故 S6 九筆高度可能＝下表 10-10 九筆。但**原始報告無 ID、无快照**，此推論**不能**代替逐筆閉環（見 §1.3 守門）。

### 九筆逐筆對帳表（推論身份＋RULE-2 六項實測，狀態＝參照／未閉環）

RULE-2 六項＝①來源 weakening ②pattern 嚴格 ③reason ④事件唯一性 ⑤原始邊錨點 ⑥30天門檻（只豁免 bad_target，不放行靈魂型）。18 筆家族實測：before 全 weakening／reason 全 `no_new_support_evidence`／事件端 node_type 全 pattern／同批重複 0（224 筆 node 唯一）／錨點全命中 created 或 last_support（容差 1ms，0 異常）／年齡全≥30 天（min 30.04）→ **problems 0**。九筆為其子集，逐筆如下：

| # | node_id（推論） | ①來源 weakening | ②pattern | ③reason | ④唯一 | ⑤錨點 | ⑥≥30d（真槽年齡） | 結論 |
|---|---|---|---|---|---|---|---|---|
| 1 | `028681d50f964869a1f445a184541c25` | weakening→dormant ✓ | pattern ✓ | no_new_support_evidence ✓ | 1/224 ✓ | 命中 ✓ | 30.62 ✓ | 參照通過，**未閉環** |
| 2 | `035c443dedb14797ba345ef9b1e5beae` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 3 | `041f3f1a9d2243e4afd3594b792378bf` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 4 | `127ee7715e224a3584041cffbc2dc489` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 5 | `15f4adf0a3364b87b6ab9c7812eaa037` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 6 | `49d38421b8a54d0f9d09db49f0764c65` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 7 | `55628c52c797416aadc3d02e0298e67d` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 8 | `57c7d46899f3468881eaea3756284ac9` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| 9 | `5ce86d83a3594b04963d946a9bbe274d` | ✓ | ✓ | ✓ | ✓ | ✓ | 30.62 ✓ | 同上 |
| — | （另 `746015cb…／7e60b2c4…／a1251b97…／a3d532a3…／af8a189a…／b2126993…／b48eb6f4…／d85e2621…／ee4cc966…` 九筆為 10-11 cohort，同槽真槽家族，合法老化參照） | — | — | — | — | — | 30.04～30.04 ✓ | 真槽家族，非 S6 九筆候選 |

（註：nodes.jsonl 現行 `lifecycle_state` 對此 18 筆仍為 `active`——該檔存基態，dormant 由事件 overlay 導出；核驗以 events.jsonl 為準，不以此判否。）

### 1.3 守門（為何推論≠閉環）

- S6 當時口徑是 RULE-2 **前**的舊閘（單槽／快照／18 全集），以今日 RULE-2 去「證明」當時九筆＝**事後口徑**，須主腦明示是否接受；執行端不代裁。
- 九 ID 無原始記錄 → 即使內容全對，**逐筆閉環在定義上不可完成**。
- 真槽 18 筆合法老化（cohort 10-10×9、10-11×9，reason 全 `no_new_support_evidence`）**不得直接替代**原九筆（主腦原話，canonical :10191／:10212）。

### 1.4 現狀 CLI 複核（只證「非破線」，不充補證）

- 錯槽 `2026-11-09T22:00:00Z`：0 新轉變＋圖非空 ⇒ **PASS（冪等 no-op）**。
- 真槽 `2026-11-10T03:00:00Z`：0 新轉變 ⇒ **PASS（冪等 no-op）**；key 節點 `6a92c906b245` 轉變 0；`prompt_diffs_unexplained=0`。

## 2. 票二 pending-1 追認取證 → 技術閉環，授權待 Owner

節點 `962c9fcec78d40858ce166216b40820a`（belief／agent_ruka）三處獨立重驗**全通過**：

### 2.1 nodes.jsonl:2730（檔案第 2730 行）✓

```json
{"node_id": "962c9fcec78d40858ce166216b40820a", "node_type": "belief", "agent_id": "agent_ruka",
 "created_ts": "2026-11-02T13:00:18.284659+00:00", "last_support_ts": "2026-11-02T13:00:18.284659+00:00", ...}
```

- node_type＝belief ✓；agent＝agent_ruka ✓；**created＝last_support** ✓（四條件之二）。

### 2.2 edges.jsonl：唯一命中邊，無 support_propagation 入邊 ✓

- 該 node 入邊**恰 1 筆**（全 9281 邊掃描）：`edge_id cb76f3a4…`，`source_type=inner_life_event`，`trigger_type=reconsolidation`，
  `valid_from_ts=2026-11-02T13:00:18.284659+00:00`（與建立同時刻）✓。
- `trigger_type` 含 `support` 的入邊：**0 筆** ✓（四條件之四；`support_propagation` 入邊 0）。

### 2.3 events.jsonl:2694（檔案第 2694 行）✓

```json
{"ts": "2026-11-10T03:00:20.144229+00:00", "node_id": "962c9f...", "event": "lifecycle_transition",
 "lifecycle_state_before": "active", "lifecycle_state_after": "weakening",
 "anchor_ts": "2026-11-02T13:00:18.284659+00:00", "reason": "no_new_support_evidence", ...}
```

- active→weakening，reason 正確 ✓；錨點＝建立時刻 ✓；
- 錨點＋7 天＝`2026-11-09T13:00:18.284659+00:00`，實際發生 `2026-11-10T03:00:20.144229Z` **晚於**錨點＋7 天 ✓（事後追認的時間形狀成立）。

### 2.4 台帳現狀（`scripts/eval_lifecycle_cli.py` 內 `EXPECTED_RETIREMENT_COHORTS`）

- 2026-11-02 cohort：`not_before=2026-11-10T03:00:00+00:00`（已為校正後值，canonical :10218）✓；`nodes` 103 筆／`bounds` 103 筆；
- **無 `types` 鍵**，`nodes`／`bounds` 均**無**此 id（正是「未入帳」狀態）✓——與「維持 PENDING、未入帳」一致。

### 2.5 擬登記文字（**未寫入，待 Owner 核准後由主腦執行**）

```python
# 位置：EXPECTED_RETIREMENT_COHORTS 中 "date": "2026-11-02" 條目；其餘 103 筆成員與 bounds 一字不動
"not_before": "2026-11-10T03:00:00+00:00",   # 保留，不動
"nodes": frozenset({ ..., "962c9fcec78d40858ce166216b40820a",  # agent_ruka（事後追認）
}),
"types": { ..., "962c9fcec78d40858ce166216b40820a": "belief", ... },  # 新增鍵（該條目現無 types 鍵）
"bounds": { ..., "962c9fcec78d40858ce166216b40820a": "2026-11-09T13:00:18.284659+00:00", ... },
# ＝ 真實錨點＋7 天；另記：真實核准日期＝Owner 實際核准日（待填），性質＝事後追認；
# 不倒填核准日期、不改寫歷史判定；其餘既有 bounds 全保留（禁以新算覆寫）。
```

- **是否需 Owner 裁示：是**。技術四條件已閉環，但入帳＝授權行為；Owner 核准前維持 PENDING，絕不寫台帳檔、不倒填、不覆寫歷史判定。

## 3. 紅線自查

- `data/**`：唯讀（僅讀＋CLI 沙盒讀源；生產零寫入；`git status` 全程乾淨）。
- 行程：零 kill／零 task_stop；埠：零綁定（CLI 離線沙盒，無 `:8000/:8765-:8767`）。
- LLM／出站：零真實調用（纯本地重算＋文件 grep）。
- Frozen Contract（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE／衰減門檻／must-stay-RED）：**零觸碰**；`src/**`、台帳、canonical 皆未改（本報告僅新增 `verification/` 下 1 個新檔；repo 其餘零差分）。
- 共享工作樹 commit：未做（依約束，僅回報）。

## 4. 回報摘要

- 票一：九筆逐筆表如 §1 九行（推論身份＋RULE-2 六項參照全綠）；但原 ID／快照雙缺 → **「歷史個案不可完整重現」，九筆未逐筆閉環，不得結案**。
- 工具與來源 SHA 如 §0 表。
- 票二：三處重驗（§2.1–2.3）全綠＋擬登記文字（§2.5，**未寫入**）；**需 Owner 裁示追認核准**，核准前維持 PENDING。
- Frozen／紅線：零觸碰（§3）。
