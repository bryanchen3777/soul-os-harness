#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LINT-CHORE-34 — persona 排版正規化工具（**只拆行，不改語意**）。

工單：``logs/LINT-CHORE-34-IMPLEMENTATION.md``
素材／落地面：``personas/agent_<id>.md``（34 份既有）
SSOT（唯讀）：``docs/persona-drafts/agent_<id>.md``

── 為什麼有這張票 ───────────────────────────────────────────────
Notion 的項目符號清單在抓取時被壓成**行內** `` • ``（U+2022 + 半形空白）分隔序列，
一個段落裡原本多個獨立條目被併成一行。本工具把這種序列**拆回獨立行**，
除排版外**不動任何字元**。

── 唯一規則（決策已定）─────────────────────────────────────────
1. 分隔序列 ＝ ``[空白]* U+2022 [空白]+``。空白指半形空白與 Tab。
   ⇒ 「:•  x」與「。 •  x」兩種型態**同樣**是分隔序列。
2. 分隔序列**之前**的那段文字（本文的行首）**逐字保留、不加前綴** ——
   它是「非分隔內容」，不得被改動。
3. **被拆出來的每一段**前綴 ``- ``（半形連字號 + 半形空白），成為獨立行的
   markdown list item。每一段的**文字逐字保留**（標點、日文、全形空白皆不動）。
