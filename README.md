# Soul OS Harness
Bryan 的 AI 陪伴系統核心框架。
## 目前狀態
| 功能 | 狀態 |
|------|------|
| 多 Agent 架構（Yua / Ruka / Akane） | ✅ |
| WebSocket 即時對話 | ✅ |
| 網頁 UI（http://localhost:8000） | ✅ |
| MiniMax M2.7 真實 LLM | ✅ |
| Yua 完整人格（Soul OS 專用） | ✅ |
| 短期對話記憶（同 session） | ✅ |
| 跨 session 持久化記憶 | ❌ 待實作 |
## 快速開始
### 安裝
```bash
git clone https://github.com/bryanchen3777/soul-os-harness
cd soul-os-harness
pip install -e .
```
### 設定 .env
```env
LLM_PROVIDER=minimax
LLM_MODEL=MiniMax-M2.7
MINIMAX_API_KEY=your_key_here
```
### 啟動
```bash
python scripts/run_server.py
```
瀏覽器開 http://localhost:8000 即可與 Yua 對話。
## 架構
```
使用者輸入（WebSocket）
  → gateway.py → Event Bus
  → Agent（consciousness.py）
  → LLMProxy → MiniMax M2.7
  → AGENT_SPEAK → WebSocket 廣播
  → 網頁 UI 顯示
Agent 人格
人格設定放在 personas/ 目錄：
personas/agent_yua.md — Yua（正宮指揮官）
personas/agent_ruka.md — Ruka（待補）
personas/agent_akane.md — Akane（待補）
開發工具分工
工具	用途
Claude Code CLI	寫 code、跑測試、git
Cowork（Perplexity）	架構設計、診斷、任務單