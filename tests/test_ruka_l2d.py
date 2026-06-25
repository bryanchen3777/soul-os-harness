"""
test_ruka_l2d.py — Ruka L2D 縫隙測試

3 個情境 × 3 次重複，觀察 Ruka 在 L2D (Refused Conclusion) 機制下的行為：
- 情境A: L2D 觸發（Bryan 提「第一次」場景，看 Ruka 反應是否跟之前「第一次」結構相同）
- 情境B: Refused Conclusion（Bryan 直接指出模式，看 Ruka 是否拒絕閉合）
- 情境C: 縫隙測試（Bryan 問抽象問題，看回應跟真正狀態之間的 gap）

每次 response 寫到 tests/results/ruka_l2d_YYYYMMDD_HHMMSS.txt
每個情境後加人工判斷用的 pass/fail 標記（不是自動評分）。
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
TEMPERATURE = 0.7
MAX_TOKENS = 512
RUNS_PER_SCENARIO = 3
REQUEST_TIMEOUT = 120  # seconds per call

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


def call_llm(user_message: str) -> str:
    """打 llama-server 拿 response。失敗時回傳 [ERROR: ...] 字串而非 raise。"""
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
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if 'choices' in data and data['choices']:
            return data['choices'][0]['message']['content']
        return f'[ERROR: no choices in response: {json.dumps(data, ensure_ascii=False)[:300]}]'
    except requests.exceptions.RequestException as e:
        return f'[ERROR: {type(e).__name__}: {str(e)[:300]}]'
    except Exception as e:
        return f'[ERROR: {type(e).__name__}: {str(e)[:300]}]'


def run_scenario(scenario: dict, run_id: int, output_lines: list) -> None:
    """跑一個情境的單次"""
    output_lines.append(f"### Run {run_id}")
    output_lines.append('')
    output_lines.append(f"**Bryan 說**: {scenario['bryan_says']}")
    output_lines.append('')
    output_lines.append("**Ruka 回應**:")
    output_lines.append('')
    content = call_llm(scenario['bryan_says'])
    output_lines.append(content)
    output_lines.append('')
    output_lines.append('---')
    output_lines.append('')


def run_full_test() -> Path:
    """跑全部 3 情境 × 3 次，結果寫到 timestamped file"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = RESULTS_DIR / f'ruka_l2d_{timestamp}.txt'

    lines = []
    lines.append('# Ruka L2D 縫隙測試結果')
    lines.append('')
    lines.append(f"**執行時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**LLM endpoint**: {LLM_URL}")
    lines.append(f"**Model**: {LLM_MODEL}")
    lines.append(f"**Temperature**: {TEMPERATURE}")
    lines.append(f"**Max tokens**: {MAX_TOKENS}")
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
    print('=== Ruka L2D 縫隙測試 ===')
    print(f"Persona: {PERSONA_PATH}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Endpoint: {LLM_URL}")
    print(f"Runs per scenario: {RUNS_PER_SCENARIO}")
    print(f"Total calls: {RUNS_PER_SCENARIO * len(SCENARIOS)}")
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