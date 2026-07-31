# MiniMax Voice Clone + TTS

Single-file Python CLI for cloning a voice via MiniMax's `/v1/voice_clone` API
and synthesizing speech with `/v1/t2a_v2`. Uses `requests` only (no other deps).

## Setup

API key is read automatically from:
`%LOCALAPPDATA%\hermes\.env` (key name: `MINIMAX_API_KEY`)

or from the `MINIMAX_API_KEY` environment variable.

Get a key at <https://platform.minimax.io/user-center/basic-information/interface-key>.

## Naming

The voice used in this project is named **`mahiruV001`** (from `iwami_9.wav`).
The `V001` suffix is required because `mahiru` alone is 6 chars and the API
requires `voice_id` length 8–256. A higher-fidelity variant using an SRT-cut
prompt is named **`mahiruPrompt01`**. Both are valid for 7 days after clone.

## Quick start

```bash
# 1. Clone (whole wav, no prompt)
python tts_clone.py clone --audio iwami_9.wav --voice-id mahiruV001

# 2. Synthesize (Chinese)
python tts_clone.py tts --voice-id mahiruV001 ^
    --text "你好,真晝來報到。" --out hello.mp3 --language Chinese

# 3. Clone + synthesize in one shot
python tts_clone.py all --audio iwami_9.wav --voice-id mahiruV001 ^
    --text "你好" --out hello.mp3 --language Chinese
```

## High-fidelity clone with prompt (Japanese recommended)

Upload a `<8s` reference clip together with its transcript for stronger
voice similarity. The cleanest path is to provide an SRT file matching
the source audio — the tool will auto-trim the wav to the chosen cue.

```bash
# 1. Clone (with prompt)
python tts_clone.py clone --audio iwami_9.wav --voice-id mahiruPrompt01 ^
    --with-prompt --srt iwami_9.ja.srt --prompt-index 1

# 2. Synthesize (Japanese)
python tts_clone.py tts --voice-id mahiruPrompt01 ^
    --text "こんにちは、世界のみなさん。" --out hello_jp.mp3 --language Japanese
```

If you don't have an SRT, you can supply the prompt text manually and the
tool will trim the first 7.9s of the wav:

```bash
python tts_clone.py clone --audio iwami_9.wav --voice-id myvoice ^
    --with-prompt --prompt-text "前 7.9 秒的逐字稿,以句號結尾。"
```

**SRT requirements:**
- UTF-8 encoded, time format `HH:MM:SS,mmm --> HH:MM:SS,mmm`
- The chosen cue must end within 8 seconds (cues longer than 8s are
  truncated to 7.9s with a warning)
- Text must end with punctuation (`。` is added automatically if missing)

## Auto-recovery on expired voice_id

Cloned voices are deleted by the API after 7 days of inactivity. Add
`--auto-reclone` to a `tts` call so that if the first TTS attempt fails
the tool re-clones the same `voice_id` and retries:

```bash
# Simple recovery
python tts_clone.py tts --voice-id mahiruV001 ^
    --text "你好" --out hello.mp3 --language Chinese ^
    --auto-reclone --audio iwami_9.wav

# With prompt recovery (must match the original clone params)
python tts_clone.py tts --voice-id mahiruPrompt01 ^
    --text "こんにちは" --out hello_jp.mp3 --language Japanese ^
    --auto-reclone --audio iwami_9.wav ^
    --with-prompt --srt iwami_9.ja.srt --prompt-index 1
```

Recovery flow:
1. TTS → if it succeeds, write output and stop.
2. TTS fails → clone the voice again using `--audio` (+ prompt if given).
3. If clone succeeds → retry TTS with the same `voice_id`.
4. If clone returns "already exists" → just retry TTS (the voice wasn't
   really gone).

## Models and languages

| Param | Default | Options |
|---|---|---|
| `--model` | `speech-2.8-hd` | `speech-2.8-hd`, `speech-2.8-turbo`, `speech-2.6-*`, `speech-02-*`, `speech-01-*` |
| `--language` | `auto` | `Chinese`, `Chinese,Yue`, `English`, `Japanese`, `Korean`, `auto`, ... |

`hd` and `turbo` cost the same (priced per `usage_characters`). Turbo is
~10% slower in speech rate but ~3–4× faster to return.

## All CLI options

```bash
python tts_clone.py clone --help
python tts_clone.py tts   --help
python tts_clone.py all   --help
```

Common knobs:
- `--speed` (0.5–2.0, default 1.0)
- `--pitch` (-12 to 12, default 0)
- `--vol` (volume, default 1.0)
- `--format` (`mp3` / `pcm` / `flac`)
- `--sample-rate` (default 32000)
- `--bitrate` (default 128000)
- `--text-file path/to/script.txt` for long inputs

## Source wav requirements

For `/v1/files/upload` (`purpose=voice_clone`):
- Formats: `mp3`, `m4a`, `wav`
- Duration: 10s to 5 min
- Size: ≤ 20 MB

For `purpose=prompt_audio`: same formats, **< 8 seconds**, ≤ 20 MB.

## Pricing

Each TTS call charges `usage_characters` — see the response `extra_info`
field (printed after each successful synthesis). Chinese chars, ASCII
letters, and digits all count; punctuation does not.

## File map

```
voice/
├── README.md          this file
├── tts_clone.py      the CLI (single file, ~26KB)
├── iwami_9.wav       source audio (the voice sample)
├── iwami_9.ja.srt    Japanese transcript with timestamps
└── out_*.mp3         test outputs from earlier sessions
```