# CRASH-F1-POSTMORTEM-1 — 主服務崩潰 dump 保全 + 唯讀事後分析

- 工單：**CRASH-F1-POSTMORTEM-1**
- 分析日期：2026-09-12
- 分析方式：**唯讀**解析 full memory dump（臨時標準庫解析器，無 cdb/windbg，無安裝任何套件）
- 隱私聲明：本報告**不含**任何記憶體內容、字串傾印、對話文本、token 或密鑰。
  僅含例外碼、位址、模組名稱／base／size／版本／時間戳、執行緒 ID、
  以及**返回位址數值**與其落點模組。

> **證據鏈與雜湊**詳見 `data/crash_dump_archive/20260912_2029_pid1132/EVIDENCE.md`。

---

## ⚠️ 更正（2026-09-13）

> **本區塊由工單 CRASH-F1-DOCFIX 於 2026-09-13 追加。原文（含全部原始數值與分析段落）一律保留於下方，未刪除、未改寫。**

**更正標的**：本報告把從 minidump 取出的 **15 個堆疊 slot 解讀為「崩潰執行緒（TID 13096）的存活堆疊 ＝ 應用程式崩潰時在做什麼」**。

**① 數值正確 —— 這一點不變。**
15 個 slot 的**每一個數值**（§5.2 的 `module+offset`、模組清單、ThreadId、SHA256、WER 三方比對等）都經逐值複驗，**全部正確**。本更正的對象**只是解讀**，不是數值。

**② 解讀更正：那 15 個 slot 是「faulthandler 傾印器自己的堆疊」，不是被傾印應用程式的堆疊。**
崩潰執行緒 **tid 13096 就是 faulthandler 的傾印執行緒本身** —— **崩潰者就是記錄者**。因此 §5.2 那條「CPython 執行期 → UCRT → Win32 → ntdll」的序列，描述的是**傾印器在傾印時正在跑什麼**，而非應用程式在崩潰瞬間做什麼。
§5.2 的 15 個存活框架中只有 **1 個**（`python311.dll+0x266605`）在本工單建檔時有具名函式可對照；更正後它的身分是 **`dump_frame+0xB1`（`traceback.c:1275`）＝ `PyCode_Addr2Line` 的回返位址**。

**③ 更正依據**：`docs/CRASH-F1-SYMBOLS-1.md`。
該報告以 `StackWalk64` 從 `ExceptionStream` context 完整走出崩潰執行緒的 9 框架具名回溯：

```
PyCode_Addr2Line+0xE            ← mov rcx,[rcx+0xA0]，RCX=0（NULL code object）
dump_frame+0xB1                 (traceback.c:1275)
dump_traceback+0x6D             (traceback.c:1340)
_Py_DumpTracebackThreads+0x160  (traceback.c:1447)
faulthandler_thread+0x50        (faulthandler.c:637)
```

配上 **`0xA0` 三重收斂**（崩潰指令讀 `+0xA0` ／ PDB 型別證實 `PyCodeObject._co_linearray @ +0xA0` ／ `ExceptionInformation[1] = 0xA0`），崩潰執行緒的身分在指令層閉合。該報告的 PDB 驗證為 **GUID/Age 雙側相符、`PdbUnmatched = 0`**（非部分相符）。

**④ 原「存活堆疊中 `.pyd` = 0」的觀察仍然有效 —— 但證明的事情要改寫。**
本報告 §0／§6.1 把「`.pyd` 框架數 = 0」當成**「與 `_overlapped.pyd`／asyncio C 擴展無關」的證據**。這個觀察**本身正確且仍然成立**（15 個 slot 內確實 0 個 `.pyd`），但**它證明的是**：

> **崩潰的傾印器是在 `python311.dll` 內部走訪時崩潰的，而不是應用程式的 C 擴充套件在崩潰。**

也就是說，**它不能用來為「應用程式端與 asyncio/IOCP 無關」背書** —— 因為這條堆疊根本不屬於應用程式端。原報告在這一層推論上**超出了證據**。

