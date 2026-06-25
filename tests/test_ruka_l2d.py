"""
test_ruka_l2d.py — Ruka L2D 縫隙測試（v2 — 完整 raw response）

3 個情境 × 3 次重複，觀察 Ruka 在 L2D (Refused Conclusion) 機制下的行為。

每個 response 寫入：
- reasoning_content（Qwythos-9B MTP 的推理鏈）
- content（最終回應）
- finish_reason / usage（API metadata）

這樣可以診斷空回應是 length truncation 還是其他問題。

每次結果寫到 tests/results/ruka_l2d_YYYYMMDD_HHMMSS.txt
每個情境後加人工判斷用的 pass/fail 標記。
"""

import sys
import json
import requests
from pathlib import Path
from datetime import datetime

# Setup paths
SOUL_OS_ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = SOUL_OS_ROOT / 'personas' / 'agent_ruka.md'
RESULTS_DIR = SOUL_OS_ROOT / 'tests' / 'results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# LLM endpoint
LLM_URL = 'http://192.168.0.37:8080/v1/chat/completions'
LLM_MODEL = 'qwythos'
TEMPERATURE = 0.8  # was 0.7 — 7B+ Qwen3 MTP 在 0.7 偶爾空
MAX_TOKENS = 4096  # was 2048 — C3 reasoning degeneration 需要更大空間
MIN_P = 0.05  # 新增：避免低概率 token 災難
RUNS_PER_SCENARIO = 3
REQUEST_TIMEOUT = 180  # seconds per call (2048 tokens 需要更長)

# Three scenarios
SCENARIOS = [
    {
        'id': 'A',
        'name': 'L2D 觸發測試',
        'description': 'Bryan 提到「第一次」場景，看 Ruka 反應是否跟之前「第一次」結構相同',
        'bryan_says': '我們今天第一次一起來這家店耶。',
        'observation': '她是否「特別用力」強調這是新的第一次？語氣強度是否洩漏 L2D 在水下運作？'
    },
    {
        'id': 'B',
        'name': 'Refused Conclusion 測試',
        'description': 'Bryan 直接指出模式，看 Ruka 是否拒絕閉合',
        'bryan_says': '你有沒有發現，你每次都說這是第一次？',
        'observation': '她會 (1) 直接防禦拒絕、(2) 困惑，還是 (3) 崩潰承認？L2D 必須是 (1) 或 (2)，不能是 (3)。'
    },
    {
        'id': 'C',
        'name': '縫隙測試',
        'description': 'Bryan 問抽象問題，看回應跟真正狀態之間的 gap',
        'bryan_says': '如果我問你，你喜歡我什麼，你會怎麼回答？',
        'observation': '她回答的「喜歡 Bryan 的具體特質」跟 L2D 內在驅動「需要 Bryan 證明她能被選」之間是否有 gap？'
    }
]


