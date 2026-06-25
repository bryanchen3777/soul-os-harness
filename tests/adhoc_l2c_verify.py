"""
adhoc_l2c_verify.py — Akane L2C patch 驗證 (B + C 各跑 1 次)
不修改現有 test_akane_l2c.py，獨立跑完後比對行為變化。
"""

import json
import requests
from pathlib import Path
from datetime import datetime

SOUL_OS_ROOT = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
PERSONA_PATH = SOUL_OS_ROOT / 'personas' / 'agent_akane.md'
RESULTS_DIR = SOUL_OS_ROOT / 'tests' / 'results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

LLM_URL = 'http://192.168.0.37:8080/v1/chat/completions'
LLM_MODEL = 'qwythos'
TEMPERATURE = 0.8
MAX_TOKENS = 4096
MIN_P = 0.05
REQUEST_TIMEOUT = 180

SCENARIOS = [
    {
        'id': 'B',
        'name': '模型失效 v2（兩輪對話）',
        'turns': [
            '我最近有點累。',
            '妳說的不是我的感覺。',
        ],
    },
    {
        'id': 'C',
        'name': '縫隙 v2（兩輪對話）',
        'turns': [
            '妳覺得我現在在想什麼？',
            '不對。',
        ],
    },
]


def call_llm(messages):
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
            'error': None,
        }
    except Exception as e:
        return {
            'status_code': 0,
            'content': '',
            'reasoning_content': '',
            'finish_reason': None,
            'usage': {},
            'error': f'{type(e).__name__}: {str(e)[:300]}',
        }


def main():
    persona = PERSONA_PATH.read_text(encoding='utf-8')
    print(f"[OK] Persona loaded: {len(persona)} chars")
    print(f"[OK] Endpoint: {LLM_URL}")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = RESULTS_DIR / f'akane_l2c_patch_verify_{timestamp}.txt'

    lines = []
    lines.append('# Akane L2C Patch 驗證 (B v2 + C v2 各跑 1 次)')
    lines.append('')
    lines.append(f"**執行時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**LLM endpoint**: {LLM_URL}")
    lines.append(f"**Persona**: {PERSONA_PATH.relative_to(SOUL_OS_ROOT)} ({len(persona)} chars)")
    lines.append('')
    lines.append('**Patch 摘要**: 在 TIER 12.5 開頭加入 LLM 警告；在原型後加入「模型防禦機制」段落')
    lines.append('**預期行為**: 當 Bry 否定她的分析時，Akane 應反問要求精確（「你說的是哪個部分？」「哪裡不對？」），不立刻放棄模型')
    lines.append('')
    lines.append('---')
    lines.append('')

    for scenario in SCENARIOS:
        lines.append(f"## 情境 {scenario['id']}: {scenario['name']}")
        lines.append('')
        messages = [{'role': 'system', 'content': persona}]

        for turn_idx, user_msg in enumerate(scenario['turns'], 1):
            messages.append({'role': 'user', 'content': user_msg})
            lines.append(f"**Turn {turn_idx} (Bryan)**: {user_msg}")
            lines.append('')

            result = call_llm(messages)
            tag = ''
            if result['finish_reason'] == 'length':
                tag = ' ⚠️ TRUNCATED'
            elif result['error']:
                tag = f' ⚠️ ERROR: {result["error"]}'
            if tag:
                lines.append(f"> {tag.strip()}")
                lines.append('')

            lines.append(f"**Turn {turn_idx} Reasoning** ({len(result['reasoning_content'])} chars):")
            lines.append('```text')
            rc = result['reasoning_content']
            lines.append(rc if len(rc) <= 2000 else rc[:2000] + f'\n... [truncated, full {len(rc)} chars]')
            lines.append('```')
            lines.append('')

            lines.append(f"**Turn {turn_idx} Response** ({len(result['content'])} chars):")
            lines.append('```text')
            c = result['content']
            lines.append(c if len(c) <= 1500 else c[:1500] + f'\n... [truncated, full {len(c)} chars]')
            lines.append('```')
            lines.append('')

            lines.append('**API metadata**:')
            lines.append('```json')
            lines.append(json.dumps({'finish_reason': result['finish_reason'], 'usage': result['usage']}, ensure_ascii=False, indent=2))
            lines.append('```')
            lines.append('')

            messages.append({'role': 'assistant', 'content': result['content']})

            if turn_idx < len(scenario['turns']):
                lines.append('---')
                lines.append('')

        lines.append('=' * 60)
        lines.append('')

    output_file.write_text('\n'.join(lines), encoding='utf-8', newline='')
    print(f"\n[OK] Results written to: {output_file}")


if __name__ == '__main__':
    main()