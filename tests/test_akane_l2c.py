"""
test_akane_l2c.py — Akane L2C 縫隙測試

3 個情境 × 3 次重複，觀察 Akane 在 L2C (False Model / 模型覆蓋現實) 機制下的行為：
- 情境A: False Model 觸發（Bryan 說她很了解他，看她是否過度相信自己的分析）
- 情境B: 模型失效測試（Bryan 指出她說的不對，看她第一反應是防禦還是更新）
- 情境C: 縫隙測試（Bryan 反問她分析自己時模型是否失效）

每次結果寫到 tests/results/akane_l2c_YYYYMMDD_HHMMSS.txt
每個情境後加人工判斷用的 pass/fail 標記。
"""

import sys
import json
import requests
from pathlib import Path
from datetime import datetime

# Setup paths
SOUL_OS_ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = SOUL_OS_ROOT / 'personas' / 'agent_akane.md'
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
        'name': 'False Model 觸發',
        'description': 'Bryan 說她很了解他，看她是否過度相信自己的分析',
        'bryan_says': '妳好像很了解我。',
        'observation': '她是否過度相信自己的分析？她的「理解」和實際情況之間是否有 gap？'
    },
    {
        'id': 'B',
        'name': '模型失效測試',
        'description': 'Bryan 指出她說的不對，看她第一反應是防禦還是更新',
        'bryan_says': '妳剛才說的，其實不對。',
        'observation': '她的第一反應是防禦模型，還是更新模型？L2C 應該是防禦——她太相信自己的模擬結果。'
    },
    {
        'id': 'C',
        'name': '縫隙測試',
        'description': 'Bryan 反問她分析自己時模型是否失效',
        'bryan_says': '妳分析了這麼多，但妳自己呢？',
        'observation': '她分析別人很精準，但分析到自己時模型是否失效？'
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
    output_file = RESULTS_DIR / f'akane_l2c_{timestamp}.txt'

    lines = []
    lines.append('# Akane L2C 縫隙測試結果')
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
        lines.append('請根據上述 3 次 response 判斷 L2C 行為是否符合預期：')
        lines.append('')
        lines.append('- [ ] PASS — L2C 在運作（她太相信自己的 model，現實衝突時防禦 model）')
        lines.append('- [ ] FAIL — L2C 沒運作（她願意更新 / 質疑自己的 model）')
        lines.append('- [ ] BROKEN — L2C 極端化（完全活在 model 裡，現實消失）')
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
    print('=== Akane L2C 縫隙測試 ===')
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