4. 表格列（行首 ``|``）、fenced code block（``` / ~~~）、縮排區塊（4 空白或 Tab 開頭）
   內的 `` • `` **不拆**。
5. 其餘一切不動：字元、標點、順序、標題、空行數、換行慣例（CRLF / LF）、檔尾換行。

── 冪等 ───────────────────────────────────────────────────────
對已正規化的文字再跑一次，輸出逐位元相同（``normalize(normalize(x)) == normalize(x)``）。

── 寫入安全（不可漂移的守門條件）───────────────────────────────
本 repo 有一條硬教訓（2026-10-19）：曾因「拿兩個各自會漂移的值去比對」
導致就地寫入生產 ``data/**``。因此本工具：
  * **沒有任何預設路徑**——檔案必須由呼叫端逐檔指名（positional args）。
  * ``--in-place`` 只會寫回呼叫端指名的那幾個檔案，**不會** glob、**不會**遞迴、**不會**掃目錄。
  * 寫入前呼叫 ``assert_write_allowed()``：目標路徑若含 ``data`` 目錄成分
    （生產狀態）或落在 ``docs/persona-drafts``（SSOT 唯讀）→ **拒寫**。
    這個判斷只吃「呼叫端自己給的那個路徑字串」，不拿任何行程期快取值互比，
    因此**不可漂移**。

用法::

    python scripts/normalize_persona_format.py personas/agent_ai.md          # dry-run，印 diff
    python scripts/normalize_persona_format.py --check personas/agent_ai.md   # 只檢查；有變動則 exit 1
    python scripts/normalize_persona_format.py --in-place personas/agent_ai.md  # 就地寫回
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

__all__ = [
    "BULLET",
    "SEPARATOR_RE",
    "LIST_PREFIX",
    "normalize_markdown",
    "normalize_bytes",
    "normalize_file",
    "assert_write_allowed",
    "main",
]

#: U+2022 BULLET（用跳脫寫法，避免任何編碼疑慮）。
BULLET = "\u2022"

#: 分隔序列＝分隔點前後的空白 + BULLET + BULLET 後至少一個空白。
#: 註：「:•  x」（BULLET 前無空白）與「。 •  x」（BULLET 前有空白）同樣命中。
SEPARATOR_RE = re.compile(r"[ \t]*" + BULLET + r"[ \t]+")

#: 拆出來的每一段的前綴。
LIST_PREFIX = "- "

#: fenced code block 的開／閉標記。
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
#: 表格列。
_TABLE_RE = re.compile(r"^\s*\|")
#: 縮排區塊（4 空白以上或 Tab 開頭）。
_INDENT_RE = re.compile(r"^(?: {4,}|\t)")

#: 禁止寫入的目錄成分（生產狀態）。
FORBIDDEN_WRITE_DIR_NAMES = frozenset({"data"})
#: 唯讀目錄序列（相對於 repo root）：SSOT 素材。
READ_ONLY_DIR_SEQUENCES = (("docs", "persona-drafts"),)


# ─────────────────────────────────────────────────────────────
# 行切分（只認 \n / \r\n；不碰 str.splitlines 的 exotic 分隔符）
# ─────────────────────────────────────────────────────────────


def _split_lines(text: str) -> List[Tuple[str, str]]:
    """把文字切成 ``[(body, terminator), ...]``。

    terminator ∈ {``"\\r\\n"``, ``"\\n"``, ``""``}；``""`` 只出現在**檔案最後一行
    且檔尾沒有換行**的情況（此時該行是唯一沒有 terminator 的一行）。

    刻意不用 ``str.splitlines()``：它連 ``\\x0b`` / ``\\x0c`` / ``\\x85`` /
    ``\\u2028`` / ``\\u2029`` 都會當行分隔，會切壞日文／中文內容。
    """
    out: List[Tuple[str, str]] = []
    pos = 0
    n = len(text)
    while pos < n:
        nl = text.find("\n", pos)
        if nl < 0:
            out.append((text[pos:], ""))
            break
        body = text[pos:nl]
        if body.endswith("\r"):
            out.append((body[:-1], "\r\n"))
        else:
            out.append((body, "\n"))
        pos = nl + 1
    return out


# ─────────────────────────────────────────────────────────────
# 核心：正規化
# ─────────────────────────────────────────────────────────────


def normalize_markdown(text: str) -> str:
    """把行內 `` • `` 分隔序列拆成獨立的 markdown list item。

    規則見模組 docstring。**純函式**、**冪等**、不改換行慣例。
    """
    lines = _split_lines(text)
    if not lines:
        return text

    # 本檔的換行慣例（第一個出現的 terminator；全檔無換行則預設 \n）。
    newline = next((term for _, term in lines if term), "\n")

    out: List[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for body, term in lines:
        fence = _FENCE_RE.match(body)
        if fence is not None:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            elif (
                marker[0] == fence_char
                and len(marker) >= fence_len
                and not fence.group(2).strip()
            ):
                in_fence, fence_char, fence_len = False, "", 0
            out.append(body + term)
            continue

        protected = in_fence or _TABLE_RE.match(body) or _INDENT_RE.match(body)
        if protected:
            out.append(body + term)
            continue

        parts = SEPARATOR_RE.split(body)
        if len(parts) == 1:
            out.append(body + term)
            continue

        # 行首那段（非分隔內容）逐字保留；被拆出來的每一段前綴 "- "。
        head = parts[0]
        items = parts[1:]
        pieces: List[str] = []
        if head:
            pieces.append(head)
        pieces.extend(LIST_PREFIX + item for item in items)
        for idx, piece in enumerate(pieces):
            last = idx == len(pieces) - 1
            # 最後一段用原行的 terminator（檔尾無換行時就是 ""——不得補上換行）；
            # 中間的段用本檔的換行慣例。
            out.append(piece + (term if last else (term or newline)))

    return "".join(out)


def normalize_bytes(data: bytes) -> bytes:
    """bytes 版的正規化（保留原檔的 CRLF / LF 與 BOM）。"""
    return normalize_markdown(data.decode("utf-8")).encode("utf-8")


# ─────────────────────────────────────────────────────────────
# 寫入安全
# ─────────────────────────────────────────────────────────────


def assert_write_allowed(path: Path) -> None:
    """拒絕寫入生產 ``data/**`` 與唯讀的 ``docs/persona-drafts/**``。

    只吃「呼叫端指名的那個路徑」——不與任何行程期快取值互比，因此不可漂移。
    """
    resolved = Path(path).resolve()
    parts = [p.lower() for p in resolved.parts]
    if "data" in parts:
        raise PermissionError(
            f"refuse to write into a production 'data/' path: {resolved}"
        )
    for i in range(len(parts) - 1):
        if tuple(parts[i : i + 2]) in READ_ONLY_DIR_SEQUENCES:
            raise PermissionError(
                f"refuse to write into the read-only SSOT tree: {resolved}"
            )


# ─────────────────────────────────────────────────────────────
# 檔案層
# ─────────────────────────────────────────────────────────────


def normalize_file(path: Path, in_place: bool = False) -> Tuple[bool, str, str]:
    """處理單一檔案。

    回傳 ``(changed, original_text, normalized_text)``。
    ``in_place=False`` 時**只讀不寫**。
    """
    path = Path(path)
    original_bytes = path.read_bytes()
    original = original_bytes.decode("utf-8")
    normalized = normalize_markdown(original)
    changed = normalized != original
    if changed and in_place:
        assert_write_allowed(path)
        # 只在「真的不一樣」時才寫入 —— 內容相同的檔案連 mtime 都不動。
        path.write_bytes(normalized.encode("utf-8"))
    return changed, original, normalized


def _diff(path: Path, original: str, normalized: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            normalized.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
        )
    )


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="normalize_persona_format",
        description=(
            "把 persona 檔裡的行內 ' • ' 分隔序列拆成獨立的 markdown list item"
            "（只改排版，不改語意）。檔案必須逐檔指名；本工具沒有任何預設路徑。"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="要處理的檔案（逐檔指名）。不會 glob、不會遞迴、不會掃目錄。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--in-place",
        action="store_true",
        help="就地寫回（只作用於上面指名的檔案）。",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="只檢查：任一檔案需要改動時 exit 1（不寫任何檔案）。",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只印摘要，不印 diff。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.paths:
        parser.error("必須至少指名一個檔案（本工具沒有預設路徑）")

    changed_files = 0
    unchanged_files = 0
    exit_code = 0

    for raw_path in args.paths:
        path = Path(raw_path)
        if not path.is_file():
            print(f"SKIP (not a file): {path}", file=sys.stderr)
            exit_code = 2
            continue

        changed, original, normalized = normalize_file(path, in_place=args.in_place)

        if changed:
            changed_files += 1
            before_lines = len(original.splitlines())
            after_lines = len(normalized.splitlines())
            print(
                f"CHANGED {path}  lines {before_lines} -> {after_lines}"
                f"  (+{after_lines - before_lines})"
            )
            if not args.quiet and not args.in_place:
                sys.stdout.write(_diff(path, original, normalized))
        else:
            unchanged_files += 1
            print(f"OK      {path}  (no change)")

    print(
        f"--- summary: {changed_files} changed / "
        f"{unchanged_files} unchanged / {changed_files + unchanged_files} scanned ---"
    )

    if args.check and changed_files:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
