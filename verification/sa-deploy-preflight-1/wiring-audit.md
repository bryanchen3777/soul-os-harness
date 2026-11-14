# Release / wiring / routing / approval-scope read-only audit
ticket: SA-DEPLOY-PREFLIGHT-1 | candidate: 6fdd38f | no production writes made

## 1. Release 解析路徑（實際程式位置，唯讀核對）
- 判定函式：`src/soul_access/release.py:30` `is_live_inbound_open(release_path)`。
  語義（同檔 :8, :33-46）：missing / unreadable / bad JSON / non-dict /
  status != "open" 一律視為 CLOSED（fail-closed hot-read）。
- 生產釘選路徑：`src/soul_access/wiring.py:74`
  `RELEASE_FILE = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/release/sa.json")`。
  註記（同檔 :72-73）：never in git；本票不建立真實檔案，Owner 核准前 gate 讀 closed。
- 缺檔狀態（實測）：`release/sa.json` 不存在（`Test-Path release/sa.json` = False；
  `release/` 目錄亦不存在）。=> 解析結果恆為 CLOSED，任何新入站一律 403。
- 例外：`bridge.build_live_handler(release_path=None)`（`src/soul_access/bridge.py:165-179`）
  表示「不諮詢 gate」，僅測試／沙盒傳 tmp 路徑時使用；生產兩條掛載皆傳實路徑（下節）。

## 2. 既有接線（唯讀核對，不啟動、不綁埠、不發請求）
- :8000 live 側：`scripts/run_server.py:794-813` 掛載 `wiring.mount_live_route`，
  同 launcher bus/llm/mw 實例，`generate=build_generate(..., dialog=None)`，
  `release_path=RELEASE_FILE`。dialog=None 正式掛載但呼叫時拒絕（500，不 fallback）。
  生產 dialog 保持 unwired，等 Owner 核准的後續票才接。
- :18765 thin 側：`scripts/serve_external_gateway.py:98-107`
  `build_thin_forwarder(release_path=args.soul_access_release or RELEASE_FILE)`；
  本行程不持 live 實例；release closed/missing/bad => 送出前 403，一位元組不發。
- Thin 唯一 :8000 字面：`src/soul_access/wiring.py:92`
  `_THIN_TARGET_BASE = "http://127.0.0.1:8000"`（檔內 :89-91 註記全倉唯一；
  `src/` 在 tests/** 釘選位址掃描範圍外，測試經此常數引用）。
- 內部端點單一來源：`INTERNAL_LIVE_PATH = api.FORWARD_ENDPOINT = "/internal/soul-access/live"`
 （`wiring.py:78,87` assert 鎖死；mount 時重驗，非此路徑拒絕註冊）。

## 3. 路由名單（唯讀核對）
- 白名單／映射：`src/external_gateway/routing.py:62-65`
  `SOUL_WHITELIST = frozenset(_SOUL_AGENT_IDS)`，`SOUL_AGENT_MAP`；
  未知 soul => 404 `route_not_found`（同檔 :110-122，先於任何 env 讀取）。
- 倉內唯一 registry 範例：`configs/external_registry.example.json`
  （owner `example-owner-do-not-deploy`，後端 `example-port`，單路由 example-soul；
  檔含 `do-not-deploy`，`serve_external_gateway.py:90-91` 啟動即拒收 exit 2）。
- 倉內唯一 presets 範例：`configs/models/example-do-not-deploy.json`
  （兩個 `example-*-do-not-deploy` preset，key_ref `EXAMPLE_NO_DEPLOY_KEY`）。
- 結論：倉內不存在生產 registry／生產 presets；T7 20 條（A 4 + B 16）僅為沙盒
  tmp release 檔＋假 generate 驗證的決策邏輯（`test_t7_rollout.py:346-512` 全經 tmp_path），
  不是生產接線。

## 4. 核准範圍與回滾（逐項程式位置）
- 採用裁決：T6 拒絕語義凍結為 403（`app.py:200`；`bridge.py:179`）。
  503 已判死，任何改回 503 即 T7 轉紅（runbook §1）。
- 回滾（runbook §4）：寫 `{"status": "closed"}` 到 release 檔（hot-read，即時生效，
  UNKNOWN 隔離列不復活，T7-2）；整批回滾 = `update()` 重裝上一批名單
  （early-B 變異證實 revert 即綠）。
- 核准狀態：APPROVAL-1（放行授權）維持 FROZEN——本票只寫計畫、不執行、
  不建立 release 檔、不接 dialog、不改 registry。部署仍 BLOCKED（runbook §5；
  接線方案與生產路徑核准下達前，任何真實放行一律禁止）。

## 5. 猜測／缺項聲明（未猜路徑，未建立 release）
- 本核對未發現除 `RELEASE_FILE` 外的第二生產 release 路徑。
- 阻斷（部署側，既有）：(a) 生產 release 檔不存在（預期，gate 讀 closed）；
  (b) 生產 dialog 未接線（預期，Owner 核准票另行）；(c) 生產 registry／presets
  未核准（倉內僅 do-not-deploy 範例）；(d) tests/interfaces 五紅缺口另票
  INTERFACE-GAPs-1 前置；(e) CANARY-WIRING-1 另票，不在本票範圍。
