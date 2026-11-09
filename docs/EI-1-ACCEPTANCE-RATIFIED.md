# EI-1 驗收追認書（Ratified Acceptance）

- **追認日期**：2026-11-09
- **適用 commit**：`f19aac1`（`docs(canonical): EI-2B e635 ACCEPTED and merged to main`）
- **來源缺口（明載）**：無 EI-1 專屬原始工單；既有 commit 主旨（如 scope 登記、EI-2B 施工）**不是**驗收授權。
- **效力聲明**：
  1. 本文件為**追認規格**——補定六模組在 `f19aac1` 時點行為的驗收條款，不倒填日期、不冒充原始授權。
  2. **scope 成立 ≠ 功能驗收**：`tests/infra/test_ei1_scope_registration_guard.py` 只證明「六檔在 scope 內」，不證明任一條款成立。
  3. 本追認**不關閉 EI-1**：是否 ACCEPTED 由主大腦依 Hard Gate 四項全綠另行判定，worker 不得自行宣告。
- **凍結邊界**：六模組原碼（`src/interfaces/classification.py`、`config.py`、`context_policy.py`、`identity.py`、`schema.py`、`__init__.py`）、既有測試及守衛唯讀；本票不授權功能修正；`journal.py` 語義零改動。
- **證據位置**：`tests/interfaces/test_ei1_acceptance_ratified.py`（AC 見證＋雙向變異），執行產物見 `verification/EI-1-ACCEPTANCE-RATIFY-1/`。

## AC 矩陣（條款 → 正反案例 → 兩向變異 → 證據）

`M-LOOSEN`＝放寬而誤接納（非法輸入被錯誤放行）；`M-TIGHTEN`＝收緊而誤拒絕（合法輸入被錯誤拒絕）。
純性／不可變條款則分別注入副作用或可變性、破壞合法匯入或建構。每個變異只改隔離 TEMP 副本，
repo 檔以前後 SHA 對帳證明零寫入；危險呼叫（socket／subprocess／sqlite／os／sys／寫入式 open 等）
在匯入前以 AST 硬攔直接 raise，不靠「它應該會失敗」。

### C. classification（`src/interfaces/classification.py`）

