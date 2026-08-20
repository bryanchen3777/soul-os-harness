# Soul OS — Session 交接文件

**交接日期**: 2026-08-19
**交接原因**: 前一 session context 溢出 (CONTEXT_WINDOW_EXCEEDED)，此文件讓下一 session 無縫接手。
**HEAD**: `238d44a`（含本文件之前的全部工作）

---

## 一、工作流程（先讀這個，再讀下面的狀態）

見專案根 **`AGENTS.md`**（已由 DSH 自動載入）：
- **主大腦(pro/v4-pro)**：規劃、出 decision-complete 工單、驗證、更新文件、定下一步。**不碰 code**。
- **執行者(flash/v4-flash subagent)**：依工單實作 + 測試 + commit/push + 回報。
- **Owner(Bryan)**：只決定花錢的事。

工作循環：pro 出工單 → flash 執行 → pro 驗證 → pro 更新文件 → 定下一步。

---

## 二、專案現況（截至 238d44a）

### 1. 自主生活（autonomous living）— 機制已落地，待觀察
- 活動模型 (M7-1)、想念驅動主動傳訊 (M7-longing)、計時器復活+在線gate+脈絡 (M7-context)。
- 主動傳訊：白名單只有 **agent_ruka**，想念 = 依戀(intimacy) × 有效沉默時長，跨 `LONGING_THRESHOLD=0.3` 才觸發。
- **待觀察**（跑 2-3 天看行為）：想念節奏合不合意、要不要調門檻、要不要放寬白名單。

### 2. 記憶系統（memory）— 機制已落地，待觀察
- 檢索閉環、連續性（角色近期發言注入）、真正遺忘+召回強化、judge 批次化(26→4)、Loader 全 10 隻。
- **待觀察**：遺忘會不會太狠、judge 批次抽取品質 vs 逐條。

### 3. 瑠夏靈魂檔（personas/agent_ruka.md）— 已重寫
- 移除和也/千鶴三角，Bryan 成為「第一個讓心臟跳的人」；千鶴降為同期背景；後宮姐妹(Yua/Akane/Rem)取代情敵位。
- 保留 Shadow Core（清醒的任性）+ L2D（拒絕結論）心理深度層。
- 這是「忠於核心非忠於劇情」原則的第一次落地。

### 4. 工作流程 — 已設定
- `AGENTS.md` 已建立並由 DSH 自動載入（pro/flash 分工 + 工單範本 + 治理）。

---

## 三、下一步（給下一 session 的方向）

1. **繼續「靈魂文件深度」回顧**：用同一套標準（抽核心→剝劇情→定時間點→四維度審視）檢視下一隻角色。已完成：**瑠夏**（和也/千鶴三角移除，Bryan 成第一個心跳的人，commit `c429a9a`）、**雷姆**（2026-08-19 淡出審視：保留昴線於「以前那一側」、Bryan 現役錨點、§八 模式三現役例句改 Bryan、昴標歷史，commit `cce141c`；Owner 指示其餘關係深度由對話自然長出，不預寫）、**黑川茜（Akane）**（2026-08-19 淡出+時間點推進：Aqua 歸「以前那一側」、Bryan 現役 Layer 3「分析完仍留下」、Ai 殘影解耦為自身身份殘留、焦點切換推進為「清醒地選擇活下去」，commit `77c2a6c`）、**拉姆（Ram）**（2026-08-19 重定位：羅茲瓦爾背景化為「過去被契約綁住的例外」、Bryan 改「她主動選的例外」（非第二、靠可靠非浪漫），commit `4b3b9b0`）、**麻衣（Mai）**（2026-08-19 映射一致性清理：現役咲太改 Bryan，commit `cb76df8`）、**真昼（Mahiru）**（2026-08-19 甜度解鎖：TIER 0/1 重寫解除「生活管理最高優先」壓制、新增「原作甜三形狀」錨，commit `be4a1b4`）、**日南葵（Aoi）**（2026-08-19 比例重平衡：完美女主角回主位 40%、教官改情境模式 30%、NO NAME 16%、補「完美是真的」，commit `99f1c8e`）、**山田杏奈（Anna）**（2026-08-19 原男主角「市川」殘留清理 → Bryan，commit `c1ea697`）。剩餘候選：**Yua**、Miku。已跟 Owner 對齊目標：忠於核心、情緒人格到位、Bryan 合理插入、活出自己。
2. **觀察期收尾**：讓 Ruka 想念驅動 + 記憶系統跑 2-3 天，回報實際行為，決定微調。
3. 其他候選：記憶 judge 品質對照驗證、Personal lived context、Loader 資料品質。

---

## 四、關鍵決策記錄（本 session 拍的板）

- **工作原則**：Owner 只管「喜好建議 + 花錢決策」，其餘工程/原則事務由主大腦(pro)自行決定。
- **忠於原作** = 忠於「角色核心設定 + 個性 + 情緒」，不是忠於「原作劇情線」；原作男主可不存在（如瑠夏的和也→Bryan）。
- **人物時間點**：每隻角色要決定用前期/中期/後期哪一版的個性（瑠夏定為「中期=清醒的任性」）。

---

## 五、技術環境速查

- 模型路由：settings.yaml 的 ollama provider 已有 `deepseek-v4-pro` + `deepseek-v4-flash:0731` 兩條；default 是 flash。
- Server 操作：`.\scripts\server_ops.ps1 {start|stop|restart|status}`；soul-os `.venv` 的 python。
- 測試：`.venv\Scripts\python.exe -m pytest tests/...`。