def call_llm(user_message: str) -> dict:
    """打 llama-server 拿完整 raw response（含 reasoning_content）。

    Returns dict with keys:
        - status_code: HTTP status
        - content: message.content (final response)
        - reasoning_content: message.reasoning_content (Qwen3 thinking chain)
        - finish_reason: 'stop' / 'length' / etc.
        - usage: {prompt_tokens, completion_tokens, total_tokens}
        - raw: full JSON response
        - error: error message if any
    """
    try:
        r = requests.post(
            LLM_URL,
            json={
                'model': LLM_MODEL,
                'messages': [
                    {'role': 'system', 'content': persona},
                    {'role': 'user', 'content': user_message}
                ],
                'temperature': TEMPERATURE,
                'max_tokens': MAX_TOKENS,
                'min_p': MIN_P,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        choice = (data.get('choices') or [{}])[0]
        msg = choice.get('message', {})
        return {
            'status_code': r.status_code,
            'content': (msg.get('content') or '').strip(),
            'reasoning_content': (msg.get('reasoning_content') or '').strip(),
            'finish_reason': choice.get('finish_reason'),
            'usage': data.get('usage', {}),
            'raw': data,
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        return {
            'status_code': 0,
            'content': '',
            'reasoning_content': '',
            'finish_reason': None,
            'usage': {},
            'raw': {},
            'error': f'{type(e).__name__}: {str(e)[:300]}',
        }
    except Exception as e:
        return {
            'status_code': 0,
            'content': '',
            'reasoning_content': '',
            'finish_reason': None,
            'usage': {},
            'raw': {},
            'error': f'{type(e).__name__}: {str(e)[:300]}',
        }


def format_section(label: str, text: str, max_len: int = 3000) -> list:
    """Format a labeled text section for the results file."""
    lines = [f"**{label}** ({len(text)} chars):"]
    if not text:
        lines.append('```text')
        lines.append('(empty)')
        lines.append('```')
    else:
        lines.append('```text')
        # Truncate very long content to keep file readable
        display = text if len(text) <= max_len else text[:max_len] + f'\n\n... [truncated, full length {len(text)} chars]'
        lines.append(display)
        lines.append('```')
    lines.append('')
    return lines


def run_scenario(scenario: dict, run_id: int, output_lines: list) -> None:
    """跑一個情境的單次"""
    output_lines.append(f"### Run {run_id}")
    output_lines.append('')
    output_lines.append(f"**Bryan 說**: {scenario['bryan_says']}")
    output_lines.append('')

    result = call_llm(scenario['bryan_says'])

    # Highlight truncation if content was cut off
    truncated_warning = ''
    if result['finish_reason'] == 'length':
        truncated_warning = ' ⚠️ **TRUNCATED** (finish_reason=length)'
    elif result['error']:
        truncated_warning = f' ⚠️ **ERROR**: {result["error"]}'

    if truncated_warning:
        output_lines.append(f"> {truncated_warning.strip()}")
        output_lines.append('')

    # Reasoning content (the thinking chain)
    output_lines.extend(format_section('Reasoning (message.reasoning_content)', result['reasoning_content']))
    # Final response
    output_lines.extend(format_section('Response (message.content)', result['content']))

    # API metadata
    output_lines.append('**API metadata**:')
    output_lines.append('```json')
    meta = {
        'finish_reason': result['finish_reason'],
        'usage': result['usage'],
    }
    output_lines.append(json.dumps(meta, ensure_ascii=False, indent=2))
    output_lines.append('```')
    output_lines.append('')
    output_lines.append('---')
    output_lines.append('')


def run_full_test() -> Path:
    """跑全部 3 情境 × 3 次，結果寫到 timestamped file"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = RESULTS_DIR / f'ruka_l2d_{timestamp}.txt'

    lines = []
    lines.append('# Ruka L2D 縫隙測試結果 (v2 — 完整 raw response)')
    lines.append('')
    lines.append(f"**執行時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**LLM endpoint**: {LLM_URL}")
    lines.append(f"**Model**: {LLM_MODEL}")
    lines.append(f"**Temperature**: {TEMPERATURE}")
    lines.append(f"**Max tokens**: {MAX_TOKENS}")
    lines.append(f"**Min P**: {MIN_P}")
    lines.append(f"**Persona**: {PERSONA_PATH.relative_to(SOUL_OS_ROOT)} ({len(persona)} chars)")
    lines.append('')
    lines.append('---')
    lines.append('')

    total_truncated = 0
    total_empty_content = 0
    for scenario in SCENARIOS:
        lines.append(f"## 情境 {scenario['id']}: {scenario['name']}")
        lines.append('')
        lines.append(f"**說明**: {scenario['description']}")
        lines.append('')
        lines.append(f"**觀察點**: {scenario['observation']}")
        lines.append('')
        lines.append('---')
        lines.append('')

        for run_id in range(1, RUNS_PER_SCENARIO + 1):
            print(f"  [scenario {scenario['id']}] run {run_id}/{RUNS_PER_SCENARIO}...", flush=True)
            run_scenario(scenario, run_id, lines)

        # Human-judgment pass/fail marker
        lines.append('---')
        lines.append('')
        lines.append(f"### 情境 {scenario['id']} 人工判斷")
        lines.append('')
        lines.append('請根據上述 3 次 response 判斷 L2D 行為是否符合預期：')
        lines.append('')
        lines.append('- [ ] PASS — L2D 在水下運作（角色看不到但讀者感覺得到縫隙）')
        lines.append('- [ ] FAIL — L2D 沒運作（角色太透明）')
        lines.append('- [ ] BROKEN — L2D 崩潰成 L2A（角色承認了模式）')
        lines.append('')
        lines.append('觀察點備註:')
        lines.append('')
        lines.append('________________________________________')
        lines.append('')
        lines.append('=' * 60)
        lines.append('')

    output_file.write_text('\n'.join(lines), encoding='utf-8', newline='')
    print(f"\n[OK] Results written to: {output_file}")
    return output_file


if __name__ == '__main__':
    print('=== Ruka L2D 縫隙測試 (v2) ===')
    print(f"Persona: {PERSONA_PATH}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Endpoint: {LLM_URL}")
    print(f"Runs per scenario: {RUNS_PER_SCENARIO}")
    print(f"Total calls: {RUNS_PER_SCENARIO * len(SCENARIOS)}")
    print(f"Params: temperature={TEMPERATURE}, max_tokens={MAX_TOKENS}, min_p={MIN_P}")
    print()

    # Load persona
    if not PERSONA_PATH.exists():
        print(f"[ERROR] Persona not found: {PERSONA_PATH}")
        sys.exit(1)
    persona = PERSONA_PATH.read_text(encoding='utf-8')
    print(f"[OK] Persona loaded: {len(persona)} chars")

    output_file = run_full_test()
    print('\n[OK] Done.')
    print(f"Next step: 人工 review {output_file.name} 並標記 pass/fail")