| AC | 條款 | 正案例（應接納／應分類） | 反案例（應拒絕） | M-LOOSEN | M-TIGHTEN | 證據測試 |
|---|---|---|---|---|---|---|
| C1 | 嚴格欄位／角色白名單：頂層鍵 ⊆ model／messages／stream／temperature／max_tokens／n；訊息鍵 ⊆ role／content／name；role ∈ system／developer／user／assistant；未知鍵 → 400 unsupported_parameter | 最小合法請求一次通過 | 頂層多 `tools`、訊息多 `tool_calls`、role `tool` 均被拒 | `if k not in _TOP:` → `if False:`：未知頂層鍵被接納 | `_ROLES` 刪 `user`：合法單 user 請求被拒 | `test_c1_allowlist`／`test_c1_mut_loosen_top_key`（三態：同一 oracle `_oracle_rejects_unknown_top_key` 跑正常綠／mutant 紅）／`test_c1_mut_tighten_roles` |
| C2 | 末則須為非空白 user：末則 role 非 user → 400 missing_current_user；末則空白 → 400 | `[user:"hi"]` 通過；`[user:"", assistant:"ok", user:"go"]` 通過（空白非末則無妨） | 末則 assistant → missing_current_user；末則 `"   "` → 400 | `if out[-1].role != "user":` → `if False:`：assistant 結尾被接納 | 同行 → `== "user"`：合法 user 結尾反被拒 | `test_c2_last_user`／`test_c2_mut_loosen_last`／`test_c2_mut_tighten_last` |
| C3 | model 對應路由：model 必須等於 route.public_model_id，否則 400 model_mismatch；且須為合法 public_model_id | model-a 對 route model-a 通過 | other-model → model_mismatch；`a/b` → 400 | `if model != route.public_model_id:` → `if False:`：錯 model 被接納 | 同行 → `if True:`：對的 model 也被拒 | `test_c3_model_route`／`test_c3_mut_loosen_mismatch`／`test_c3_mut_tighten_mismatch` |
| C4 | 配額：訊息數 ≤128；單則正文 ≤32768 bytes；總正文 ≤131072 bytes；整體 JSON UTF-8 ≤262144 bytes；超限 → 413 | 邊界內請求通過 | 129 則／單則 32769 B／總量超限／整體超限各 → 413 | 四閾值改天文數字：四類超限全部被接納 | `> 128` → `> 0`：任何非空請求被拒（其餘三閾值同理見證） | `test_c4_quotas`／`test_c4_mut_loosen_all_thresholds`／`test_c4_mut_tighten_count` |
| C5 | 拒 surrogate：raw／content／name 含 surrogate 即 400 | 純 ASCII＋合法中文通過 | content `a\ud800b`、name surrogate、key surrogate 均 400 | 一併移除整組 surrogate 五層防禦（raw 掃描→`if False:`、content 檢查→`if False:`、name 檢查去 surrogate 項、`ensure_ascii=False`→`True`、`encode("utf-8")`→`encode("utf-8","surrogatepass")`）：髒輸入被接納 | `if contains_surrogate(content):` → `if True:`：乾淨輸入被拒 | `test_c5_surrogate`／`test_c5_mut_loosen_scan`／`test_c5_mut_tighten_content` |
| C6 | 僅非串流、有限參數：stream 僅缺省／False；True → 400 unsupported_feature，非 bool → 400；temperature 有限且 0–2（bool 不得冒充數字）；max_tokens 整數 1–2048（bool 不得冒充）；n 整數且 ==1（bool 不得冒充） | 溫度 1（int）正規化為 1.0 通過 | stream True／1／"false"；溫度 NaN／Inf／-0.5／2.5／True；max_tokens True／0／2049／1.5；n 2／True／0／1.0 均被拒 | stream `if st is True:` → `if False:`：串流被接納；溫度 bool 檢查刪除：`True` 被接納為 1.0 | 溫度 `> 2` → `> 1`：合法 1.5 被拒；max_tokens `> 2048` → `> 100`：合法 512 被拒；n `!= 1` → `!= 2`：合法 n=1 被拒 | `test_c6_params`／`test_c6_mut_loosen_stream_and_booltemp`／`test_c6_mut_tighten_ranges` |
| C7 | 分類：末則 CURRENT_USER；system／developer → EXTERNAL_INSTRUCTION；其餘 → EXTERNAL_HISTORY；classify 初始候選全否 | `[system, user, assistant, user]` 得 instruction／history／history／current_user，全 False | 空 messages → missing_current_user（classify 層二次守衛） | `if i == last:` → `if False:`：末則 user 掉進 EXTERNAL_HISTORY | `elif m.role in ("system", "developer"):` → `elif True:`：全部歷史被升格為 INSTRUCTION | `test_c7_sources`／`test_c7_mut_loosen_last_source`／`test_c7_mut_tighten_history_source` |

### G. config（`src/interfaces/config.py`）

