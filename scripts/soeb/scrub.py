# -*- coding: utf-8 -*-
"""清除 SOEB run 產物中的憑證明文（復原工具）。

存在理由
--------
`client.redact()` 已是「欄位名白名單」，新產生的列不會帶明文。
但**已在記憶體中載入舊版 client 的 runner 行程**，會在跑完時寫出明文。
本工具用來事後補救，並可反覆執行（idempotent）。

安全設計（對應 AGENTS.md 鐵律 12）
--------------------------------
1. **寫入範圍寫死**：只動 `tests/results/soeb/`，**不提供任何改寫入範圍的旗標**。
   ⇒ 結構上不可能寫到生產 `data/`。不是「用兩個值比對」擋下來的。
2. **絕不製造明文副本**：不建立 `.bak`。舊版腳本會先備份原始內容（含明文）再清洗，
   等於把外洩複製一份；`.bak` 由本工具的無差別掃描自行處理（有殘留就會被清）。
3. **輸出不含憑證值**：只印端點名稱、長度、命中次數。
4. **無差別掃描**：不看副檔名白名單（先前教訓：`.jsonl.bak` 因 suffix=`.bak` 逃過複查）。

用法
----
    .venv\\Scripts\\python.exe scripts\\soeb\\scrub.py            # 實際清洗
    .venv\\Scripts\\python.exe scripts\\soeb\\scrub.py --dry-run  # 只報告

退出碼：0 = 掃描後零殘留；1 = 仍有殘留或無法處理。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.soeb.client import (  # noqa: E402
    is_sensitive_name,
    load_endpoints,
    redact,
)

# 寫死，不可由 CLI 覆寫（鐵律 12：移除寫入能力，而不是守門）。
SCAN_ROOT = REPO_ROOT / "tests" / "results" / "soeb"

REDACTED = "<redacted:leak-fallback>"


# --------------------------------------------------------------------------- #
# 載入憑證（只在記憶體中使用）
# --------------------------------------------------------------------------- #
def load_secrets() -> dict[str, str]:
    """回傳 {端點名稱: 金鑰值}；值不離開本行程。"""
    secrets: dict[str, str] = {}
    for name, endpoint in load_endpoints().items():
        key = getattr(endpoint, "api_key", None)
        if key:
            secrets[name] = key
    return secrets


def count_hits(text: str, secrets: dict[str, str]) -> dict[str, int]:
    return {name: text.count(value) for name, value in secrets.items() if text.count(value)}


# --------------------------------------------------------------------------- #
# 結構化清洗
# --------------------------------------------------------------------------- #
def scrub_structure(node: object, secrets: dict[str, str]) -> tuple[object, int]:
    """遞迴清洗：凡是憑證欄位名，值一律遮蔽；另對殘留的完整金鑰做字串替換。"""
    changed = 0
    if isinstance(node, dict):
        for key in list(node.keys()):
            value = node[key]
            if isinstance(value, str):
                new_value = value
                if is_sensitive_name(key):
                    scheme, sep, token = value.partition(" ")
                    if sep and token:
                        new_value = f"{scheme} {redact(token, name=key)}"
                    else:
                        new_value = redact(value, name=key)
                for secret in secrets.values():
                    if secret in new_value:
                        new_value = new_value.replace(secret, REDACTED)
                if new_value != value:
                    node[key] = new_value
                    changed += 1
            else:
                _, delta = scrub_structure(value, secrets)
                changed += delta
    elif isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, str):
                new_item = item
                for secret in secrets.values():
                    if secret in new_item:
                        new_item = new_item.replace(secret, REDACTED)
                if new_item != item:
                    node[index] = new_item
                    changed += 1
            else:
                _, delta = scrub_structure(item, secrets)
                changed += delta
    return node, changed


def scrub_jsonl_text(text: str, secrets: dict[str, str]) -> tuple[str, int]:
    """逐列清洗 JSONL；解析不了的列退回字串替換。"""
    out_lines: list[str] = []
    changed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            new_line = line
            for secret in secrets.values():
                new_line = new_line.replace(secret, REDACTED)
            if new_line != line:
                changed += 1
            out_lines.append(new_line)
            continue
        _, delta = scrub_structure(record, secrets)
        changed += delta
        out_lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(out_lines) + "\n", changed


def scrub_json_text(text: str, secrets: dict[str, str]) -> tuple[str, int] | None:
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    _, changed = scrub_structure(record, secrets)
    return json.dumps(record, ensure_ascii=False, indent=2) + "\n", changed


def scrub_text(text: str, secrets: dict[str, str]) -> tuple[str, int]:
    changed = 0
    new_text = text
    for secret in secrets.values():
        if secret in new_text:
            changed += new_text.count(secret)
            new_text = new_text.replace(secret, REDACTED)
    return new_text, changed


# --------------------------------------------------------------------------- #
# 檔案處理
# --------------------------------------------------------------------------- #
def rel(path: Path) -> str:
    """顯示用路徑：在 repo 底下就給相對路徑，否則給絕對路徑（不拋例外）。"""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_jsonl(path: Path) -> bool:
    """含 `.bak`：`observations.jsonl.bak` 也是 JSONL。"""
    return path.name.endswith(".jsonl") or path.name.endswith(".jsonl.bak")


def write_atomic(path: Path, text: str) -> None:
    """同目錄 temp + replace，避免中途失敗留下半個檔（不產生 .bak）。"""
    tmp = path.with_name(path.name + ".scrubtmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def iter_files() -> list[Path]:
    if not SCAN_ROOT.is_dir():
        return []
    return sorted(p for p in SCAN_ROOT.rglob("*") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清除 SOEB run 產物中的憑證明文")
    parser.add_argument("--dry-run", action="store_true", help="只報告，不改寫")
    args = parser.parse_args(argv)

    secrets = load_secrets()
    print("載入端點（只印長度，不印值）：")
    for name, value in secrets.items():
        print(f"  {name}: len={len(value)}")
    if not secrets:
        print("→ 沒有任何憑證可掃描。")
        return 1

    files = iter_files()
    print(f"\n掃描根：{rel(SCAN_ROOT)}（寫死，不可覆寫）")
    print(f"檔案總數（無差別，含 .bak）：{len(files)}")

    total_before = 0
    touched: list[Path] = []
    unhandled: list[Path] = []

    for path in files:
        raw = path.read_bytes().decode("utf-8", errors="replace")
        hits = count_hits(raw, secrets)
        if not hits:
            continue
        total_before += sum(hits.values())
        print(f"  🔴 {rel(path)}: {hits}")
        if args.dry_run:
            unhandled.append(path)
            continue

        if is_jsonl(path):
            new_text, changed = scrub_jsonl_text(raw, secrets)
        elif path.suffix.lower() == ".json":
            result = scrub_json_text(raw, secrets)
            if result is None:
                new_text, changed = scrub_text(raw, secrets)
            else:
                new_text, changed = result
        else:
            new_text, changed = scrub_text(raw, secrets)

        write_atomic(path, new_text)
        residual = count_hits(path.read_bytes().decode("utf-8", errors="replace"), secrets)
        print(f"     已改寫：結構清洗 {changed} 處，殘留={residual or '無'}")
        touched.append(path)
        if residual:
            unhandled.append(path)

    print(f"\n命中合計（清除前）：{total_before}")
    print(f"已改寫檔案數：{len(touched)}")

    if args.dry_run:
        print("RESULT: DRY-RUN（未改寫）" + ("，有命中" if total_before else "，無命中"))
        return 1 if total_before else 0

    # 複查：整個根目錄重掃一次（不是只驗改過的檔）
    print("\n全目錄複查（無差別）：")
    residual_files = 0
    for path in iter_files():
        raw = path.read_bytes().decode("utf-8", errors="replace")
        hits = count_hits(raw, secrets)
        if hits:
            residual_files += 1
            print(f"  🔴 {rel(path)}: {hits}")
    if residual_files:
        print(f"\nRESULT: FAIL — 仍有 {residual_files} 個檔案含憑證明文")
        return 1
    print("  （零命中）")
    print("\nRESULT: PASS — tests/results/soeb 之下零憑證明文（含備份檔）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
