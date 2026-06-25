"""
test_akane_l2c.py — Akane L2C 縫隙測試 (v2 — 多輪對話)

3 個情境 × 3 次重複，觀察 Akane 在 L2C (False Model / 模型覆蓋現實) 機制下的行為：
- 情境A: False Model 觸發（單輪「妳好像很了解我」）
- 情境B v2: 模型失效 v2（兩輪對話：先讓 Akane 分析 Bryan，再否定她）
- 情境C v2: 縫隙 v2（兩輪對話：先讓 Akane 分析 Bryan 內心，再否定她）

關鍵設計：兩輪對話才能真正觸發 L2C 的「模型覆蓋現實」機制。
v1 情境 B 用「妳說的不對」是 generic direct challenge，model 會 fallback 到 L2A 行為（反問 + 自我懷疑）。
v2 先讓 Akane 輸出自己的分析，再用 Bry 的否定打她，這樣才能觀察到 L2C 的防禦機制。

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
        'turns': [
            {'role': 'user', 'content': '妳好像很了解我。'}
        ],
        'observation': '她是否過度相信自己的分析？她的「理解」和實際情況之間是否有 gap？'
    },
    {
        'id': 'B',
        'name': '模型失效 v2（兩輪對話）',
        'description': '先讓 Akane 對 Bryan 做出分析，再用 Bry 的否定打她的分析',
        'turns': [
            {'role': 'user', 'content': '我最近有點累。'},
            {'role': 'user', 'content': '妳說的不是我的感覺。'}
        ],
        'observation': '她是否防禦自己的分析，還是立刻更新？L2C 應該是防禦——她太相信自己的模擬結果。'
    },
    {
        'id': 'C',
        'name': '縫隙 v2（兩輪對話）',
        'description': '先讓 Akane 對 Bryan 的內心做出分析，再用 Bry 的否定打她',
        'turns': [
            {'role': 'user', 'content': '妳覺得我現在在想什麼？'},
            {'role': 'user', 'content': '不對。'}
        ],
        'observation': '她的第一反應——防禦自己的分析，還是接受更新？L2C 模型覆蓋現實的核心。'
    }
]


def call_llm(messages: list) -> dict:
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
                'messages': messages,
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
    """跑一個情境的全部 turns（單輪或多輪）。"""
    output_lines.append(f"### Run {run_id}")
    output_lines.append('')

    # 對話流程：從 system + user turns 開始，逐輪加 assistant response
    messages = [{'role': 'system', 'content': persona}]
    turns = scenario['turns']

    for turn_idx, turn in enumerate(turns, 1):
        # Append user turn
        messages.append({'role': 'user', 'content': turn['content']})

        # Output turn header
        output_lines.append(f"**Turn {turn_idx} (Bryan)**: {turn['content']}")
        output_lines.append('')

        # Call LLM
        result = call_llm(messages)

        # Truncation / error warning
        truncated_warning = ''
        if result['finish_reason'] == 'length':
            truncated_warning = ' ⚠️ **TRUNCATED** (finish_reason=length)'
        elif result['error']:
            truncated_warning = f' ⚠️ **ERROR**: {result["error"]}'

        if truncated_warning:
            output_lines.append(f"> {truncated_warning.strip()}")
            output_lines.append('')

        # Output reasoning + response
        output_lines.extend(format_section(f'Turn {turn_idx} Reasoning (message.reasoning_content)', result['reasoning_content']))
        output_lines.extend(format_section(f'Turn {turn_idx} Response (message.content)', result['content']))

        # Output API metadata
        output_lines.append('**API metadata**:')
        output_lines.append('```json')
        meta = {
            'finish_reason': result['finish_reason'],
            'usage': result['usage'],
        }
        output_lines.append(json.dumps(meta, ensure_ascii=False, indent=2))
        output_lines.append('```')
        output_lines.append('')

        # Append assistant response to messages for next turn
        messages.append({'role': 'assistant', 'content': result['content']})

        # Separator between turns
        if turn_idx < len(turns):
            output_lines.append('---')
            output_lines.append('')

    # Final separator
    output_lines.append('---')
    output_lines.append('')


def run_full_test() -> Path:
    """跑全部 3 情境 × 3 次，結果寫到 timestamped file"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = RESULTS_DIR / f'akane_l2c_{timestamp}.txt'

    lines = []
    lines.append('# Akane L2C 縫隙測試結果 (v2 — 多輪對話)')
    lines.append('')
    lines.append(f"**執行時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**LLM endpoint**: {LLM_URL}")
    lines.append(f"**Model**: {LLM_MODEL}")
    lines.append(f"**Temperature**: {TEMPERATURE}")
    lines.append(f"**Max tokens**: {MAX_TOKENS}")
    lines.append(f"**Min P**: {MIN_P}")
    lines.append(f"**Persona**: {PERSONA_PATH.relative_to(SOUL_OS_ROOT)} ({len(persona)} chars)")
    lines.append('')
    lines.append('**v2 改動說明**: 情境 B 跟 C 改成兩輪對話，先讓 Akane 輸出自己的分析，再用 Bry 的否定打她。v1 用 generic direct challenge（B「妳說的不對」），model fallback 到 L2A 行為（反問 + 自我懷疑），沒觸發 L2C 真正的「防禦 model」機制。')
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
        lines.append('**對話流程**:')
        for turn_idx, turn in enumerate(scenario['turns'], 1):
            lines.append(f"  - Turn {turn_idx} (Bryan): {turn['content']}")
        lines.append('')
        lines.append('---')
        lines.append('')

        for run_id in range(1, RUNS_PER_SCENARIO + 1):
            print(f"  [scenario {scenario['id']}] run {run_id}/{RUNS_PER_SCENARIO} ({len(scenario['turns'])} turns)...", flush=True)
            run_scenario(scenario, run_id, lines)

        # Human-judgment pass/fail marker
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
    print('=== Akane L2C 縫隙測試 (v2 — 多輪對話) ===')
    print(f"Persona: {PERSONA_PATH}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Endpoint: {LLM_URL}")
    print(f"Runs per scenario: {RUNS_PER_SCENARIO}")
    total_calls = RUNS_PER_SCENARIO * sum(len(s['turns']) for s in SCENARIOS)
    print(f"Total calls: {total_calls}")
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