**⑤ 仍然有效、未受本更正影響的部分**：dump／WER 的雜湊與保全合規、`ExceptionAddress`／`faulting module`／`offset 0xB5BDA` 的讀取、11 條執行緒的 `ThreadList` 對照、context descriptor 位移的實測、以及 §6.2 `_overlapped.pyd` 殘留位址出現在 **tid 24572（非崩潰執行緒）** 的觀察。這些都是**數值／結構層**的事實，與解讀無關。

---

## 0. 一句話結論

**本次崩潰與 `_overlapped.pyd` / asyncio C 擴展無關。**
崩潰執行緒（tid 13096）的**存活堆疊 15 個返回位址候選中，`.pyd` 框架數為 0**，
全部落在 `python311.dll` 與 Windows 系統模組；
`_overlapped.pyd` 的 4 個殘留位址出現在**另一條執行緒（tid 24572）**，不在崩潰路徑上。

---

## 1. 事故元資料與證據鏈

| 項目 | 值 | 性質 |
|---|---|---|
| 崩潰時間 | 2026-09-12 20:29:36.877 | 已驗證事實 |
| 崩潰程序 | `python.exe` pid **1132** | 已驗證事實 |
| dump | `data\crash_dumps\python.exe.1132.dmp`，272,583,424 bytes | 已驗證事實 |
| dump SHA256 | `09A1C7EC0899001A59360CD4F42A573904C1A641578426DC2F02029791A4B2ED` | 已驗證事實 |
| WER Report.wer | 62,586 bytes，SHA256 `328B4D33…5AACF31C` | 已驗證事實 |
| 保全位置 | `data\crash_dump_archive\20260912_2029_pid1132\` | 已驗證事實 |
| 原始↔備份 SHA256 | **相同**（dump 與 WER 皆相同） | 已驗證事實 |
| watchdog | 20:33:03 `port_listen=False procs=0 -> restart` → 20:33:06 新 pid 14056 | 已驗證事實 |
| 20:28:03 前狀態 | `OK … listener=1132 procs=2` | 已驗證事實 |
| 20:38:04 後狀態 | `OK … listener=14056 procs=2`（至 21:53 無再重啟） | 已驗證事實 |

**證據保全合規**：只複製、未移動／未修改／未刪除原始 dump 與任何 WER 報告。
分析結束後再次複驗：原始 dump SHA256 與 bytes 仍完全相同。

`.`gitignore`` 涵蓋：`data/crash_dump_archive/` 已被 `.gitignore` L42 `data/` 忽略（`git check-ignore` 命中，
且該檔無 `!` 反向排除規則）→ **無需修改 `.gitignore`**。

---

## 2. 工具可用性

| 工具 | 結果 |
|---|---|
| `cdb.exe` | ❌ 不存在 |
| `windbg.exe` | ❌ 不存在 |
| `kd.exe` | ❌ 不存在 |
| `C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe` | ❌ 不存在 |
| `C:\Program Files\Windows Kits\10\Debuggers\x64\cdb.exe` | ❌ 不存在 |
| `C:\Program Files\WindowsApps\Microsoft.WinDbg*` | ❌ 不存在 |

→ 採 **步驟 3b：純 Python 標準庫自行解析 minidump**（`struct` + `os`）。
未執行任何 `pip install` / `winget install` / 工具下載。

解析器：`data/crash_dump_archive/20260912_2029_pid1132/parse_minidump.py`
（一次性工具，置於保全目錄，**未放入 `src/`**）。解析對象為**備份那份** dump。

### 2.1 解析器實作要點（供覆核）

| 結構 | dump 內實際情形 | 處置 |
|---|---|---|
| `MINIDUMP_HEADER` | 簽章 `MDMP`、version `41204.42899`、17 個 stream、dir_rva `0x20` | 標準解析 |
| Stream type 編碼 | 目錄內為**低 16 位**形式（`0x00000006`）而非 `0x60000006` | 兩種編碼皆接受 |
| `ExceptionStream` (6) | 168 bytes；`ThreadContext` 描述元位於 stream offset **`0xA0`**（非慣例 `0x58`） | 以實測佈局為準 |
| ThreadContext | size `1232` (=0x4D0)、rva `0x69E2`（**檔案位移**） | 直讀檔案位移，並保留 VA 後備路徑 |
| `ModuleListStream` (4) | 72 模組；模組名 RVA 為**檔案位移**（非 VA） | 以檔案位移讀取 |
| `Memory64ListStream` (9) | 768 個區塊，file_base `0x24B00`，共 272,433,152 bytes | 建 (VA→file offset) 對照表 |
| `SystemInfoStream` (7) | 存在 | 見 §5 |

**x64 `CONTEXT` 位移的推算與驗證方式**：官方 `CONTEXT` 佈局在 `P1Home..P6Home`(0x00–0x28) 之後為
`ContextFlags`(0x30)、`MxCsr`(0x34)、6 個 segment(0x38)、`EFlags`(0x44)、`Dr0..Dr7`(0x48–0x88)、
再依 `Rax,Rcx,Rsp,Rbp,Rsi,Rdi,R8..R15` 排列(0x88 起，每 8 bytes)，最後 `Rip`(0xF8)。
**自我驗證**：以最小平方法逐一比對相鄰欄位後確認 `Rip` 必須為 `0xF8`、`Rsp` 必須為 `0x98`、
`Rbp` 必須為 `0xA0`；此組合使 `Rip == ExceptionAddress (0x7FFE75245BDA)`，
且 `Rsp = 0x551DDAFD40` **恰好落在**該執行緒被傾印的堆疊區間
`[0x551DDAE818, 0x551DDB0000)` 內 —— 兩項獨立一致性檢查同時成立，故位移組合可信。

> 修正紀錄：初版曾將 `Rsp`/`Rbp` 誤置於 `0xA8`/`0xB0`（整體位移一格），
> 導致 `RSP` 讀出 `0x1F455DF2970`（實為 `Rsi`）。經上表交叉驗證後修正，本報告所載為修正後數值。

---

## 3. 例外元資料與 WER 交叉核對

### 3.1 dump 解析結果（已驗證事實）

| 欄位 | 值 |
|---|---|
| ExceptionCode | `0xC0000005` = `EXCEPTION_ACCESS_VIOLATION` |
| ExceptionFlags | `0x00000000` |
| ExceptionAddress | `0x00007FFE75245BDA` |
| ThreadId | **13096** (`0x3328`) |
| NumberParameters | 2 |
| ExceptionInformation[0] | `0x0` → **讀取**違規（非寫入、非執行） |
| ExceptionInformation[1] | `0x00000000000000A0` → 被存取的位址 |
| ContextFlags | `0x0010005F` |

### 3.2 faulting module + offset

| 項目 | 值 |
|---|---|
| faulting module | `python311.dll` |
| 模組 base | `0x00007FFE75190000` |
| 模組 size | `0x5E3000` |
| offset | **`0xB5BDA`** |
| 模組檔案版本 | **3.11.15** |
| 模組 TimeDateStamp | `0x6A1F5973` |

### 3.3 吻合判定（全部一致）

| 核對項 | WER `Sig[n]` | dump | 結果 |
|---|---|---|---|
| 例外狀況代碼 | `c0000005` (Sig[6]) | `0xC0000005` | ✅ 吻合 |
| 錯誤模組名稱 | `python311.dll` (Sig[3]) | `python311.dll` | ✅ 吻合 |
| 例外狀況位移 | `00000000000b5bda` (Sig[7]) | `0xB5BDA` | ✅ 吻合 |
| 錯誤模組時間戳 | `6a1f5973` (Sig[5]) | `0x6A1F5973` | ✅ 吻合 |
| 應用程式時間戳 | `6a1f598a` (Sig[2]) | `python.exe` 已載入 | ✅ 一致 |

**結論：WER 與 dump 完全互相印證，三方（WER / dump / 磁碟映像）一致。**

---

## 4. 崩潰執行緒 RIP/RSP 落點

（已驗證事實）

| 暫存器 | 值 | 落點 |
|---|---|---|
| **RIP** | `0x00007FFE75245BDA` | **`python311.dll + 0xB5BDA`** |
| **RSP** | `0x0000551DDAFD40` | 執行緒堆疊 `[0x551DDAE818 … 0x551DDB0000)` 內 |
| RBP | `0x000001F44E4D0928` | 非模組位址（heap 區） |
| RDI | `0x00007FFE75565F9C` | `python311.dll + 0x3D5F9C` |
| RSI | `0x000001F455DF2970` | 非模組位址 |
| EFLAGS | `0x00010206` | — |
| SegCs | `0x0033` | 64-bit user code segment |

**RIP 落在 `python311.dll` 內 —— 崩潰指令位於 CPython 直譯器本體，不在任何 C 擴展模組。**

---

## 5. 崩潰執行緒堆疊（位址級證據）

### 5.1 ⚠️ 無符號，僅能提供位址級證據

`python311.dll` 的官方 PDB 未取得（本機無 cdb/windbg，且**嚴禁安裝／下載工具與符號**）。
因此**無法**還原函式名稱，本報告**不編造任何函式名**。
以下僅提供「返回位址候選 → 模組+位移」的位址級序列。

### 5.2 存活堆疊框架（RSP 以上，時序可信）

> ⚠️ **本段「存活堆疊」的歸屬已於 2026-09-13 更正，見文首更正區塊。**
> 下表 15 個 slot 的**數值全部正確且已逐值複驗**；但它是 **faulthandler 傾印器自己的**堆疊
> （tid 13096 即傾印執行緒），**不是**應用程式崩潰時的堆疊。模組分佈結論（無 `.pyd`）不變，
> 但證明的是「傾印器在 `python311.dll` 內走訪時崩潰」。

由 `RSP = 0x551DDAFD40` 向上掃描，落在已載入模組 `[base, base+size)` 區間的 8-byte 值：

| # | 堆疊位置 | 返回位址 | 模組 + 位移 | 分類 |
|---|---|---|---|---|
| 1 | `0x551DDAFD48` | `0x7FFE7524BE9F` | `python311.dll+0xBBE9F` | CALL-indirect-ret |
| 2 | `0x551DDAFD90` | `0x7FFE75565F9C` | `python311.dll+0x3D5F9C` | addr-in-image（= RDI 值） |
| 3 | `0x551DDAFD98` | `0x7FFE753F6605` | `python311.dll+0x266605` | CALL-rel32-ret |
| 4 | `0x551DDAFDB0` | `0x7FFE75329BC0` | `python311.dll+0x199BC0` | addr-in-image |
| 5 | `0x551DDAFDD8` | `0x7FFE753F6725` | `python311.dll+0x266725` | CALL-rel32-ret |
| 6 | `0x551DDAFE00` | `0x7FFE7575C960` | `python311.dll+0x5CC960` | addr-in-image |
| 7 | `0x551DDAFE08` | `0x7FFE753F62C8` | `python311.dll+0x2662C8` | CALL-rel32-ret |
| 8 | `0x551DDAFE20` | `0x7FFE75329BC0` | `python311.dll+0x199BC0` | addr-in-image |
| 9 | `0x551DDAFE38` | `0x7FFE753A398C` | `python311.dll+0x21398C` | CALL-rel32-ret |
| 10 | `0x551DDAFE50` | `0x7FFE753A393C` | `python311.dll+0x21393C` | addr-in-image |
| 11 | `0x551DDAFE68` | `0x7FFE752ACCAE` | `python311.dll+0x11CCAE` | addr-in-image |
| 12 | `0x551DDAFE98` | `0x7FFEB0EDCD30` | `ucrtbase.dll+0x2CD30` | CALL-rel32-ret |
| 13 | `0x551DDAFEC8` | `0x7FFEB223CD87` | `kernel32.dll+0x2CD87` | CALL-rel32-ret |
| 14 | `0x551DDAFEF8` | `0x7FFEB3B4CAEC` | `ntdll.dll+0xACAEC` | CALL-rel32-ret |
| 15 | `0x551DDAFF28` | `0x7FFEB148ED90` | `KERNELBASE.dll+0x10ED90` | addr-in-image |

**框架模組分佈（存活堆疊）**：`python311.dll` × 11、`ucrtbase.dll` × 1、
`kernel32.dll` × 1、`ntdll.dll` × 1、`KERNELBASE.dll` × 1。
即：**CPython 執行期 → UCRT → Win32 → ntdll** 的典型 Python 呼叫路徑。

> 分類說明：`CALL-rel32-ret` = 候選位址前 5 bytes 為 `E8`（x86-64 `CALL rel32`），
> 強烈暗示為真實返回位址；`CALL-indirect-ret` = 前 6 bytes 為 `FF /2`；
> `addr-in-image` = 落在映像內但未見 CALL 前綴，可能為資料、尾呼叫或非標準序言。
> 此為**啟發式**分類，非精確回溯（精確回溯需 `.pdata` unwind 資訊或符號）。

### 5.3 RSP 以下區域（崩潰前殘留，**非**存活框架）

`RSP` **以下**的堆疊位址另掃得 `.pyd` 位址：

| 堆疊位置 | 模組 + 位移 | 相對 RSP |
|---|---|---|
| `0x551DDAF348` | `_pydantic_core.cp311-win_amd64.pyd+0x10D340` | RSP − 0x9F8 |
| `0x551DDAF398` | `_cffi_backend.cp311-win_amd64.pyd+0x1A942` | RSP − 0x9A8 |
| `0x551DDAF3F8` | `_cffi_backend.cp311-win_amd64.pyd+0x1A6D6` | RSP − 0x948 |
| `0x551DDAF400` | `_pydantic_core.cp311-win_amd64.pyd+0x0` | RSP − 0x940 |

**判讀**：這些值位於**堆疊指標之下**（該區塊 `0x551DDAD000`–`0x551DDB0000` 內、RSP 以下），
是**先前呼叫留下的未回收殘留資料**，**不構成崩潰當下的存活呼叫框架**。
存活序列（§5.2）中**沒有任何 `.pyd`**。此區分對 CRASH-F1 的核心問題至關重要。

---

## 6. 🔴 CRASH-F1 核心問題：`_overlapped.pyd` / asyncio C 擴展是否在崩潰堆疊中？

### 6.1 直接回答（已驗證事實）

| 檢查 | 結果 |
|---|---|
| **崩潰執行緒 tid 13096 存活堆疊中的 `.pyd` 框架數** | **0** |
| **崩潰執行緒中的 `_overlapped.pyd` / `_asyncio.pyd` / `select.pyd` / `_socket.pyd` 框架數** | **0** |
| `_overlapped.pyd` 是否已載入程序 | ✅ 已載入，base `0x7FFE80660000`, size `0xE000` |
| `_overlapped.pyd` 殘留位址出現於何處 | 另一條執行緒 **tid 24572**（RSP 以下之殘留，非存活框架） |

### 6.2 `_overlapped.pyd` 殘留位址（tid 24572）

| 堆疊位置 | 模組 + 位移 |
|---|---|
| `0x551D5EF148` | `_overlapped.pyd+0x2E80` |
| `0x551D5EF190` | `_overlapped.pyd+0x2DEC` |
| `0x551D5EF1A8` | `_overlapped.pyd+0x2E2B` |
| `0x551D5EF1C0` | `_overlapped.pyd+0x78C0` |

tid 24572 的 RIP 為 `ntdll.dll+0x160EE4`（典型等待／系統呼叫狀態）。
即：**有一條執行緒曾走過 `_overlapped.pyd`**（合理，該程式使用 asyncio/IOCP），
但**崩潰發生在那條執行緒之外**。

### 6.3 全執行緒交叉檢查（11 條執行緒）

| tid | RIP 落點 | 候選數 | .pyd 框架 | IOCP/asyncio C 擴展框架 |
|---|---|---|---|---|
| 24572 | `ntdll.dll+0x160EE4` | 51 | 4（`_overlapped.pyd`） | **4** |
| 7596 | `ntdll.dll+0x160E44` | 53 | 3（`_queue.pyd`） | 0 |
| 1136 | `ntdll.dll+0x160E44` | 53 | 3（`_queue.pyd`） | 0 |
| 4072 | `ntdll.dll+0x160E44` | 50 | 3（`_queue.pyd`） | 0 |
| 2056 | `ntdll.dll+0x164A74` | 3 | 0 | 0 |
| 2272 | `ntdll.dll+0x160E44` | 50 | 3（`_queue.pyd`） | 0 |
| 8104 | `ntdll.dll+0x160E44` | 41 | 0 | 0 |
| 6260 | `ntdll.dll+0x160E44` | 58 | 3（`_queue.pyd`） | 0 |
| 23104 | `ntdll.dll+0x160E44` | 50 | 3（`_queue.pyd`） | 0 |
| 16720 | `ntdll.dll+0x160EE4` | 6 | 0（`mswsock.dll` ×4） | 0 |
| **13096（崩潰）** | **`python311.dll+0xB5BDA`** | 15（存活） | **0** | **0** |

- **唯一**出現 `_overlapped.pyd` 的執行緒是 **24572**（非崩潰執行緒）
- `_queue.pyd` ×3 出現在多條執行緒，屬**模組層級**共同殘留，非個別崩潰特徵
- 崩潰執行緒 13096 的存活堆疊**完全沒有任何 C 擴展**

### 6.4 結論（區分事實與推論）

**已驗證事實**：
1. 崩潰指令 `python311.dll+0xB5BDA` 位於 **CPython 直譯器本體**，不在任何 `.pyd`。
2. 崩潰執行緒存活堆疊 15 個框架中 **`.pyd` = 0、IOCP/asyncio C 擴展 = 0**。
3. `_overlapped.pyd` 只在**另一條**執行緒（24572）留下殘留位址。
4. 例外為**讀取**存取違規，存取位址 `0xA0`（極低位址 → 近似 null-pointer dereference 特徵）。
5. 崩潰執行緒存活堆疊形如 CPython → UCRT → Win32 → ntdll，**未見任何 asyncio/IOCP 事件迴圈路徑**。

**推論（待驗，非結論）**：
- 目前的位址級證據**不支持**「崩潰導因於 `_overlapped.pyd` / IOCP 事件迴圈 C 擴展」此一假設。
  若 CRASH-F1 原假設為此，本次事件**不構成支持證據**。
- 存取位址 `0xA0` + RIP 在 `python311.dll` 內，與「讀取一個由低品質／已釋放指標算出的欄位」型缺陷一致
  （例如物件指標為 null 或近 null，取其偏移 `0xA0` 處成員）。**尚未能定位是哪一個 C 層呼叫**，需符號與精確回溯。
- 存活堆疊未出現 `_overlapped.pyd` **不能**反向證明「事件迴圈絕對無涉」：
  若崩潰發生在**回呼（callback）自事件迴圈被喚起之後的純 Python/C 執行段**，
  事件迴圈框架可能已在更下方或被 unwinding 移除。**此為待驗的替代假設。**
- 由於缺乏符號，**無法**判定 `python311.dll+0xB5BDA` 對應哪個 Python 內部函式。

---

## 7. 載入模組重點清單（含版本）

（已驗證事實；版本取自磁碟映像 `VersionInfo`，因 dump 內 `VS_FIXEDFILEINFO` 未帶版本資源）

### 7.1 CRASH-F1 關注模組

| 模組 | 是否載入 | base | size | 版本 | TimeDateStamp |
|---|---|---|---|---|---|
| `python311.dll` | ✅ | `0x00007FFE75190000` | `0x5E3000` | **3.11.15** | `0x6A1F5973` |
| `_overlapped.pyd` | ✅ | `0x00007FFE80660000` | `0xE000` | **3.11.15** | `0x6A1F597D` |
| `_asyncio.pyd` | ✅ | `0x00007FFE80670000` | `0x11000` | **3.11.15** | `0x6A1F597B` |
| `select.pyd` | ✅ | `0x00007FFE829C0000` | `0x9000` | **3.11.15** | `0x6A1F597E` |
| `_socket.pyd` | ✅ | `0x00007FFE829F0000` | `0x15000` | **3.11.15** | `0x6A1F5981` |
| `python3.dll` | ✅ | `0x000001F44E4E0000` | `0xF000` | 3.11.15 | `0x6A1F597B` |
| `_queue.pyd` | ✅ | `0x00007FFE80650000` | `0x9000` | 3.11.15 | — |

> 所有 CPython 3.11.15 元件的 TimeDateStamp 落在 `0x6A1F597B`–`0x6A1F5981` 區間，
> 與 `python.exe` 的 `0x6A1F598A` 同批 —— **映像為同一建置，無版本混用**。

### 7.2 崩潰堆疊涉及的系統模組

| 模組 | 版本 | 是否出現在存活堆疊 |
|---|---|---|
| `ucrtbase.dll` | 10.0.26100.9444 | ✅ |
| `kernel32.dll` | 10.0.26100.9278 | ✅ |
| `ntdll.dll` | 10.0.26100.9278 | ✅ |
| `KERNELBASE.dll` | 10.0.26100.9278 | ✅ |

### 7.3 其他已載入 Python C 擴展（共 72 個模組，節錄）

`_pydantic_core.cp311-win_amd64.pyd`、`_rust.pyd`、`_rust_notify.cp311-win_amd64.pyd`、
`_yaml.cp311-win_amd64.pyd`、`_cffi_backend.cp311-win_amd64.pyd`、`_decimal.pyd`、
`_lzma.pyd`、`_bz2.pyd`、`_ssl.pyd`、`_hashlib.pyd`、`_sqlite3.pyd`、`_ctypes.pyd`、
`_multiprocessing.pyd`、`_elementtree.pyd`、`_zoneinfo.pyd`、`_uuid.pyd`、`_queue.pyd`、
`pyexpat.pyd`、`unicodedata.pyd`、`speedups.cp311-win_amd64.pyd`、
`url_parser.cp311-win_amd64.pyd`、`parser.cp311-win_amd64.pyd`、`cd.cp311-win_amd64.pyd`、
`ada92cb5d92a588d1b93__mypyc.cp311-win_amd64.pyd`

---

## 8. 系統資訊

（已驗證事實）

| 項目 | 值 |
|---|---|
| ProcessorArchitecture | 9 (**AMD64**) |
| ProcessorLevel / Revision | 6 / 47618 |
| NumberOfProcessors | 20 |
| MajorVersion | 10 |
| MinorVersion | 0 |
| BuildNumber | **26200** |
| ContextFlags（崩潰執行緒） | `0x0010005F`（CONTEXT_AMD64 \| CONTROL \| INTEGER） |
| SegCs | `0x0033` |

→ 執行緒 Context 架構（AMD64）與 `SystemInfoStream` 處理器架構（AMD64）**吻合**，
與 `python311.dll` x86-64 映像一致 —— 無架構錯配。

---

## 9. 堆疊傾印涵蓋度與分析極限

（已驗證事實）

11 條執行緒的堆疊區間在 dump 中皆為 **100% 涵蓋**：

| tid | 堆疊區間 | size | 涵蓋 |
|---|---|---|---|
| 24572 | `0x551D5EF0E8`–`0x551D5F0000` | `0xF18` | 100% |
| 7596 | `0x551DF9F088`–`0x551DFA0000` | `0xF78` | 100% |
| 1136 | `0x551E18EE38`–`0x551E190000` | `0x11C8` | 100% |
| 4072 | `0x551E37EDC8`–`0x551E380000` | `0x1238` | 100% |
| 2056 | `0x551E56FBA8`–`0x551E570000` | `0x458` | 100% |
| 2272 | `0x551E75EDC8`–`0x551E760000` | `0x1238` | 100% |
| 8104 | `0x551E94F498`–`0x551E950000` | `0xB68` | 100% |
| 6260 | `0x551D9CEE48`–`0x551D9D0000` | `0x11B8` | 100% |
| 23104 | `0x551DBBF3E8`–`0x551DBC0000` | `0xC18` | 100% |
| 16720 | `0x551D7DF9A8`–`0x551D7E0000` | `0x658` | 100% |
| **13096（崩潰）** | `0x551DDAE818`–`0x551DDB0000` | `0x17E8` | 100% |

**分析極限（誠實揭露）**：
1. 崩潰執行緒僅約 `0x2C0` bytes 存活堆疊（RSP→區間頂端），故存活框架總數僅 15；**更深的呼叫者不可得**。
2. **無符號** → 無法還原函式名、行號、引數。**僅能提供位址級證據。**
3. 未載入 `.pdata` unwind 資訊做精確回溯 → §5.2 為**啟發式候選序列**，非保證連續的真實呼叫鏈；
   但「存活區間內 `.pyd` 數為 0」此一事實在任何合理回溯法下皆成立（該區間內不存在指向 `.pyd` 的返回位址）。
4. 本報告未、亦不得輸出任何記憶體內容。

---

## 10. 建議下一步（供幕僚長／Owner 決策，本工單未執行）

1. **取得 `python311.dll` 官方符號**（需另開工單授權下載 `python.org` 對應 3.11.15 的 PDB），
   即可把 `python311.dll+0xB5BDA` 對映到具體 CPython 函式 —— 這是目前最高價值的一步。
2. 若改採對照組策略：持續以 `WindowsSelectorEventLoopPolicy` 運行，觀察同型
   `c0000005 @ python311.dll+0xB5BDA` 是否復現；本次證據顯示該位移與事件迴圈 C 擴展**無直接關聯**。
3. 針對「讀取位址 `0xA0`」型缺陷，建議在 Python 層加裝 `faulthandler` + `sys.settrace` 取樣，
   以低成本鎖定崩潰前最後進入的 Python 框架。
4. 保留本次保全目錄至少至 CRASH-F1 結案；**注意 `data/` 已被 `.gitignore` 忽略**，
   故保全內容不會進版控，需另行備份至版控外儲存位置以免單點遺失。

---

## 11. 合規聲明

| 鐵律 | 遵守情形 |
|---|---|
| 0 Service Restarts | ✅ 全程未重啟／kill／干擾任何服務；四埠 PID 不變 |
| 嚴禁改動原始證據 | ✅ 只複製；原始 dump/WER 的 SHA256、bytes、時間戳皆未變 |
| 隱私紅線 | ✅ 全程未輸出任何記憶體內容／字串／對話文本／token／密鑰 |
| 禁止安裝新套件 | ✅ 無 `pip install` / `winget` / 下載；純標準庫 |
| 嚴禁 `git add -A`；`.dmp` 不入 Git | ✅ `git status --porcelain` 過濾 `\.dmp` 為空 |
| 嚴禁改註冊表、不碰 `src/**` | ✅ 未觸碰 |
| 保全目錄不在 `data\crash_dumps\` 下 | ✅ 置於 `data\crash_dump_archive\`（免受 keep-5 淘汰） |

**服務狀態（分析期間實測）**：`:8000`=**14056**、`8765`=**9576**、`8766`=**9592**、`8767`=**9584**。
**零重啟聲明**：本工單執行期間（約 21:51–22:0x）未對上述任何程序執行重啟、kill 或干擾動作。
