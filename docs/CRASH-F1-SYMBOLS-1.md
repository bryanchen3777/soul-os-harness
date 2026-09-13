# CRASH-F1-SYMBOLS-1 — `python311.pdb` 符號化：3 個崩潰熱區 + 崩潰執行緒堆疊 → 具名函式

- **工單**：CRASH-F1-SYMBOLS-1
- **執行者**：DSH（developer subagent）
- **日期**：2026-09-12
- **性質**：FORENSICS / READ-ONLY（**0 服務重啟 / 0 生產資料變更 / 0 Frozen Contract 觸碰 / 0 `src/**` 變更 / 0 `data/**` 寫入**）
- **結論**：**符號化成功**（3/3 熱區全部落回 CPython 具名函式；崩潰執行緒 6 層 CPython 堆疊全部具名）。**GUID/Age 雙側完全相符** → 依工單授權，本報告入庫。

> **鐵律遵從（先驗證後符號化）**：本工單**先**完成下載、tarball SHA256、PDB GUID/Age 雙側比對，**確認相符後才**執行任何符號化。若不符，本檔不會存在。

---

## 0. 摘要（三行）

1. `python311.pdb` 與 dump 中的 `python311.dll` **GUID `93d9df12-5d13-43ba-9509-1f735f47fe74` / Age `1` 完全相符**（DLL 側、PDB 側、dbghelp 側三方獨立確認）。
2. **11 個 fault offset 全部落回具名函式**，收斂於 **3 個函式**：`dump_frame`、`PyCode_Addr2Line`、`advance`（第 11 個 `0x1C51DB` 為 `PyCode_Addr2Line` 的 **cold fragment，無 PDB 符號 → 誠實記 NO DATA**，並以 `.pdata` 提供結構性證據）。
3. **崩潰執行緒 tid 13096 的完整堆疊＝CPython `faulthandler` 崩潰傾印器本身**：
   `faulthandler_thread → _Py_DumpTracebackThreads → dump_traceback → dump_frame → PyCode_Addr2Line`。
   故障指令 `mov rcx,[rcx+0xA0]`（`RCX == 0`）＝ 讀取 `PyCodeObject._co_linearray`（PDB 型別確認 `+0xA0`）→ **存取位址恰為 `0xA0`**。

---

## 1. 下載與驗證鏈（item 1）

