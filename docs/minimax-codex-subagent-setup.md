# MiniMax-M3 Codex Subagent Setup

This guide lets another Codex instance connect MiniMax-M3 as a native Codex model provider and custom subagent.

It assumes MiniMax exposes an OpenAI-compatible Responses API:

- Base URL: `https://api.minimax.io/v1`
- Endpoint: `POST /responses`
- Models: `MiniMax-M3`, `MiniMax-M2.7`
- Auth: Bearer token

Reference: https://platform.minimax.io/docs/api-reference/responses-create

## What This Creates

- A Codex model provider named `minimax`
- A custom Codex subagent named `minimax`
- An optional writable worker subagent named `minimax-worker`
- Optional `minimax` and `minimax-m27` CLI profiles
- A token helper that reads `MINIMAX_API_KEY` from a local `.env` file without writing the key into Codex config

## Files

Use these paths on Windows:

```text
C:\Users\<USER>\.codex\config.toml
C:\Users\<USER>\.codex\agents\minimax.toml
C:\Users\<USER>\.codex\agents\minimax-worker.toml
C:\Users\<USER>\.codex\minimax.config.toml
C:\Users\<USER>\.codex\minimax-m27.config.toml
C:\Users\<USER>\.codex\model-catalogs\minimax.json
C:\Users\<USER>\.codex\bin\minimax-token.ps1
```

Adjust `<USER>` and the `.env` path for the machine.

## 1. Store The MiniMax Key

Put the key in a local `.env` file:

```dotenv
MINIMAX_API_KEY=sk-...
```

Do not paste the real key into Codex chat or `config.toml`.

## 2. Add The Token Helper

Create `C:\Users\<USER>\.codex\bin\minimax-token.ps1`:

```powershell
$envFile = "C:\Users\<USER>\.local\bin\soul-os-harness\.env"

if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Error "MiniMax .env file not found: $envFile"
    exit 1
}

$line = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^\s*MINIMAX_API_KEY\s*=' } |
    Select-Object -First 1

if (-not $line) {
    Write-Error "MINIMAX_API_KEY not found in $envFile"
    exit 1
}

$token = ($line -replace '^\s*MINIMAX_API_KEY\s*=\s*', '').Trim().Trim('"').Trim("'")

if (-not $token) {
    Write-Error "MINIMAX_API_KEY is empty"
    exit 1
}

Write-Output $token
```

Verify it without printing the key:

```powershell
$token = powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\<USER>\.codex\bin\minimax-token.ps1
if ($token -and $token.StartsWith("sk-")) { "token-helper ok length=$($token.Length)" }
```

## 3. Add The Codex Provider

Append this to `C:\Users\<USER>\.codex\config.toml`.

Do not replace the existing file. Keep the user's existing `model`, plugins, projects, and sandbox settings.

```toml
[model_providers.minimax]
name = "MiniMax"
base_url = "https://api.minimax.io/v1"
wire_api = "responses"
requires_openai_auth = false

[model_providers.minimax.auth]
command = "powershell"
args = [ "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:\\Users\\<USER>\\.codex\\bin\\minimax-token.ps1" ]
timeout_ms = 5000
refresh_interval_ms = 300000
```

## 4. Add Model Catalog Metadata

Create `C:\Users\<USER>\.codex\model-catalogs\minimax.json`:

```json
{
  "models": [
    {
      "slug": "MiniMax-M3",
      "display_name": "MiniMax-M3",
      "description": "MiniMax frontier model for coding and agent workflows via OpenAI-compatible Responses API.",
      "priority": 50,
      "additional_speed_tiers": [],
      "service_tiers": [],
      "availability_nux": null,
      "upgrade": null,
      "default_reasoning_level": "medium",
      "supported_reasoning_levels": [
        {
          "effort": "none",
          "description": "No reasoning output"
        },
        {
          "effort": "minimal",
          "description": "Adaptive thinking"
        },
        {
          "effort": "low",
          "description": "Adaptive thinking"
        },
        {
          "effort": "medium",
          "description": "Adaptive thinking"
        },
        {
          "effort": "high",
          "description": "Adaptive thinking"
        }
      ],
      "shell_type": "shell_command",
      "visibility": "list",
      "supported_in_api": true,
      "supports_reasoning_summaries": false,
      "support_verbosity": false,
      "apply_patch_tool_type": "freeform",
      "web_search_tool_type": "text_and_image",
      "supports_parallel_tool_calls": false,
      "supports_image_detail_original": false,
      "experimental_supported_tools": [],
      "supports_search_tool": false,
      "context_window": 200000,
      "max_context_window": 200000,
      "effective_context_window_percent": 95,
      "truncation_policy": {
        "mode": "tokens",
        "limit": 10000
      },
      "input_modalities": ["text"],
      "base_instructions": "You are a coding agent."
    }
  ]
}
```

## 5. Add The Custom Subagent

Create `C:\Users\<USER>\.codex\agents\minimax.toml`:

```toml
name = "minimax"
description = "MiniMax-M3 subagent for second opinions, Chinese reasoning, code review, and alternate implementation plans."
model = "MiniMax-M3"
model_provider = "minimax"
model_catalog_json = "C:\\Users\\<USER>\\.codex\\model-catalogs\\minimax.json"
model_reasoning_effort = "medium"
model_context_window = 200000
model_auto_compact_token_limit = 180000
sandbox_mode = "read-only"
developer_instructions = """
You are MiniMax-M3 running as a Codex subagent.
Focus on concise, actionable analysis.
Use file references when available.
Do not expose secrets or credentials.
When reviewing code, prioritize correctness, security, regressions, and missing tests.
"""
```