| AC | 條款 | 正案例 | 反案例 | M-LOOSEN | M-TIGHTEN | 證據測試 |
|---|---|---|---|---|---|---|
| G1 | 必填及未知欄位嚴格驗證：頂層恰 4 鍵、route 恰 6 鍵、client 恰 7 鍵；缺鍵／多鍵 → 500 invalid_registry | 雙客戶標準 registry 通過 | 頂層多 `extra`、缺 `backends`、route 多 `endpoint`、client 多 `raw_token` 均 500 | 頂層未知鍵檢查 → `if False:`：`extra` 被接納 | `len(keys) != 4` → `!= 5`：合法 4 鍵 registry 被拒 | `test_g1_strict_fields`／`test_g1_mut_loosen_top_extra`／`test_g1_mut_tighten_count` |
| G2 | ID、引用、唯一性、digest 格式合法且正規化：ID 字元集＋長度；backend_ref ∈ backends；route (soul,backend) 唯一；client_id 唯一；digest 64 hex（大小寫皆收，存小寫）；digest 跨客戶唯一（大小寫歸一後比）；allowed_routes 格式＋指向既有 route＋客戶內唯一；backends 非空唯一；context_window_tokens 為正整數 | 大寫 digest 存成小寫通過 | 壞 digest／重複 route／重複 client_id／大小寫重複 digest／未知 backend_ref／壞 allowed_routes／重複 backends／空 routes／clients 均 500 | digest `if dig is None:` → `if False:`：壞 digest 被接納；backend `if b in seen_be:` → `if False:`：重複 backend 被接納 | digest `len(v) != 64` → `!= 65`：合法 digest 被拒 | `test_g2_ids_refs_uniqueness`／`test_g2_mut_loosen_digest_and_dupes`／`test_g2_mut_tighten_digest_len` |
| G3 | principal 須屬 owner：client principal_id 必須等於 owner_principal_id | 標準 registry 通過 | principal `owner-02` → 500 | `if pid != owner:` → `if False:`：外來 principal 被接納 | 同行 → `if True:`：合法 owner 被拒 | `test_g3_principal_owner`／`test_g3_mut_loosen_principal`／`test_g3_mut_tighten_principal` |
| G4 | utility 只能 none 且禁持久記憶；interactive 只能 owner_private | utility(none, mem False)／interactive(owner_private) 通過 | utility 配 owner_private、utility 開 mem、interactive 配 none 均 500 | utility `if scope != Scope.NONE or apm is not False:` → `if False:`：utility＋持久記憶被接納 | interactive `if scope != Scope.OWNER_PRIVATE:` → `if True:`：合法 interactive 被拒 | `test_g4_purpose_scope`／`test_g4_mut_loosen_utility`／`test_g4_mut_tighten_interactive` |
| G5 | 路由精確匹配，未知回 404：完全相等才命中；404 不洩漏既有 route／請求別名 | `soul-a/backend-a` 命中 | `soul-a/missing` → 404 route_not_found，無洩漏 | 404 行 → `return registry.routes[0]`：未知路由被接納並綁到別家 route | 匹配行 → `if False:`：合法路由也 404 | `test_g5_exact_route`／`test_g5_mut_loosen_404`／`test_g5_mut_tighten_404` |
| G6 | 錯誤設定回 500 invalid_registry（validate_registry 全路徑） | 見 G1–G4 反案例狀態碼斷言 | 非 Mapping、backends 非 list 等 → 500 invalid_registry | 同 G2-loosen（壞 digest 被接納即 500 守衛被旁路） | 同 G2-tighten | `test_g6_status_500`（併 G1–G4 斷言 status/code） |

### I. identity（`src/interfaces/identity.py`）