全部產物置於**倉庫之外**：`C:\Users\bbfcc\soul-os-forensics\pbs-build-20260602\`（作業前不存在，由本工單建立）。
**未安裝任何套件**（0 pip / 0 winget / 0 choco；**未安裝 capstone / pefile / lief**）——全程 **Python stdlib + `ctypes` 直呼系統內建 `C:\Windows\System32\dbghelp.dll`**。

| 步驟 | 動作 | 結果 |
|---|---|---|
| 1 | 下載 CPython 3.11.15 官方 Windows x64 建置（Owner 已授權 ~46 MiB） | `cpython-3.11.15+20260602-x86_64-pc-windows-msvc-install_only.tar.gz` = **48,477,564 bytes** |
| 2 | **tarball SHA256 驗證** | `985169a3aab1413eddaccf417c0ce36671ada2bcb8fcc0852b29963c3cfa0146` → **與同批 `SHA256SUMS`（121,896 bytes）逐字元相符 ✅** |
| 3 | 解壓取 PDB | `python\python311.pdb` = **17,403,904 bytes**，SHA256 `696747caa2ea7dec73b5ac26f69ac4b4102da5179f060577e38cb630ac5f3002` |
| 4 | 建立 **symsrv 索引目錄**（讓 dbghelp 自己再驗一次 index） | `symsrv\python311.pdb\93D9DF125D1343BA95091F735F47FE741\python311.pdb`（SHA256 與原始 PDB **逐位元組相同**） |
| 5 | **PDB GUID/Age 驗證** | 見 §2，**相符 → 放行符號化** |
| 6 | 符號化 | 見 §3 / §4 / §5 |

- **未使用微軟符號伺服器**（`msdl.microsoft.com` 未授權、未連線）；系統模組（`ucrtbase` / `kernel32` / `ntdll` / `KERNELBASE`）因此**無 PDB**，其名稱僅能靠匯出表，並另以 `.pdata` 嚴格否證（見 §4.3）。
- 被符號化的本機影像（唯讀）：`C:\Users\bbfcc\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python311.dll`（**5,846,016 bytes**）。

---

## 2. 雙側 GUID/Age + index 比對（item 2）

**配對唯一依據 = PDB GUID + Age。** 不符就必須停手（錯的 PDB 會給出**錯誤的函式名**，比 NO DATA 更危險）。

### 2.1 DLL 側（讀自 `python311.dll` 的 PE / CodeView `RSDS`）

| 欄位 | 值 |
|---|---|
| `TimeDateStamp` | `0x6A1F5973`（＝ 2026-06-02，與 PDB Stream#1 Signature **相同**） |
| `SizeOfImage` | `0x5E3000`（與 dump 內 `ModuleList` 記錄的 `SizeOfImage` **相同**） |
| CodeView 簽章 | `RSDS` |
| **PDB GUID** | **`93d9df12-5d13-43ba-9509-1f735f47fe74`** |
| **PDB Age** | **`1`** |
| **符號索引（symsrv）** | **`93D9DF125D1343BA95091F735F47FE741`** |
| 內嵌 PdbFileName | `C:\Users\Administrator\AppData\Local\Temp\python-build-0e3y7br2\Python-3.11.15\PCbuild\amd64\python311.pdb` |

### 2.2 PDB 側（讀自 `python311.pdb` 的 MSF 7.0 → PDB Stream #1）

| 欄位 | 值 | 對 DLL 側 |
|---|---|---|
| MSF magic | `Microsoft C/C++ MSF 7.00\r\n\x1aDS` | — |
| BlockSize / NumBlocks | `4096` / `4249` | — |
| NumStreams / Stream#1 size | `294` / `161` | — |
| Stream#1 Version | `20000404` | — |
| **Stream#1 Signature** | **`0x6A1F5973`** | **✅ 相符** |
| **Stream#1 GUID** | **`93d9df12-5d13-43ba-9509-1f735f47fe74`** | **✅ 相符** |
| **Stream#1 Age** | **`1`** | **✅ 相符** |
| 自算 symsrv index | `93D9DF125D1343BA95091F735F47FE741` | **✅ 相符（與工單指定值逐字元相同）** |

> **方法學註（易錯點）**：symsrv index 必須用 **canonical（顯示序）GUID** 轉 32 位大寫十六進位、再接**不補零**的 Age 十六進位。
> 直接拿記憶體中的位元組做 `.hex()` 會得到 `12DFD993135DBA4395091F735F47FE741`（**錯的**，位元組序反了）。本工單初版即踩此雷，已修正。

### 2.3 第三個獨立來源：dbghelp 自己的判定

以 dbghelp 載入**索引目錄**中的 PDB（強迫 dbghelp 自行驗證 index）後讀 `MODULEINFO`：

| 欄位 | 值 |
|---|---|
| `LoadedPdbName` | `...\symsrv\python311.pdb\93D9DF125D1343BA95091F735F47FE741\python311.pdb` |
| `PdbSig70` | 與 DLL 相符 |
| `PdbAge` | `1` |
| **`PdbUnmatched`** | **`0`（＝完全相符，非部分相符）** |
| `SymType` | `3`（`SymPdb`） |
| 能力旗標 | `LineNumbers=1 GlobalSymbols=1 Publics=1 TypeInfo=1` |
| 實際枚舉到的符號數 | **18,782**（其中 18,042 帶尺寸） |

**→ 三方（DLL / PDB / dbghelp）一致；`PdbUnmatched = 0`。放行符號化。**

### 2.4 dbghelp 呼叫關鍵細節（供後人複現；踩過才知道）

`SYMBOL_INFOW.SizeOfStruct` **必須是 `88`**（＝ `align8(offsetof(Name) + 2)`）。
傳 `ctypes.sizeof(SYMBOL_INFOW)`（4088）或 `86` 都只會得到 `FALSE` / `ERROR_INVALID_PARAMETER(87)`，**每一個位址都查不到**（且 `SymEnumSymbolsW` 仍正常，極易誤判為「PDB 沒符號」）。

---

## 3. 熱區：11 個 fault offset → 函式名（item 3）

Dump 內模組基底：`python311.dll @ 0x7FFE75190000`。

| # | RVA | 函式 + 位移 | 原始碼行 | 熱區 |
|---|---|---|---|---|
| 1 | `+0x26656C` | `dump_frame+0x18` | 1262 | A |
| 2 | `+0x266588` | `dump_frame+0x34` | 1265 | A |
| 3 | `+0x26659B` | `dump_frame+0x47` | 1265 | A |
| 4 | `+0x26659F` | `dump_frame+0x4B` | 1265 | A |
| 5 | `+0x26665D` | `dump_frame+0x109` | 1285 | A |
| 6 | `+0x266661` | `dump_frame+0x10D` | 1285 | A |
| 7 | `+0xB5BDA` | **`PyCode_Addr2Line+0xE`** ← **本 dump 的實際崩潰點** | 772 | B |
| 8 | `+0xB5C07` | `PyCode_Addr2Line+0x3B` | 776 | B |
| 9 | `+0xB5C30` | `PyCode_Addr2Line+0x64` | 769 | B |
| 10 | `+0xB5F53` | `advance+0x53` | 921 | B |
| 11 | `+0x1C51DB` | **NO DATA**（`PyCode_Addr2Line` 的 cold fragment，無 PDB 符號；見 §3.2） | 773（nearest-only） | C |

- 熱區 A `0x266554–0x266661`（64.5%）＝ **`dump_frame`**（RVA `0x266554`，size `0x162`）
- 熱區 B `0xB5BDA–0xB5F53`（19.6%）＝ **`PyCode_Addr2Line`（`0xB5BCC`，size `0x69`）＋ `advance`（`0xB5F00`，size `0x63`）**
- 熱區 C `0x1C51DB`（15.5%）＝ **同上函式的冷區塊，無符號 → NO DATA**

**11/11 有序號或結構性定位；11/11 有原始碼行號；10/11 有具名函式。**

### 3.1 各 fault offset 的實際指令（唯讀讀自影像；手寫 x86-64 長度解碼器，未用 capstone）

```
PyCode_Addr2Line @ 0xB5BCC
  +0x00  sub rsp, 0x58
  +0x04  movsxd r9, edx
  +0x07  mov r8, rcx
  +0x0A  test edx, edx
  +0x0C  js +0x56              ; → 0xB5C30  (#9)
  +0x0E  mov rcx, [rcx+0xA0]   ; <<< #7 崩潰指令：RCX == 0 → 讀 0x00000000000000A0
  +0x15  test rcx, rcx
  +0x18  jnz +0x10F5D8         ; → 0x1C51C2 (cold，熱區 C 的入口)

advance @ 0xB5F00
  +0x04  mov r11, [rcx+0x18]
  +0x0E  call +0x1A9           ; → 0xB60B7
  +0x1E  mov al, [r11]
  +0x53  cmp byte [rax], 0     ; <<< #10
  ...

dump_frame @ 0x266554
  +0x18  mov rsi, [rdx+0x20]              ; <<< #1  frame->f_code
  +0x34  mov rax, [rsi+0x70]              ; <<< #2  f_code->co_filename
  +0x47  mov rax, [rax+0x8]               ; <<< #3
  +0x4B  test dword [rax+0xA8], 0x00100000; <<< #4
  +0x95  mov rcx, [rbp+0x20]              ;   rcx = frame->f_code（第二次讀，見 §6）
  +0x99  mov rdx, [rbp+0x38]              ;   rdx = frame->prev_instr
  +0x9D  sub rdx, rcx
  +0xA0  sub rdx, 0xB8                    ;   sizeof(_PyInterpreterFrame)
  +0xAA  add edx, edx                     ;   addrq = 2 × 指令索引
  +0xAC  call PyCode_Addr2Line            ;   ← 這一行把 co 傳進去
  +0xB1  ...                              ; <<< #3 堆疊位址即此處（回返位址）
  +0x109 mov rax, [rdx+0x8]               ; <<< #5  frame->f_globals
  +0x10D test dword [rax+0xA8], 0x00100000; <<< #6
```

### 3.2 熱區 C（`0x1C51DB`）— 無符號，但以 `.pdata` 給出結構性結論

- `.pdata`（`IMAGE_DIRECTORY_ENTRY_EXCEPTION`，rva `0x580000` / size `0x23550` / **12,060 筆** `RUNTIME_FUNCTION`）中存在條目 **`0x1C51C2 .. 0x1C51E3`（size `0x21`）**，`0x1C51DB` 為其 **`+0x19`** → 是**獨立函式**（MSVC hot/cold splitting 的 cold fragment，**自帶 `.pdata` 但常無 PDB 符號**）。
- 正確對齊解碼後，`0x1C51DB` 確為**合法指令開頭**：`8B 04 81` = `mov eax, [rcx+rax*4]`（讀 `_co_linearray` 的 **4-byte** entry）。
- 其兄弟分支 `0x1C51D2` = `0F BF 04 41` = `movsx eax, word [rcx+rax*2]`（**2-byte** entry）；兩者由同一冷區塊入口 `0x1C51C2` 起算，並在 `0x1C51C8` 以 `cmp word [r8+0x36], 2` **選擇 2 或 4 位元組**。
- **PDB 型別資訊證實此推論**：`PyCodeObject._co_linearray_entry_size` **就在 `+0x36`**（見 §3.3）→ 冷區塊依 entry size 選 `movsx word [..*2]` 或 `mov eax [..*4]`，完全自洽。
- 兩個冷分支的 `jmp` 皆**收斂回 `0xB5C2B`（熱區 B 的函式本體內部）**，且入口 `0x1C51C2` 由 `0xB5BE4 jnz` 抵達 → **結論：`0x1C51DB` 屬於 `PyCode_Addr2Line` 的 out-of-line cold block，區段為 `.text`。**
- **依鐵律，無符號即 NO DATA**（不編造名稱）；上面是**結構性證據**，不是名稱。

### 3.3 `PyCodeObject` 版面（取自相符 PDB 的型別資訊，關鍵欄位）

| Offset | 成員 | 被哪條故障指令讀到 |
|---|---|---|
| `+0x20` | `co_names` | — |
| **`+0x36`** | **`_co_linearray_entry_size`** | 熱區 C 冷區塊的 `cmp word [r8+0x36], 2` |
| `+0x70` | `co_filename` | 熱區 A #2 `mov rax, [rsi+0x70]`（`dump_frame` 要印檔名） |
| `+0x88` | `co_linetable` | `0xB5BEA mov rax, [r8+0x88]` |
| **`+0xA0`** | **`_co_linearray`** | **本 dump 的崩潰指令 `mov rcx,[rcx+0xA0]`** |
| `+0xA8` | `_co_firsttraceable` | 熱區 A #4/#6 `test dword [rax+0xA8], …` |

另：`_PyInterpreterFrame` 版面亦由同一 PDB 證實 —— **`+0x20 = f_code`**、`+0x8 = f_globals`、`+0x38 = prev_instr`、`+0x48 = localsplus`。
（`PyFrameObject` 在此 PDB 中**查無型別**。）

---

## 4. 崩潰執行緒 tid 13096：15 個堆疊位址 → 具名框架（item 4）

- `RSP`（故障當下）＝ `0x551DDAFD40`；本執行緒被捕獲的堆疊區間 ＝ `[0x551DDAE818, 0x551DDB0000)`（`0x17E8` bytes，`0x2C0` live）。
- **15 個 slot 的值全部重新從 dump 讀回並逐位元組比對相符**（無一處靠推測）。

| # | 堆疊 slot | 值 | 模組 + 位移 | 符號 | 判定 |
|---|---|---|---|---|---|
| 1 | `0x551DDAFD48` | `0x7FFE7524BE9F` | python311+`0xBBE9F` | `_Py_write_impl+0xAF` | 具名（`dump_frame` 內的 I/O 呼叫，回返位址） |
| 2 | `0x551DDAFD90` | `0x7FFE75565F9C` | python311+`0x3D5F9C` | `` `string' `` | **NEAREST-ONLY（udt，無所屬函式）→ 極可能不是框架，僅為資料** |
| 3 | `0x551DDAFD98` | `0x7FFE753F6605` | python311+`0x266605` | **`dump_frame+0xB1`** | 具名 ★ 即 `0x266554 + 0xB1` |
| 4 | `0x551DDAFDB0` | `0x7FFE75329BC0` | python311+`0x199BC0` | `_silent_invalid_parameter_handler` | 具名 |
| 5 | `0x551DDAFDD8` | `0x7FFE753F6725` | python311+`0x266725` | **`dump_traceback+0x6D`** | 具名 |
| 6 | `0x551DDAFE00` | `0x7FFE7575C960` | python311+`0x5CC960` | `_PyRuntime+0x28960` | **資料（`[data]`），非框架** |
| 7 | `0x551DDAFE08` | `0x7FFE753F62C8` | python311+`0x2662C8` | **`_Py_DumpTracebackThreads+0x160`** | 具名 |
| 8 | `0x551DDAFE20` | `0x7FFE75329BC0` | python311+`0x199BC0` | `_silent_invalid_parameter_handler` | 具名（與 #4 同值，重複出現） |
| 9 | `0x551DDAFE38` | `0x7FFE753A398C` | python311+`0x21398C` | **`faulthandler_thread+0x50`** | 具名 |
| 10 | `0x551DDAFE50` | `0x7FFE753A393C` | python311+`0x21393C` | **`faulthandler_thread`** | 具名 |
| 11 | `0x551DDAFE68` | `0x7FFE752ACCAE` | python311+`0x11CCAE` | **`bootstrap+0x32`** | 具名 |
| 12 | `0x551DDAFE98` | `0x7FFEB0EDCD30` | ucrtbase+`0x2CD30` | **NO DATA** | 見 §4.3 |
| 13 | `0x551DDAFEC8` | `0x7FFEB223CD87` | kernel32+`0x2CD87` | **`BaseThreadInitThunk+0x17`** | ✅ .pdata 確認 |
| 14 | `0x551DDAFEF8` | `0x7FFEB3B4CAEC` | ntdll+`0xACAEC` | **NO DATA** | 見 §4.3 |
| 15 | `0x551DDAFF28` | `0x7FFEB148ED90` | KERNELBASE+`0x10ED90` | `UnhandledExceptionFilter+0x0`（函式起點已確認） | **非 live 框架**（不在迴溯鏈中）→ 判定為**殘留堆疊字** |

**15/15 已定位；11/15 具名函式；4/15 誠實 NO DATA。含行號者 9/15。**

### 4.1 為什麼系統模組是 NO DATA（而非「查不到就亂給名字」）

本機**未授權連線微軟符號伺服器**，`ucrtbase` / `ntdll` / `kernel32` / `KERNELBASE` 皆**無 PDB**。初版僅能做「匯出表最近的位址」（nearest export），這**極易給出錯名**（匯出之間夾著大量非匯出函式）。

因此追加了**嚴格否證步驟**：解析各模組的 `.pdata` `RUNTIME_FUNCTION` 表，取得該位址**真正所屬函式的起點**，再與匯出表聲稱的名稱比對：

| 模組 + 位址 | 匯出表nearest聲稱 | `.pdata` 實際所屬函式 | 判定 |
|---|---|---|---|
| ucrtbase+`0x2CD30` | `wcsrchr+0x150` | `0x2CD00 .. 0x2CD64`（起點 ≠ `wcsrchr@0x2CBE0`） | ❌ **否證 → NO DATA** |
| kernel32+`0x2CD87` | `BaseThreadInitThunk+0x17` | `0x2CD70 .. 0x2CDBD`，**起點 == 匯出 RVA `0x2CD70`** | ✅ **確認** |
| ntdll+`0xACAEC` | `RtlUserThreadStart+0x2C` | `0xACAC0 .. 0xACB1C`，起點 ≠ `RtlUserThreadStart@0xACA90` | ❌ **否證 → NO DATA** |
| KERNELBASE+`0x10ED90` | `UnhandledExceptionFilter+0x0` | `0x10ED90 .. 0x10F348`，**起點 == 匯出 RVA** | ✅ 名稱確認（但此位址非 live 框架） |

> **兩個假陽性被擋下**（`wcsrchr`、`RtlUserThreadStart`）。若不設這道關，本報告會寫出**兩個錯誤的函式名** —— 正是工單所警告的危險。

### 4.2 執行緒 13096 的雙快照（一處曾看似矛盾，已釐清）

Dump 內同一執行緒有**兩份 context**，且不一致：

| 來源 | `RIP` | `RSP` |
|---|---|---|
| `ExceptionStream`（**權威**：MSDN 定義為「引發例外的執行緒 context」） | `python311!PyCode_Addr2Line+0xE` | `0x551DDAFD40` |
| `ThreadListStream`（dump 傾印當時） | `ntdll!NtWaitForMultipleObjects+0x14` | `0x551DDAE818` |

- `ExceptionStream.ThreadId == 13096`，且其 `RSP` **落在 13096 自己的堆疊區間內**；`RIP == ExceptionAddress` ✅。
- `ThreadList` 對 13096 的 `RSP` **恰等於其被捕獲堆疊區間的起點**（此 dump 的傾印器以「傾印當時的 RSP」為區間起點；11 個執行緒**無一例外**成立）。
- **`ThreadList.RSP` 比故障 `RSP` 低 `0x1528`（＝更深）。** 執行緒在故障後才會**往下長**堆疊（進入 ntdll 的未處理例外處理機制），不可能反向。
- **→ 判定：`ExceptionStream` 為故障瞬間的真實 context；`ThreadList` 為「故障後、WER 傾印期間該執行緒停在 `NtWaitForMultipleObjects`」的較晚快照。兩者不衝突。**

### 4.3 其他 10 個執行緒的 `ThreadList` 狀態（旁證）

| 執行緒 | `ThreadList` RIP | 狀態 |
|---|---|---|
| 24572 / 16720 | `ntdll!ZwRemoveIoCompletion+0x14` | IOCP 等待 |
| 7596 / 1136 / 4072 / 2272 / 8104 / 6260 / 23104 | `ntdll!NtWaitForSingleObject+0x14` | 單一物件等待 |
| 2056 | `ntdll!ZwWaitForWorkViaWorkerFactory+0x14` | thread-pool worker 閒置 |
| **13096** | `ntdll!NtWaitForMultipleObjects+0x14` | 即上表之「故障後快照」 |

（名稱同樣為**匯出表nearest**，未經 `.pdata` 逐項確認 → 本表僅作旁證，不列為交付結論。）

---

## 5. 4c：完整堆疊回溯成功（item 5）

`StackWalk64` + `PREAD_PROCESS_MEMORY_ROUTINE64`（以 dump 的 `Memory64ListStream` 為後端）從 `ExceptionStream` context 起溯：

```
seed: RIP=0x7FFE75245BDA RSP=0x551DDAFD40 RBP=0x1F44E4D0928  MachineType=0x8664

 #  ADDRESS           MODULE+OFFSET     SYMBOL                                        LINE
 1  0x7FFE75245BDA    python311+0xB5BDA PyCode_Addr2Line+0xE                          772   <-- 崩潰點
 2  0x7FFE753F6605    python311+0x266605 dump_frame+0xB1                               1275
 3  0x7FFE753F6725    python311+0x266725 dump_traceback+0x6D                           1340
 4  0x7FFE753F62C8    python311+0x2662C8 _Py_DumpTracebackThreads+0x160               1447
 5  0x7FFE753A398C    python311+0x21398C faulthandler_thread+0x50                     637
 6  0x7FFE752ACCAE    python311+0x11CCAE bootstrap+0x32                                182
 7  0x7FFEB0EDCD30    ucrtbase+0x2CD30   NO DATA（無 PDB；`wcsrchr` 已否證）
 8  0x7FFEB223CD87    kernel32+0x2CD87   BaseThreadInitThunk+0x17（.pdata 確認）
 9  0x7FFEB3B4CAEC    ntdll+0xACAEC      NO DATA（無 PDB；`RtlUserThreadStart` 已否證）
StackWalk64 於 9 框架後回傳 FALSE（err=0，＝正常結束：已到執行緒起點）
ReadProcessMemory callback: 447 hit / 0 miss
```

### 5.1 交叉驗證（兩條獨立證據互相鎖死）

- **回返位址位置**：`PyCode_Addr2Line` 的序言是 `sub rsp, 0x58`，故其回返位址應落在 `[RSP + 0x58]`。實測 `0x551DDAFD40 + 0x58 = 0x551DDAFD98`，而該 slot 的值正是 **`dump_frame+0xB1`**（§4 表 #3）→ **與 `StackWalk64` 的第 2 框架完全一致**。這是一條幾乎不可能巧合的算術鎖。
- **`dump_frame` 的呼叫點**：`+0xAC` 是 `call PyCode_Addr2Line`，`+0xB1` 緊接其後 = 回返位址 ✅（§3.1 逐指令解碼）。
- 熱區 A 全部落在 `dump_frame`、熱區 B/C 全部落在 `PyCode_Addr2Line`/`advance` → **與回溯鏈的函式集合完全一致**（無任何熱區落在這條鏈之外）。

### 5.2 故障當下的決定性暫存器（A）

```
ExceptionStream:  tid = 13096   code = 0xC0000005   addr = 0x7FFE75245BDA
                  ExceptionInformation = [0x0 (READ), 0xA0]
                  ContextFlags = 0x0010005F
Rcx = 0x0000000000000000   <-- 近 null 基底
Rip = 0x7FFE75245BDA       Rax = 1        Rbx = 3        Rdx = R9 = 0x4E4D07D8
Rsp = 0x551DDAFD40         Rbp = 0x1F44E4D0928            Rsi = 0x1F455DF2970
Rdi = 0x7FFE75565F9C       R11 = 0x246    R12 = 1        R15 = 3
一致性檢查：Rip == ExceptionAddress ✅ ；Rsp 落在本執行緒堆疊區間內 ✅
```

**故障指令為 `mov rcx, [rcx+0xA0]`、`RCX == 0` → 存取位址 = `0x00000000000000A0`**，與 `ExceptionInformation[1] = 0xA0` **逐位元組相符**。

> **注意**：x64 `CONTEXT` 的正確位移為 `Rax@0x78`、`Rcx@0x80`、`Rdx@0x88`、`Rsp@0x98`、`Rbp@0xA0`、`Rip@0xF8`、`ContextFlags@0x30`。
> 既有腳本 `parse_minidump.py` 中的 `CTX_RAX=0x88` / `CTX_RCX=0x90` **是錯的**（本工單以標準版面覆寫，並以「`Rip` 等於 `ExceptionAddress`」與「`Rsp` 落在堆疊內」兩項自我驗證通過）。**該檔案未被修改**（唯讀沿用）。

---

## 6. 一句話解讀（item 6）

> **【推論，非事實】** 244 次 `python311.dll` + `c0000005` 的崩潰，**是 CPython 的 `faulthandler` 崩潰傾印器自己在傾印時踩空**：`faulthandler_thread` 走訪解譯器框架時，把一個已經不是有效 `PyCodeObject` 的指標交給 `PyCode_Addr2Line`，而 `PyCode_Addr2Line+0xE` 毫無防備地讀 `co->_co_linearray`（`+0xA0`）→ `[NULL+0xA0]`。

支持這個推論的**已驗證事實**（推論之外的部分都是硬證據）：

1. 崩潰執行緒的**完整堆疊**（6 層，全部具名且有行號）＝ `faulthandler` 的 traceback 傾印路徑，**起點到終點都在傾印器裡**。
2. 11 個 fault offset **全部**落在這條路徑的三個函式內 —— 沒有例外。
3. 崩潰指令讀的位移 `0xA0` 經 PDB 型別確認 = `PyCodeObject._co_linearray`，且 `ExceptionInformation[1]` 恰為 `0xA0`。

**推論部分（明確標示為推測，尚未證實）**：

- `dump_frame` 在 `+0x18` 先把 `frame->f_code` 讀進 `rsi`（該次讀取**未**觸發例外，否則會在 `+0x34` 的 `[rsi+0x70]` 以存取位址 `0x70` 崩潰），卻在 `+0x95` **重新讀同一個位址** `[rbp+0x20]` 後傳給 `PyCode_Addr2Line` 而得到 `0`。這意味著**兩次讀取之間該記憶體被改寫了**。合理的機制候選（**未經證實**）：
  - (a) `faulthandler` **不停世界**，它在走訪**別的執行緒**的解譯器框架；該執行緒同時在回返／清理該 `_PyInterpreterFrame`（`f_code` 被清為 `NULL`）→ 競態。
  - (b) 走訪到的框架指標本身已失效（`previous` 鏈指向已被回收／重用的記憶體）。
- **因此**：真正的**原始缺陷**（若有）可能不在這條路徑上，而是「**先有別的執行緒把某個 `_PyInterpreterFrame`/`PyCodeObject` 弄壞**，再被傾印器踩到」；亦可能「週期性 `dump_traceback_later` 走訪本身就是缺陷」。
  **本工單沒有足以區分這兩者的證據**（傾印器在崩潰前從未成功寫出崩潰執行緒的堆疊 —— 見 CRASH-OBS-2/3）。
- **可證偽的下一步**（供後續工單）：`faulthandler` 走訪的是**哪一個**執行緒的框架、以及該執行緒在故障瞬間是否正在 `_PyEvalFrameClearAndPop`。

### 6.1 本工單**不支持**的既有結論（必須明示）

- 既有 `docs/CRASH-F1-POSTMORTEM-1.md` 把崩潰執行緒的堆疊**當成故障應用程式的堆疊**來解讀（其 §5.2 的 15 個 slot 清單本身正確、已被本工單逐值複驗）——**但該堆疊是傾印器自己的堆疊，不是「應用程式在崩潰時正在做什麼」**。這是本工單對既有結論的**實質修正**。
- **本工單不主張**「`0xA0` 是 244 次崩潰的共同存取位址」。`0xA0` 只被**本 dump** 證實。熱區 A 的故障指令位移分別是 `+0x20` / `+0x70` / `+0x8` / `+0xA8`（→ 存取位址會隨基底而異）；若那些崩潰也回報 `0xA0`，則代表基底是**小的非零值**（例如 `[rdx+0x20]` 配 `rdx=0x80`），那本身是另一個值得追的線索。**此點列為待驗，未被本工單證實。**

---

## 7. 使用的方法 / API，與鐵律遵守情形（item 7）

### 7.1 使用的 API / 技術（全部在授權範圍內）

| 類別 | 具體 |
|---|---|
| 容器解析 | `MINIDUMP_HEADER`(`MDMP`) + 17 個 stream：`ThreadList(3)` / `ModuleList(4)` / `Exception(6)` / `Memory64List(9)` |
| PE 解析 | DOS/COFF/OptionalHeader、`IMAGE_DIRECTORY_ENTRY_EXCEPTION(3)`＝`.pdata`、`IMAGE_DIRECTORY_ENTRY_EXPORT(0)`＝匯出表、CodeView `RSDS`（純 stdlib `struct`） |
| PDB 解析 | MSF 7.0：superblock → block map → directory → PDB Stream #1（Version/Signature/Age/GUID） |
| 符號引擎 | `dbghelp.dll`（系統內建）via `ctypes`：`SymInitializeW` / `SymSetOptions` / `SymSetSearchPathW` / `SymLoadModuleExW` / `SymGetModuleInfoW64` / `SymFromAddrW` / `SymGetLineFromAddrW64` / `SymEnumSymbolsW` / `SymSrvGetFileIndexesW` / `SymGetTypeFromNameW` / `SymGetTypeInfo` |
| 堆疊回溯 | `StackWalk64` + `SymFunctionTableAccess64` + `SymGetModuleBase64` + 自製 `ReadProcessMemory` callback（後端＝dump 的 `Memory64ListStream`） |
| 反組譯 | **手寫** x86-64 長度解碼器（REX prefixes、ModRM+SIB、`0x8B/0x89/0x8D/0x83/0x85/0x39/0x3B/0x0F xx/0xE8/0xE9/0x74-0x7F/0xC3/0xCC/0xFF` 等）——**未使用 capstone** |
| 型別查詢 | `SymGetTypeFromNameW` + `SymGetTypeInfo`（`TI_GET_CHILDRENCOUNT=13` / `TI_FINDCHILDREN=7` / `TI_GET_OFFSET=10` / `TI_GET_SYMNAME=1` / `TI_GET_LENGTH=2`） |

### 7.2 鐵律逐條核對

| 鐵律 | 狀態 | 證據 |
|---|---|---|
| **0 服務重啟**（不得重啟／kill／干擾 `:8000` 與 8765/8766/8767） | ✅ **遵守** | 見 §9 的 PID 表；**全程未執行** `server_ops.ps1` / `Stop-Process` / `taskkill` / 任何重啟 |
| **0 套件安裝**（禁 pip/winget/choco；**明文禁 capstone / pefile / lief**） | ✅ **遵守** | 只用 stdlib + `ctypes`；反組譯器為手寫；**未安裝任何套件** |
| **0 生產資料變更**（不得碰 `data/**`，尤其 `data\faulthandler.log`） | ✅ **遵守** | 只**讀** `data\crash_dump_archive\...\python.exe.1132.dmp`；`data\faulthandler.log` **未被讀／寫／複製／改名／刪除**；`data/**` 0 寫入 |
| **不得修改原始 dump**（及外部分析副本）；事後複驗 SHA256 | ✅ **遵守** | 見 §8；兩份均為唯讀，複驗雜湊不變 |
| **下載物必須留在 repo 之外**；`git status --porcelain` 不得出現大檔 | ✅ **遵守** | 所有產物在 `C:\Users\bbfcc\soul-os-forensics\pbs-build-20260602\`（repo 外）；repo 內**零新增二進位檔** |
| **禁用 `git add -A` / `git add .`** | ✅ **遵守** | 僅以顯式路徑 `git add docs/CRASH-F1-SYMBOLS-1.md logs/ENGINEERING_STATE.md` |
| **0 Frozen Contract 觸碰**；0 `src/` 變更；0 faulthandler 設定變更 | ✅ **遵守** | `git status` 中 `src/**` 與 `configs/**` **無本工單產生之變更**；`scripts/run_server.py` 的既有 WIP 為**他票產物**，本工單未 stage |
| **先驗證後符號化** | ✅ **遵守** | §2 三方 GUID/Age/index 全部相符後，才執行 §3–§5 的符號化 |

**未踩任何鐵律。**

---

## 8. Dump SHA256 複驗（item 8）

| 對象 | 大小 | SHA256 | 分析後複驗 |
|---|---|---|---|
| `data\crash_dump_archive\20260912_2029_pid1132\python.exe.1132.dmp` | **272,583,424** | `09A1C7EC0899001A59360CD4F42A573904C1A641578426DC2F02029791A4B2ED` | **不變 ✅** |
| `C:\Users\bbfcc\soul-os-forensics\20260912_2029_pid1132\python.exe.1132.dmp`（外部分析副本） | 272,583,424 | 同上 | **不變 ✅** |

- 兩份皆以**唯讀**開啟；`LastWriteTime` 與大小於作業前後一致。
- 附帶：本工單亦記錄 `python311.pdb` 與 symsrv 索引副本的 SHA256（§1），兩者逐位元組相同。

---

## 9. 四埠 PID 表 + 零重啟聲明（item 9）

| 埠 | 角色 | PID | 狀態 |
|---|---|---|---|
| `:8000` | 主服務（**正跑 `SOUL_OS_EVENT_LOOP=selector` 48h 單一變數實驗**） | **14056** | **未動** |
| `8765` | VC Akane | **9576** | **未動** |
| `8766` | VC Mai | **9592** | **未動** |
| `8767` | VC Rem | **9584** | **未動** |

### 🔒 正式聲明

**本工單全程 0 服務重啟、0 程序終止、0 訊號干擾。** 上述四個 PID 於作業前後實測一致，無任何 restart / kill / attach / suspend 行為。
**生產中執行的 `SOUL_OS_EVENT_LOOP=selector` 48 小時單一變數實驗完全未被觸碰** —— 本工單為**純離線鑑識**：讀取一個已靜止的 `.dmp` 檔與一份下載到 repo 外的 PDB，**與在線服務零互動**（連 `data/**` 都只讀了那個 dump 本身）。
`git status --porcelain` 於收尾時**無任何大檔**（下載物全部在 repo 外）。

---

## 10. 限制、NO DATA 清單與後續（item 10）

### 10.1 誠實的 NO DATA 清單（依鐵律，無符號即不給名）

| 項目 | 狀態 | 原因 |
|---|---|---|
| `+0x1C51DB`（熱區 C） | **NO DATA** | `PyCode_Addr2Line` 的 hot/cold 分裂冷區塊，**PDB 無符號**；已用 `.pdata` 給出結構性歸屬（§3.2），但**不編造名稱** |
| `ucrtbase.dll+0x2CD30` | **NO DATA** | 無 PDB；匯出表nearest（`wcsrchr`）**已被 `.pdata` 否證** |
| `ntdll.dll+0xACAEC` | **NO DATA** | 無 PDB；匯出表nearest（`RtlUserThreadStart`）**已被 `.pdata` 否證**（真正所屬函式 `0xACAC0..0xACB1C` 未匯出） |
| 系統模組（`KERNELBASE` 等） | 名稱不可靠 | 同上；未授權連線微軟符號伺服器 |
| 熱區 A/B 之**其他 10 個 offset 的存取位址** | **未驗** | 本機僅有 **1 份** dump（該次的崩潰點是 `+0xB5BDA`）；其餘 offset 出自 WER 彙總，**無對應 dump 可驗** |
| 「244 次崩潰的共同存取位址是否為 `0xA0`」 | **未驗** | 同上；`0xA0` 僅由本 dump 證實（§6.1） |
| 各熱區 offset 的**執行緒歸屬** | **未驗** | 同上 |

### 10.2 已確實完成的交付

- ✅ 3/3 熱區定位到具名函式（`dump_frame` / `PyCode_Addr2Line` / `advance`）；11/11 offset 有行號。
- ✅ 崩潰執行緒 **9 框架完整回溯**，其中 6 層 CPython 框架**全部具名 + 有行號**。
- ✅ 崩潰機制在**指令層**閉合：`mov rcx,[rcx+0xA0]`（`RCX=0`）↔ `PyCodeObject._co_linearray`（PDB 型別確認）↔ `ExceptionInformation[1]=0xA0`。
- ✅ 修正一項既有誤讀（崩潰堆疊屬**傾印器**，非應用程式）+ 擋下兩個假陽性函式名。

### 10.3 建議的下一步（供幕僚長／Owner 決策；本工單不自行擴大範圍）

1. **等待 CRASH-OBS-3 生效後重啟**：目前線上進程仍**沒有**致命傾印處理器（`faulthandler.enable()` 從未被呼叫）；下一次原生崩潰才會產出**崩潰執行緒自己的** Python 堆疊。那將直接把 §6 的兩個候選機制（競態 vs 失效框架指標）分開。
2. **補第二份 dump**：若能在 selector 實驗窗口內再取一份 dump，即可比對「是否為同一位移」與「存取位址是否為 `0xA0`」。
3. **（可選、唯讀）** 以相符 PDB 的型別資訊 + `_PyInterpreterFrame` / `PyThreadState` 版面，反推 `_Py_DumpTracebackThreads` 走訪的是哪個 tstate（本工單已驗證型別查詢管道可用）。

---

## 附錄 A — 產物清單（全部在 repo 外）

目錄：`C:\Users\bbfcc\soul-os-forensics\pbs-build-20260602\`

| 檔案 | 大小 | SHA256 |
|---|---|---|
| `cpython-3.11.15+20260602-x86_64-pc-windows-msvc-install_only.tar.gz` | 48,477,564 | `985169a3aab1413eddaccf417c0ce36671ada2bcb8fcc0852b29963c3cfa0146` |
| `SHA256SUMS` | 121,896 | （權威清單，tarball 逐字元相符） |
| `python\python311.pdb` | 17,403,904 | `696747caa2ea7dec73b5ac26f69ac4b4102da5179f060577e38cb630ac5f3002` |
| `python\python3.pdb` | 126,976 | `808225d97a790005b286682344b5e6c79e78f26dce50489b6d5d6a08704a15bb`（備用，未使用） |
| `symsrv\python311.pdb\93D9DF125D1343BA95091F735F47FE741\python311.pdb` | 17,403,904 | 同 `python311.pdb`（索引副本） |

腳本（皆唯讀取樣，可重跑）：
`verify_pdb.py`（GUID/Age 閘門）· `symbolicate.py`（**主符號化程式**）· `stackwalk.py`（`StackWalk64` 回溯）· `deepdive2.py`（`_pdata` + 手寫解碼器）· `verify_extra.py`（執行緒 context 交叉核對 + 型別查詢）· `final_checks.py`（`ThreadList` raw 欄位 + 型別版面）· `callsite.py`（呼叫點定位）· `pdatalast.py`（系統模組名稱否證）
輸出：對應之 `*_output.txt`。

## 附錄 B — 本報告中「事實 / 推論」的分界

| 標記 | 含義 |
|---|---|
| **事實（已驗證）** | 可由 dump、相符 PDB、影像位元組、或 `dbghelp` 直接重現者 |
| **推論（未證實）** | §6 明確標示者；以及 §4.3 僅靠匯出表得出、未經 `.pdata` 逐項確認的執行緒狀態 |
| **NO DATA** | 無符號可用，且**拒絕編造名稱**（§10.1 逐項列出） |
| **待驗** | 本工單無足夠材料判定者 |

**本報告未 commit 任何 `src/**` 變更、未重啟任何服務、未改動任何生產資料。**