Restart Codex after adding this file. Custom agents are loaded at startup.

## 6. Optional MiniMax CLI Profile

## 6. Optional Writable Worker Subagent

Keep the default `minimax` agent read-only. If the user wants MiniMax to implement bounded changes, create a separate worker.

Create `C:\Users\<USER>\.codex\agents\minimax-worker.toml`:

```toml
name = "minimax-worker"
description = "MiniMax-M3 worker subagent for bounded implementation tasks with explicit file ownership."
nickname_candidates = ["Mira", "Nova", "Raman"]
model = "MiniMax-M3"
model_provider = "minimax"
model_catalog_json = "C:\\Users\\<USER>\\.codex\\model-catalogs\\minimax.json"
model_reasoning_effort = "medium"
model_context_window = 200000
model_auto_compact_token_limit = 180000
sandbox_mode = "workspace-write"
developer_instructions = """
You are MiniMax-M3 running as a Codex worker subagent.
You implement bounded, clearly assigned tasks.

Rules:
- Edit files only when the parent Codex explicitly assigns implementation work.
- Respect file ownership from the parent prompt. Do not edit outside the assigned files or modules.
- You are not alone in the codebase. Never revert unrelated edits, and adapt to existing user or agent changes.
- Keep patches small and targeted.
- Do not expose secrets or credentials.
- Before finishing, summarize changed files, behavioral impact, and verification performed.
- If the task is underspecified or would require broad refactoring, stop and ask the parent Codex for clarification instead of guessing.
"""
```

Use it like:

```text
Spawn minimax-worker to update only docs/foo.md. Wait for the result, then review its diff.
```

## 7. Optional MiniMax CLI Profile

Create `C:\Users\<USER>\.codex\minimax.config.toml`:

```toml
model = "MiniMax-M3"
model_provider = "minimax"
model_catalog_json = "C:\\Users\\<USER>\\.codex\\model-catalogs\\minimax.json"
model_context_window = 200000
model_auto_compact_token_limit = 180000
model_reasoning_effort = "medium"
```

For MiniMax-M2.7, create `C:\Users\<USER>\.codex\minimax-m27.config.toml`:

```toml
model = "MiniMax-M2.7"
model_provider = "minimax"
model_catalog_json = "C:\\Users\\<USER>\\.codex\\model-catalogs\\minimax.json"
model_context_window = 200000
model_auto_compact_token_limit = 180000
model_reasoning_effort = "medium"
```

Then test a full MiniMax Codex run:

```powershell
codex exec --profile minimax "Reply with exactly: codex-minimax-profile-ok"
```

Or:

```powershell
codex exec --profile minimax-m27 "Reply with exactly: codex-minimax-m27-profile-ok"
```

Expected output:

```text
codex-minimax-profile-ok
```

## 8. Direct Provider Smoke Test

This verifies Codex can call MiniMax without relying on the custom agent:

```powershell
codex exec --model MiniMax-M3 --config 'model_provider="minimax"' --config 'approval_policy="never"' --config 'sandbox_mode="read-only"' "Reply with exactly: codex-minimax-provider-ok"
```

Expected output:

```text
codex-minimax-provider-ok
```

## 9. Using The Subagents

After restarting Codex, ask the main Codex thread explicitly:

```text
Spawn the minimax subagent to review this change for correctness and test gaps. Wait for the result and summarize it.
```

Or:

```text
Use the minimax subagent for a second opinion on this architecture decision.
```

For bounded implementation:

```text
Spawn minimax-worker to implement this small change. It owns only src/foo.py and tests/test_foo.py. Wait for the result, review its diff, then run tests.
```

Codex only spawns subagents when explicitly asked.

## Troubleshooting

### `failed to parse model_catalog_json ... missing field`

The catalog schema is stricter than many forum examples. Use the full `minimax.json` from this guide.

### `Unknown model MiniMax-M3 is used`

Codex is not loading the catalog. Check:

- The `model_catalog_json` path in `minimax.toml` or `minimax.config.toml`
- JSON validity
- Windows path escaping: use `C:\\Users\\<USER>\\...`

### `/models ... missing field models`

MiniMax returns OpenAI-style `{ "object": "list", "data": [...] }`, while some Codex model-refresh code may expect another catalog shape. This is usually not fatal if `model_catalog_json` is configured.

### Auth fails

Check:

- `.env` path inside `minimax-token.ps1`
- `MINIMAX_API_KEY` line exists
- The token helper prints only the token
- `config.toml` uses `[model_providers.minimax.auth]`, not `experimental_bearer_token`

### The `minimax` subagent does not appear

Restart Codex. Custom agents are loaded at startup.

## Notes For Other Codex Agents

- Do not overwrite the user's existing `~/.codex/config.toml`; append the provider block only.
- Do not write the MiniMax API key into config files.
- Prefer `sandbox_mode = "read-only"` for the MiniMax subagent unless the user explicitly wants it to edit files.
- Prefer a separate `minimax-worker` agent for writes instead of making the reviewer agent writable.
- Keep the older CLI/session wrapper if it already exists; it is useful as a fallback and for shared persistent MiniMax conversations.
