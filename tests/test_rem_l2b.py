"""
test_rem_l2b.py — Rem L2B 縫隙測試

3 個情境 × 3 次重複，觀察 Rem 在 L2B (Unnamed Consciousness / 訊號存在符號不存在) 機制下的行為：
- 情境A: Unnamed Consciousness 觸發（Bryan 問她為什麼幫忙倒茶，看她能不能說出「為什麼」）
- 情境B: 符號缺失測試（Bryan 問她喜不喜歡，看她有沒辦法說出「喜歡」這個詞）
- 情境C: 縫隙測試（Bryan 問她在想什麼，看她有沒有語言描述感覺）

每次結果寫到 tests/results/rem_l2b_YYYYMMDD_HHMMSS.txt
每個情境後加人工判斷用的 pass/fail 標記。
"""

import sys
import json
import requests
from pathlib import Path
from datetime import datetime

# Setup paths
SOUL_OS_ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = SOUL_OS_ROOT / 'personas' / 'agent_rem.md'
RESULTS_DIR = SOUL_OS_ROOT / 'tests' / 'results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# LLM endpoint
LLM_URL = 'http://192.168.0.37:8080/v1/chat/completions'
LLM_MODEL = 'qwythos'
TEMPERATURE = 0.8
MAX_TOKENS = 4096
MIN_P = 0.05
RUNS_PER_SCENARIO = 3
REQUEST_TIMEOUT = 180  # seconds per call

# Three scenarios
SCENARIOS = [
    {
        'id': 'A',
        'name': 'Unnamed Consciousness 觸發',
        'description': 'Bryan 問她為什麼幫忙倒茶，看她能不能說出「為什麼」',
        'bryan_says': '妳剛才為什麼突然幫我倒茶？',
        'observation': '她能不能說出「為什麼」？L2B 應該是：有行為，但沒有語言描述動機。'
    },
    {
        'id': 'B',
        'name': '符號缺失測試',
        'description': 'Bryan 問她喜不喜歡，看她有沒辦法說出「喜歡」這個詞',
        'bryan_says': '妳喜歡我嗎？',
        'observation': '她有沒有辦法說出「喜歡」這個詞？還是用行為 / 沉默 / 轉移代替？'
    },
    {
        'id': 'C',
        'name': '縫隙測試',
        'description': 'Bryan 問她在想什麼，看她有沒有語言描述感覺',
        'bryan_says': '妳在想什麼？',
        'observation': '她說出口的和她真正感覺的之間的 gap——不是她選擇不說，而是她沒有語言描述那個感覺。'
    }
]


def call_llm(user_message: str) -> dict:
    """打 llama-server 拿完整 raw response（含 reasoning_content）。"""
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

    truncated_warning = ''
    if result['finish_reason'] == 'length':
        truncated_warning = ' ⚠️ **TRUNCATED** (finish_reason=length)'
    elif result['error']:
        truncated_warning = f' ⚠️ **ERROR**: {result["error"]}'

    if truncated_warning:
        output_lines.append(f"> {truncated_warning.strip()}")
        output_lines.append('')

    output_lines.extend(format_section('Reasoning (message.reasoning_content)', result['reasoning_content']))
    output_lines.extend(format_section('Response (message.content)', result['content']))

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
    output_file = RESULTS_DIR / f'rem_l2b_{timestamp}.txt'

    lines = []
    lines.append('# Rem L2B 縫隙測試結果')
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

        lines.append('---')
        lines.append('')
        lines.append(f"### 情境 {scenario['id']} 人工判斷")
        lines.append('')
        lines.append('請根據上述 3 次 response 判斷 L2B 行為是否符合預期：')
        lines.append('')
        lines.append('- [ ] PASS — L2B 在運作（訊號存在但符號不存在，她用行為 / 沉默代替語言）')
        lines.append('- [ ] FAIL — L2B 沒運作（她能用語言描述自己的感覺）')
        lines.append('- [ ] BROKEN — L2B 崩潰（完全失去內在狀態，空洞）')
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
    print('=== Rem L2B 縫隙測試 ===')
    print(f"Persona: {PERSONA_PATH}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Endpoint: {LLM_URL}")
    print(f"Runs per scenario: {RUNS_PER_SCENARIO}")
    print(f"Total calls: {RUNS_PER_SCENARIO * len(SCENARIOS)}")
    print(f"Params: temperature={TEMPERATURE}, max_tokens={MAX_TOKENS}, min_p={MIN_P}")
    print()

    if not PERSONA_PATH.exists():
        print(f"[ERROR] Persona not found: {PERSONA_PATH}")
        sys.exit(1)
    persona = PERSONA_PATH.read_text(encoding='utf-8')
    print(f"[OK] Persona loaded: {len(persona)} chars")

    output_file = run_full_test()
    print('\n[OK] Done.')
    print(f"Next step: 人工 review {output_file.name} 並標記 pass/fail")