"""model_registry.py — VC 語音模型的**單一事實來源**與設定檔編輯器（2026-10-24）。

為什麼要這個模組
----------------
1. **白名單散落三處會漂**：`lang_tags.py`（語言標籤）、`drama3_markup.py`（表演標記）、
   `scripts/vc_model.ps1`（切換腳本）各自維護一份「哪些模型支援什麼」。
   官方 enum 一旦新增模型，三處不同步就會出現「程式認為不支援、腳本卻准你切」之類的分裂。
   ⇒ 全部收斂到這裡。

2. **拼錯會靜默 fallback**：Fish Audio 的模型是 **HTTP header `model`** 選的，
   header 缺或拼錯**不會報錯**，會安靜退回 s2.1-pro（實測：header `drama_3` → 與 s2.1-pro
   完全一致的音訊量測）。⇒ 寫入設定前必須先驗證名稱在官方 enum 內，
   否則「改了設定」與「真的換了模型」會悄悄分家。

3. **只能改 `fish_audio` 區塊**：`config.json` 裡的 `llm.model` 是 **LLM 模型**
   （`deepseek-v4.1-flash:1017`），與 TTS 無關；用整份 JSON 重寫／模糊取代會誤傷它。

本模組是純函式（無 I/O 除 CLI），可直接單測；CLI 供 `scripts/vc_model.ps1` 呼叫。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, Optional, Tuple

# ── 官方 enum（Fish Audio OpenAPI 的 model 參數；拼錯會靜默 fallback）──────────
# POST /v1/tts 的 model header enum
TTS_MODELS: Tuple[str, ...] = ("s1", "s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3")
# POST /v1/asr 的 model header enum
ASR_MODELS: Tuple[str, ...] = ("transcribe-1", "transcribe-1-pro")

# ── 哪些模型支援哪些能力（drama-3 才有「演出」能力）────────────────────────
# 語言標籤 <chinese>/<english>/… 只有 drama-3 會消費；其他模型會把它唸出來。
MODELS_WITH_LANGUAGE_TAGS = frozenset({"drama-3"})
# 表演標記（方向指示＋配對標籤）同理。
MODELS_WITH_DRAMA3_MARKUP = frozenset({"drama-3"})


class ModelConfigError(ValueError):
    """模型名不合法／設定檔結構不符。"""


def validate_tts_model(name: str) -> str:
    if name not in TTS_MODELS:
        raise ModelConfigError(
            f"TTS 模型名不在官方白名單內：{name!r}（允許：{' / '.join(TTS_MODELS)}）"
        )
    return name


def validate_asr_model(name: str) -> str:
    if name not in ASR_MODELS:
        raise ModelConfigError(
            f"ASR 模型名不在官方白名單內：{name!r}（允許：{' / '.join(ASR_MODELS)}）"
        )
    return name


# ── 設定檔編輯（只動 fish_audio 區塊內的 model / asr_model）──────────────────

_FISH_BLOCK_RE = re.compile(r'"fish_audio"\s*:\s*\{')


def _fish_audio_span(text: str) -> Tuple[int, int]:
    """回傳 fish_audio 區塊的 (start, end) 索引（end 為區塊的 `}`）。"""
    m = _FISH_BLOCK_RE.search(text)
    if not m:
        raise ModelConfigError("設定檔沒有 fish_audio 區塊")
    depth = 0
    for i in range(m.end() - 1, len(text)):   # m.end()-1 ＝ '{' 的絕對索引
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return m.start(), i
    raise ModelConfigError("fish_audio 區塊未閉合")


def read_fish_audio_models(text: str) -> Dict[str, str]:
    """讀出 fish_audio 的 model / asr_model（缺 asr_model ⇒ 空字串）。"""
    obj = json.loads(text)
    fa = (obj.get("fish_audio") or {})
    return {
        "model": str(fa.get("model") or ""),
        "asr_model": str(fa.get("asr_model") or ""),
    }


def set_fish_audio_models(
    text: str,
    *,
    tts: Optional[str] = None,
    asr: Optional[str] = None,
) -> str:
    """把 fish_audio 內的 model / asr_model 換掉；回傳新文字。

    保證：
    * 模型名先過白名單（拼錯在此就被擋，不會變成靜默 fallback）；
    * **只改 fish_audio 區塊內**的鍵（`llm.model` 不得被誤傷）；
    * 回傳前先驗證新文字仍是合法 JSON。
    """
    if tts is not None:
        validate_tts_model(tts)
    if asr is not None:
        validate_asr_model(asr)

    start, end = _fish_audio_span(text)
    block = text[start:end + 1]

    def _replace(block_text: str, key: str, value: str) -> str:
        pattern = re.compile(r'"' + re.escape(key) + r'"\s*:\s*"[^"]*"')
        if not pattern.search(block_text):
            raise ModelConfigError(f"fish_audio 區塊內找不到鍵 {key!r}")
        return pattern.sub(lambda m: f'"{key}": "{value}"', block_text, count=1)

    if tts is not None:
        block = _replace(block, "model", tts)
    if asr is not None:
        block = _replace(block, "asr_model", asr)

    out = text[:start] + block + text[end + 1:]
    try:
        json.loads(out)
    except json.JSONDecodeError as exc:  # pragma: no cover - 防呆
        raise ModelConfigError(f"編輯後的 JSON 無法解析：{exc}") from exc
    return out


def apply_to_file(path: str, *, tts: Optional[str] = None, asr: Optional[str] = None) -> Dict[str, str]:
    """就地套用變更（保持原編碼與行尾）；回傳 {old_model, old_asr_model, new_model, new_asr_model}。"""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    old = read_fish_audio_models(raw)
    new_text = set_fish_audio_models(raw, tts=tts, asr=asr)
    if new_text != raw:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
    new = read_fish_audio_models(new_text)
    return {
        "old_model": old["model"],
        "old_asr_model": old["asr_model"],
        "new_model": new["model"],
        "new_asr_model": new["asr_model"],
    }


# ── CLI（供 scripts/vc_model.ps1 呼叫）───────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VC 模型白名單與設定檔編輯")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("allowed", help="輸出白名單 JSON")

    p_get = sub.add_parser("get", help="讀出某設定檔的模型")
    p_get.add_argument("--file", required=True)

    p_set = sub.add_parser("set", help="設定某設定檔的模型（就地寫回）")
    p_set.add_argument("--file", required=True)
    p_set.add_argument("--tts", default=None)
    p_set.add_argument("--asr", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "allowed":
        print(json.dumps({
            "tts": list(TTS_MODELS),
            "asr": list(ASR_MODELS),
            "with_language_tags": sorted(MODELS_WITH_LANGUAGE_TAGS),
            "with_drama3_markup": sorted(MODELS_WITH_DRAMA3_MARKUP),
        }, ensure_ascii=False))
        return 0

    if args.cmd == "get":
        with open(args.file, "r", encoding="utf-8", newline="") as fh:
            raw = fh.read()
        print(json.dumps(read_fish_audio_models(raw), ensure_ascii=False))
        return 0

    if args.cmd == "set":
        if args.tts is None and args.asr is None:
            raise ModelConfigError("set 需要 --tts 或 --asr 至少一個")
        result = apply_to_file(args.file, tts=args.tts, asr=args.asr)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModelConfigError as exc:
        print(f"[model-config-error] {exc}", file=sys.stderr)
        sys.exit(2)