| AC | 條款 | 正案例 | 反案例 | M-LOOSEN | M-TIGHTEN | 證據測試 |
|---|---|---|---|---|---|---|
| I1 | 有效 token 才認證：正確 token 回 AuthenticatedClient 且 profile 對應 | TOKEN_A → client-a／owner-01 | —（反例見 I2） | `if hmac.compare_digest(...):` → `if True:`：任意 token 皆認證為首客戶 | 同行 → `if False:`：正確 token 也被拒 | `test_i1_authenticate_ok`／`test_i1_mut_loosen_always_true`／`test_i1_mut_tighten_always_false` |
| I2 | 空白、非字串、超 4096 字元或不匹配回 401 | — | `""`／`"   "`／`None`／`123`／bytes／4097 字元／錯 token 均 401 | `if not isinstance(bearer_token, str):` → `if False:`：`None` 繞過型別閘（爆 TypeError 而非 401，照樣是破線） | `> 4096` → `> 0`：合法 token 被拒 | `test_i2_reject_401`／`test_i2_mut_loosen_typecheck`／`test_i2_mut_tighten_length` |
| I3 | SHA-256 與恆時 digest 比較，不回顯憑證：以 `hmac.compare_digest` 比對 hex digest；401 訊息／repr／param 皆不含 token | 正確 token 通過；錯 token 的錯誤物件不含 token 字串 | — | `hmac.compare_digest(a, b)` → `(a == b)`：功能輸出不變但恆時性遺失——字串存在性 oracle 證明測試有牙（mutant 缺 `hmac.compare_digest` 即被同一存在性斷言殺死；本條款是源碼性質，功能差異無法見證，故不用 AST） | 破壞雜湊：`hashlib.sha256` 改不存在函式 → 合法認證亦被拒（401，合法建構被破壞） | `test_i3_no_echo_and_timing`／`test_i3_mut_loosen_plain_eq`（三態：同一存在性 oracle `_oracle_timing_guard` 跑正常綠／mutant 紅，功能輸出不變但恆時性遺失）／`test_i3_mut_tighten_break_hash` |
| I4 | 路由須精確授權，否則 403：allowed 完全字串相等才放行 | client-a 走 soul-a/backend-a 通過 | 走未授權 backend-b → 403 | `if e == want:` → `if True:`：未授權路由被放行 | 同行 → `if False:`：已授權路由也被拒 | `test_i4_authorize_exact`／`test_i4_mut_loosen_always_allow`／`test_i4_mut_tighten_never_allow` |

### P. context_policy（`src/interfaces/context_policy.py`）

| AC | 條款 | 正案例 | 反案例 | M-LOOSEN | M-TIGHTEN | 證據測試 |
|---|---|---|---|---|---|---|
| P1 | 認證先於路由及正文：壞 token 下無論路由／正文／header／request_id 多壞，一律 401 | 好 token＋好輸入一次通過 | 壞 token＋壞路由／壞正文／壞 header／壞 request_id 全部仍是 401（不是 404／400） | authenticate 延後到 resolve＋authorize 之後：壞 token＋未知路由變 404（認證順序破線） | branch 預設刪除（`seen.get(_B)`）：合法省略 branch 請求變 400 | `test_p1_auth_first`／`test_p1_mut_loosen_auth_deferred`／`test_p1_mut_tighten_branch_required` |
| P2 | header 大小寫正規化且拒重複／未知 x-soul-*；branch 預設 root | 大小寫混雜 header 通過；缺 branch 得 `root`；非 x-soul 雜項 header 被忽略 | 同名大小寫重複（turn／content-type）→ 400；未知 `X-Soul-*` → 400 unsupported_parameter；壞 ID 格式 → 400 | 重複檢查 → `if False:`：大小寫重複被接納；未知 x-soul 檢查 → `if False:`：`X-Soul-Bogus` 被接納 | 未知檢查 → `if low.startswith("x-soul-"):`：合法三識別 header 全被拒 | `test_p2_headers`／`test_p2_mut_loosen_dup_and_unknown`／`test_p2_mut_tighten_known_rejected` |
| P3 | utility 永不候選；interactive 僅 owner_private、三識別 header 齊備且明許持久時末則可候選，其餘 ephemeral | 全 header＋allow_mem → INTERACTIVE_CANDIDATE 且僅末則 True；utility 全 False；缺任一 header／allow_mem False → EPHEMERAL 全 False | —（以狀態區分，見正案例矩陣） | utility `UTILITY_NO_MEMORY` → `INTERACTIVE_CANDIDATE`：utility 被誤授候選；候選標記 `memory_candidate=True` → `False`：全 header interactive 丟失末則候選（與 tighten 對稱，見 P3-tighten） | `INTERACTIVE_CANDIDATE` → `EPHEMERAL`：合法持久會話被降級；同上标记行改 False 亦為收緊方向 | `test_p3_memory_states`／`test_p3_mut_loosen_utility_candidate`／`test_p3_mut_tighten_candidate_lost` |
| P4 | 生成限制為一次 provider、零工具輪：temperature／max_tokens 透傳，context_window 取自 route，provider_attempts == 1，tool_rounds == 0 | 全 header admit 的 limits 如上 | — | `provider_attempts=1, tool_rounds=0` → `=9, =9`：多輪工具呼叫被放行 | 同行 → `provider_attempts=0`：合法一次嘗試規格被破壞 | `test_p4_generation_limits`／`test_p4_mut_loosen_rounds`／`test_p4_mut_tighten_no_attempt` |
| P5 | namespace 固定六欄：(principal_id, agent_id, scope, conversation_id, branch_id, idempotency_key) | 雙客戶同輸入同 namespace | branch 不同 → namespace 不同 | 刪除 branch 欄：不同 branch 共用 namespace（去重鍵碰撞） | 追加 request_id 欄：同次邏輯操作因 request_id 不同而 namespace 分裂 | `test_p5_namespace`／`test_p5_mut_loosen_drop_branch`／`test_p5_mut_tighten_add_request_id` |
| P6 | fingerprint 忽略 request_id、對全部既定 payload 欄位敏感且序列化穩定：request_id 不同指紋相同；text／turn／route／generation／policy／candidate 任一不同指紋不同；`json.dumps(sort_keys=True, separators=(",",":"), allow_nan=False)`＋sha256 | 僅 request_id 不同 → 指紋相同；其餘欄位各有變異見證 | — | 刪除 `"turn_id"` payload 行：不同 turn 指紋相同（turn 敏感度遺失）；`sort_keys=True` → `False`：序列化穩定性遺失（同次建構功能輸出相同，以存在性 oracle 見證） | 追加 `"request_id"` payload 行：僅 request_id 不同指紋就變（request_id 隔離破線）；穩定性另以存在性 oracle `_oracle_serialization_stable` 釘選 `sort_keys=True`（mutant 改 `False` 即被同一斷言殺死；同次建構插入序固定故功能輸出相同，屬源碼性質，不用 AST） | `test_p6_fingerprint`／`test_p6_mut_loosen_drop_turn`／`test_p6_mut_tighten_add_request_id`／`test_p6_sensitivity_route_generation_policy_candidate`（末者以 `dataclasses.replace` 單改一欄獨證 route／generation／policy／candidate 四者敏感度）／`test_p6_mut_loosen_sort_keys_unstable`（存在性 oracle 見證序列化穩定性） |

