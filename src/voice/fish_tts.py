"""
Fish Audio TTS (s2-pro / s2.1-pro-free)
========================================
Single-file Python CLI wrapping https://api.fish.audio/v1/tts.

Reference docs:
  - Endpoint: POST https://api.fish.audio/v1/tts
  - Headers:  Authorization: Bearer <FISH_API_KEY>
              Content-Type: application/json
              model: <model_name>
  - Body:     {text, reference_id, format, ...}

Setup:
  Put FISH_API_KEY into C:\\Users\\bbfcc\\Downloads\\voice\\.env as
  `FISH_API_KEY=<your_key>` (or `= $<FISH_API_KEY>` if you copy-paste from
  shell scripts). Lines starting with `#` or blank are ignored.

Voice IDs (Fish "reference_id"):
  These are opaque 32-char hex strings that identify a cloned voice on the
  Fish side. Aliases live in the VOICES dict below — change the right-hand
  side to swap in a different cloned voice, and use the left-hand key on the
  CLI (e.g. `--voice-id mahiru_voice`).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


API_URL = "https://api.fish.audio/v1/tts"

# Friendly CLI name -> Fish reference_id.
# Update the right-hand side to use your own cloned voice. To add more, copy
# a line and put in a new key + Fish ID.
VOICES: dict[str, str] = {
    "rem_voice":   "aebe94aa8d634c19b2a08ba9836a7af4",  # Re:Zero — レム (default)
    "rem_zh_voice": "1325f2c9fdb4406db10dea59a85dde3f",  # 雷姆 中文專用
    "mahiru_voice": "b3d773aaa2f44e128e8ae21321e64b45",  # 椎名まひる
    "yua_voice":   "5ae50aa681784e7e8913f681117a1b7e",  # ユア
    "akane_voice": "4c11d21b14284d428074f76a1cf32298",  # 黒川茜 (推しの子)
    "ruka_voice":  "6a45afe8e5f84217acdfc8103156e0cc",  # 更科瑠夏
    "mai_voice":   "101fe58fb9914eefa510f3c92ec1d798",  # 櫻島麻衣 (青春豬頭) — Bry 自製
    "ram_voice":   "3559bc2b12d24220b107378130419890",  # ラム (Re:Zero 雙胞胎姊姊)
    "miku_voice":  "d8d514a56bca4a7a8af3c27232cc39aa",  # 中野三玖 (五等分) — Fish Audio default
    "anna_voice":  "a3eda99cf8f8425bac220499766ffbdd",  # 山田杏奈
    "hinami_voice": "b60ceb0336e24f8a9b6e785db1f01488",  # 日南葵 (弱キャラ友崎くん)
}

DEFAULT_MODEL = "s2.1-pro-free"  # free tier; switch to "s2-pro" for higher quality (paid)


def load_api_key(env_path: Path = Path(__file__).parent / ".env") -> str:
    """
    Read FISH_API_KEY from a .env file in the script directory, falling back
    to the process environment.

    Tolerates a leading `$` in either the key name (some folks paste shell
    variable references into .env files) or the value (shell-style refs that
    weren't actually expanded).
    """
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().lstrip("$")  # strip leading $ from key name
            v = v.strip()
            if k == "FISH_API_KEY" and v and not v.startswith("$"):
                return v
    key = os.environ.get("FISH_API_KEY", "").strip().lstrip("$")
    if not key:
        sys.exit("FISH_API_KEY not found. Set it in "
                 f"{env_path} or as an env var.")
    return key


def resolve_voice_id(voice: str) -> str:
    """Map a friendly alias (from VOICES) to a real Fish reference_id.
    If `voice` looks like a 32-char hex hash already, pass it through."""
    if voice in VOICES:
        return VOICES[voice]
    if len(voice) == 32 and all(c in "0123456789abcdef" for c in voice.lower()):
        return voice  # raw Fish reference_id
    sys.exit(f"Unknown voice '{voice}'. Either add it to VOICES in "
             f"{Path(__file__).name} or pass a 32-char hex reference_id.")


def synthesize(*, text: str, voice_id: str, api_key: str,
               out_path: Path, model: str = DEFAULT_MODEL,
               format: str = "mp3", timeout: int = 180) -> Path:
    """POST /v1/tts → write audio to out_path. Raises RuntimeError on HTTP error.

    Lesson 42 (2026-07-31 Bry 拍板): 從 sys.exit 改成 raise RuntimeError。
    原因:sys.exit 會直接殺掉整個 process,被 async code 誤呼叫會把整個 server 砍掉
    (正是 Lesson 38 fire-and-forget task 吞異常那個模式的反面 — 沒有 try/except 時
    sys.exit 是最糟糕的失敗模式)。改 raise 之後,async caller 必須自己 try/except 兜底
    (fish_tts_handler.py 已經有 except SystemExit 跟 except Exception 雙重保護,
    把 raise 改成 RuntimeError 後仍然接得住)。

    CLI 入口 (if __name__ == "__main__"): 故意不安裝 try/except,
    CLI 環境下未捕捉例外 + 非零 exit code 是正常行為。
    """
    import requests  # lazy import so --help works without requests installed

    r = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": model,
        },
        json={"text": text, "reference_id": voice_id, "format": format},
        timeout=timeout,
    )
    if r.status_code != 200:
        # Lesson 42: raise 而不是 sys.exit
        raise RuntimeError(
            f"fish TTS failed (HTTP {r.status_code}): {r.text[:400]}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)
    print(f"  ✓ wrote {out_path} ({len(r.content):,} bytes, "
          f"{r.headers.get('content-type')}, model={model})")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Fish Audio TTS (clone voices)")
    ap.add_argument("--text", default=None,
                    help="text to synthesize (or use --text-file)")
    ap.add_argument("--text-file", default=None,
                    help="read text from this file (UTF-8)")
    ap.add_argument("--voice-id", default="rem_voice",
                    help="alias from VOICES dict, or a raw 32-char "
                         "Fish reference_id (default: rem_voice)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    choices=["s2.1-pro-free", "s2-pro"],
                    help="Fish model (default: s2.1-pro-free)")
    ap.add_argument("--out", default="out.mp3",
                    help="output mp3 path (default: out.mp3)")
    ap.add_argument("--format", default="mp3", choices=["mp3", "wav", "pcm"])
    args = ap.parse_args()

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text
    else:
        sys.exit("--text or --text-file required")

    api_key = load_api_key()
    voice_id = resolve_voice_id(args.voice_id)

    print(f"synthesizing {len(text)} chars "
          f"[voice={args.voice_id}→{voice_id[:8]}…, model={args.model}] …")
    synthesize(text=text, voice_id=voice_id, api_key=api_key,
               out_path=Path(args.out), model=args.model, format=args.format)


if __name__ == "__main__":
    main()