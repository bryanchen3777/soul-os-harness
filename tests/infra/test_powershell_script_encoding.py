"""PS-ENCODING-CONTRACT — 生產 ``.ps1`` 的編碼契約（含 UTF-8 BOM）。

為什麼有這條（2026-10-31 實測事故）
------------------------------------
Windows PowerShell 5.1 讀 ``.ps1`` 時：**有 BOM ⇒ UTF-8；沒有 BOM ⇒ 系統 ANSI 碼頁**
（本機 cp950）。生產 ``scripts/*.ps1`` 都帶 BOM，所以 5.1 以 UTF-8 解，中文註解沒事。

但只要有一支含中文的 ``.ps1`` 掉了 BOM，5.1 就改用 cp950 解 ⇒ **位元組層級的位移**
會讓中文註解的尾位元組被當成 cp950 的前導位元組，**把下一行的 ``#`` 配對掉**
⇒ 那一行從註解變成活程式碼 ⇒ parse 失敗。

實測（本票實撞到的）::

    #   舊流程在 Start-SoulOsServer 回來後就釋放 lock ⇒ 落在「鎖已解、port 尚未 LISTEN」
    #     00:08:04 WARN post-eb0b15f N=1/10 port_listen=False procs=2 -> restart
                                                       ↑ 這一行的 '#' 被吃掉了

前一行的「：」= UTF-8 ``EF BC 9A`` ⇒ ``9A`` 在 cp950 是合法前導位元組 ⇒ 配對掉下一行的
``#`` ⇒ ``port_listen=False`` 被 5.1 當成賦值敘述 ⇒
``The assignment expression is not valid`` / ``Array index expression is missing``。

⇒ **契約**：``scripts/**`` 底下**含非 ASCII** 的 ``.ps1`` 必須是 **UTF-8 with BOM**。
   （純 ASCII 的 ``.ps1`` 不需要 BOM —— 對它而言 BOM 只是雜訊，不強行約束。）

本檔釘住這條契約
----------------
* **E1** 含非 ASCII 的 ``scripts/**/*.ps1`` ⇒ 必須有 UTF-8 BOM；
* **E2** 這些檔案必須能以 **strict UTF-8** 解碼（不得有非法位元組）；
* **E3** 純 ASCII 的 ``.ps1`` 允許無 BOM（不得過度約束）；
* **E4** 牙齒：E1 必須抓得到合成的「有中文但無 BOM」樣本，且必須放行
  「有中文且有 BOM」與「純 ASCII 無 BOM」兩種樣本 —— 護欄自己不是盲腸。

本檔純 Python、唯讀，不啟任何行程、不碰 ``data/**``。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

UTF8_BOM = b"\xef\xbb\xbf"


def _ps1_files() -> list:
    return sorted(SCRIPTS_DIR.rglob("*.ps1"))


def _has_non_ascii(data: bytes) -> bool:
    return any(byte > 0x7F for byte in data)


def _violations(data: bytes) -> list:
    """回傳此檔案違反的契約條目（空清單 = 合格）。"""
    problems = []
    if _has_non_ascii(data):
        if not data.startswith(UTF8_BOM):
            problems.append(
                "含非 ASCII 但沒有 UTF-8 BOM ⇒ PS 5.1 會改用系統 ANSI 碼頁解碼"
                "（中文註解會被解壞，甚至把下一行的 '#' 吃掉 ⇒ 該行變成活程式碼）"
            )
        try:
            data[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append("BOM 之後的內容不是合法 UTF-8：%s" % exc)
    return problems


def test_e1_scripts_ps1_with_non_ascii_must_carry_utf8_bom():
    """E1：含非 ASCII 的 ``scripts/**/*.ps1`` 必須是 UTF-8 with BOM。"""
    offenders = []
    for path in _ps1_files():
        problems = _violations(path.read_bytes())
        if problems:
            offenders.append("  - %s: %s" % (path.relative_to(REPO_ROOT).as_posix(),
                                             "；".join(problems)))
    assert not offenders, (
        "以下 scripts/**/*.ps1 違反編碼契約（PS 5.1 會用錯誤的碼頁解碼它們）:\n"
        + "\n".join(offenders)
    )


def test_e2_scan_is_not_empty():
    """E2：掃描範圍不得是空的（避免「掃不到」被誤讀成「全綠」）。"""
    files = _ps1_files()
    assert len(files) >= 5, "只掃到 %d 支 .ps1，掃描範圍可能已損壞" % len(files)
    assert any(_has_non_ascii(p.read_bytes()) for p in files), (
        "沒有任何一支 .ps1 含非 ASCII ⇒ 掃描器可能壞掉（契約形狀已漂移）"
    )


def test_e3_pure_ascii_ps1_may_omit_the_bom():
    """E3：純 ASCII 的 ``.ps1`` 不強行要求 BOM（避免過度約束）。"""
    ascii_files = [p for p in _ps1_files() if not _has_non_ascii(p.read_bytes())]
    for path in ascii_files:
        assert not _violations(path.read_bytes()), (
            "純 ASCII 的 .ps1 不該被判違規：%s" % path.name
        )


def _eol_violations(data: bytes) -> list:
    """行尾一致性：同一個 .ps1 不得混用 CRLF 與純 LF。

    為什麼有這條（2026-10-31 實測）：本回合改 _start_plan_a.ps1 時，手寫的
    CRLF 轉義誤植成純 LF 一行 ⇒ 檔案變成 CRLF/LF 混用。PS 5.1 對混用**不會報錯**
    （兩種行尾都吃），但「這個檔案的行尾是什麼」變成不可預期 ⇒
    diff／雜湊／位元組級比較都失去可重現性。

    規則：**全 CRLF** 或 **全 LF** 皆可，**混用**不可。
    """
    crlf = data.count(b"\r\n")
    lf_only = data.count(b"\n") - crlf
    if crlf and lf_only:
        return ["行尾混用：CRLF %d 行 + 純 LF %d 行（同一個檔案必須一致）" % (crlf, lf_only)]
    return []


def test_e6_scripts_ps1_line_endings_must_not_be_mixed():
    """E6：scripts/**/*.ps1 的行尾不得混用 CRLF 與 LF。"""
    offenders = []
    for path in _ps1_files():
        problems = _eol_violations(path.read_bytes())
        if problems:
            offenders.append("  - %s: %s" % (path.relative_to(REPO_ROOT).as_posix(),
                                             "；".join(problems)))
    assert not offenders, (
        "以下 scripts/**/*.ps1 行尾混用（位元組級比較會失去可重現性）:\n"
        + "\n".join(offenders)
    )


def test_e7_eol_guard_has_teeth():
    """E7：E6 的牙齒 —— 混用樣本必須被判違規，全 CRLF／全 LF 必須放行。"""
    mixed = b"line one\r\nline two\nline three\r\n"
    all_crlf = b"line one\r\nline two\r\n"
    all_lf = b"line one\nline two\n"

    assert _eol_violations(mixed), "E6 抓不到 CRLF/LF 混用 ⇒ 護欄已失效"
    assert not _eol_violations(all_crlf), "E6 誤判『全 CRLF』⇒ 護欄太嚴"
    assert not _eol_violations(all_lf), "E6 誤判『全 LF』⇒ 護欄太嚴"


def test_e4_guard_has_teeth():
    """E4：E1 的牙齒 —— 合成樣本必須被正確判斷。"""
    bad = "Write-Host '中文'"      # 有中文、**無** BOM ⇒ 必須被判違規
    good_bom = UTF8_BOM + "Write-Host '中文'".encode("utf-8")
    good_ascii = b"Write-Host 'ascii only'"

    assert _violations(bad.encode("utf-8")), "E1 抓不到「有中文但無 BOM」⇒ 護欄已失效"
    assert not _violations(good_bom), "E1 誤判「有中文且有 BOM」⇒ 護欄太嚴"
    assert not _violations(good_ascii), "E1 誤判「純 ASCII 無 BOM」⇒ 護欄太嚴"


def test_e5_sandbox_copies_must_match_production_bom():
    """E5：沙盒副本必須與生產檔**位元組組態一致**（含 BOM）。

    ``tests/infra/test_plan_a_launch_inheritance.py`` 會把生產 ``.ps1`` **逐字**複製成
    沙盒副本再交給 PS 5.1 執行 ⇒ 沙盒副本少了 BOM，PS 5.1 就改用系統 ANSI 碼頁解碼
    ⇒ **沙盒測的不是生產會讀的那個檔**（2026-10-31 實測：中文註解把下一行的 ``#``
    吃掉 ⇒ parse 失敗）。

    實作：以 **AST** 解析被測檔，找出那三個「內容來自生產檔」的 ``write_text`` 呼叫點，
    逐一要求 ``encoding="utf-8-sig"``。用 AST 而非字串視窗 ⇒ 不會被 docstring 裡
    出現的檔名誤導（該誤導本票實測踩過一次）。

    （沙盒裡另有 ``parse_check.ps1`` —— 它是**純 ASCII** 的產生器，無需 BOM，
      不在本條契約內。）
    """
    test_path = REPO_ROOT / "tests" / "infra" / "test_plan_a_launch_inheritance.py"
    src = test_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    write_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ):
            enc = None
            for kw in node.keywords:
                if kw.arg == "encoding" and isinstance(kw.value, ast.Constant):
                    enc = kw.value.value
            write_calls.append((ast.get_source_segment(src, node) or "", enc))

    for target_marker, arg_marker in (
        ("_nohup_log_rotation.ps1", "read_text"),
        ("plan_a_path", "plan_a_sandbox"),
        ("ops_path", "ops_sandbox"),
    ):
        hits = [enc for seg, enc in write_calls
                if target_marker in seg and arg_marker in seg]
        assert len(hits) == 1, (
            "沙盒副本 %s 的寫入點應恰有 1 個，實際 %d 個 ⇒ 這個守門的形狀可能已漂移"
            % (target_marker, len(hits))
        )
        assert hits[0] == "utf-8-sig", (
            "沙盒副本 %s 沒有帶 BOM（utf-8-sig）⇒ 沙盒測的不是生產會讀的那個檔"
            % target_marker
        )