### S. schema（`src/interfaces/schema.py`）

| AC | 條款 | 正案例 | 反案例 | M-LOOSEN（注入可變性／副作用） | M-TIGHTEN（破壞合法匯入或建構） | 證據測試 |
|---|---|---|---|---|---|---|
| S1 | 列定現有欄位、列舉及預設：Purpose／Scope／MemoryPolicy／Source 取值；RequestInput 預設 stream False／temp 0.5／max_tokens 512／n 1；GenerationLimits 預設 1／0；ContentSegment 預設 False；RequestContext 可選欄 | 預設建構值全對；列舉值全對 | — | 預設 `temperature = 0.5` → `2.5`：預設值掉出合法區間（建構仍成功，放寬） | `n: int = 1` 刪預設（末欄去預設，定義期即 TypeError）：合法匯入被破壞 | `test_s1_fields_enums_defaults`／`test_s1_mut_loosen_default_temp`／`test_s1_mut_tighten_drop_default` |
| S2 | 資料類別不可變：全部 dataclass frozen | 欄位賦值／`backends.append` 均爆錯 | — | `RouteBinding` 去 `frozen=True`：欄位可被改寫（不可變性遺失） | `RouteBinding.soul_alias` 改名：合法建構爆 TypeError | `test_s2_frozen`／`test_s2_mut_loosen_unfrozen`／`test_s2_mut_tighten_rename_field` |
| S3 | digest 不進 repr：CredentialBinding.digest 欄 `repr=False` | digest hex 不在 repr 內 | — | `repr=False` → `repr=True`：digest hex 出現在 repr（憑證雜湊外洩面） | 刪除 digest 欄：合法 `CredentialBinding(digest=…, profile=…)` 爆 TypeError | `test_s3_digest_repr`／`test_s3_mut_loosen_repr_true`／`test_s3_mut_tighten_drop_digest` |
| S4 | 錯誤保留 status／code／param／message | InterfaceError 四欄完整保留 | — | `self.param = param` → `= None`：param 遺失（除錯資訊被吞） | `__init__` 加必填 `extra`：既有四參建構爆 TypeError | `test_s4_error_shape`／`test_s4_mut_loosen_drop_param`／`test_s4_mut_tighten_extra_arg` |
| S5 | ID 及 public_model_id 依各自字元與長度規則驗證，不混用：ID 僅放行 `[A-Za-z0-9._-]` 且 1–128 且非 `.`／`..`；public_model_id 另禁空白字串、禁 `/`＼`0x00`／換行／控制字元、禁 surrogate，長度同為 1–128 | `"model a"`（空格）：ID 非法但 public_model_id 合法——證明兩套規則未混用 | ID：`""`／`a/b`／`a b`／129 字元；model：`""`／`"   "`／`a/b`／surrogate／控制字元 | ID 字元檢查 → `if False:`：`"a b"` 變合法 ID（字元集被放寬） | public_model 長度 `n > 128` → `n > 5`：合法 `model-a` 被拒 | `test_s5_id_rules`／`test_s5_mut_loosen_id_charset`／`test_s5_mut_tighten_model_len` |

### N. __init__（`src/interfaces/__init__.py`）

| AC | 條款 | 正案例 | 反案例 | M-LOOSEN（注入副作用／放寬） | M-TIGHTEN（破壞合法匯入） | 證據測試 |
|---|---|---|---|---|---|---|
| N1 | 維持空入口：僅 docstring，無匯入／賦值／匯出符號 | AST 無 Import／賦值；無非模組公開符號（子模組屬性係匯入機制正常綁定，不算洩漏） | — | 追加 `from .schema import InterfaceError`：入口洩漏符號（`hasattr` 見證） | 追加 `raise RuntimeError`：合法 `import src.interfaces` 被破壞 | `test_n1_empty_entry`／`test_n1_mut_loosen_leak_symbol`／`test_n1_mut_tighten_raise_on_import` |
| N2 | 六模組只依 stdlib 及彼此，匯入無 I／O、網路或服務副作用：絕對匯入僅 stdlib 或 `src.interfaces.*`；相對匯入僅同包六模組；模組層無呼叫（除 dataclass／field）；帶 IO／網路／env 封鎖的匯入一次通過 | 六檔 AST 全過；封鎖匯入成功（`test_n2_normal_import_under_blocked_io`：socket／subprocess／寫入式 open／os.environ 讀取全封下重匯入六模組＋全鏈 admit 成功） | — | classification 追加 `import src.agent.registry`：AST 守衛即報 bad import（有牙見證；FUP-4 起殺死由同一 oracle `_oracle_no_foreign_import` 承載，`any(...)` 僅為失敗原因診斷）；另以 socket／subprocess／寫入式 open 注入見證匯入前硬攔（blocked mutant） | classification 刪除 `from .schema import Message`：匯入仍成功，呼叫期爆 NameError（合法呼叫被破壞） | `test_n2_import_clean`／`test_n2_mut_loosen_foreign_import`／`test_n2_mut_tighten_drop_import`／`test_n2_mut_loosen_blocked_io_witness`／`test_n2_normal_import_under_blocked_io` |

## 不做聲明

- 不改 journal 語義、frozen contract、SETTLE、skip 基線；G1–G5 維持 must-stay-RED。
- 禁啟服務、綁埠、真實 API、殺任何行程；`data/**` 唯讀；測試／變異產物只寫自建 TEMP（交付兩檔＋`verification/` 除外）。
- 不得 commit／push／合入 main；不寫 canonical／Notion（由主大腦處理）。
