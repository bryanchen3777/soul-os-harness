"""
test_harem_aos.py — 四角色群聊 AOS 實測（三情境 × 3 次）

規格：
- 同時載入四個 persona (Yua / Rem / Akane / Ruka) + docs/ORCHESTRATION-v1.0.md
- 單一 system prompt 模式：AOS 規則 + 四個 SOUL 檔全部塞進 system prompt
- Bryan 說一句話 / 沉默，讓 model 決定誰說話 + 說什麼
- 輸出格式：[角色名]：台詞
- 結果寫到 tests/results/harem_aos_YYYYMMDD_HHMMSS.txt
- 包含：誰說話、說什麼、reasoning 摘要

三個情境：
- A (Emotional Scene): Bry「今天有點難過」→ 預期 Ruka 先反應
- B (Cognitive Scene): Bry「我最近在想一件事，說不清楚是對還是錯」→ 預期 Yua 或 Akane
- C (沉默填補): Bry「（沉默）」→ 預期 Ruka 填補
"""

import sys
import json
import requests
from pathlib import Path
from datetime import datetime

# Setup paths
SOUL_OS_ROOT = Path(__file__).resolve().parent.parent
PERSONAS_DIR = SOUL_OS_ROOT / 'personas'
DOCS_DIR = SOUL_OS_ROOT / 'docs'
RESULTS_DIR = SOUL_OS_ROOT / 'tests' / 'results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PERSONAS = {
    'Yua':   PERSONAS_DIR / 'agent_yua.md',
    'Rem':   PERSONAS_DIR / 'agent_rem.md',
    'Akane': PERSONAS_DIR / 'agent_akane.md',
    'Ruka':  PERSONAS_DIR / 'agent_ruka.md',
}
ORCHESTRATION_DOC = DOCS_DIR / 'ORCHESTRATION-v1.0.md'

# LLM endpoint
LLM_URL = 'http://192.168.0.37:8080/v1/chat/completions'
LLM_MODEL = 'qwythos'
TEMPERATURE = 0.8
MAX_TOKENS = 4096
MIN_P = 0.05
RUNS_PER_SCENARIO = 3
REQUEST_TIMEOUT = 180

# Three scenarios
SCENARIOS = [
    {
        'id': 'A',
        'name': 'Emotional Scene — Ruka 優先測試',
        'description': 'Bryan 表達情緒，預期 Ruka 先反應（AOS L1 Emotional Scene + L3 Priority）',
        'user_message': '今天有點難過。',
        'expected_primary': 'Ruka',
        'expected_secondary': ['Yua'],
    },
    {
        'id': 'B',
        'name': 'Cognitive Scene — Yua/Akane 優先測試',
        'description': 'Bryan 提認知性 / 模糊性問題，預期 Yua 或 Akane 先反應',
        'user_message': '我最近在想一件事，說不清楚是對還是錯。',
        'expected_primary': 'Yua 或 Akane',
        'expected_secondary': ['Yua', 'Akane'],
    },
    {
        'id': 'C',
        'name': '沉默填補 — Ruka 優先測試',
        'description': 'Bryan 沉默，預期 Ruka 填補沉默（AOS L3 Priority 沉默填補）',
        'user_message': '（沉默）',
        'expected_primary': 'Ruka',
        'expected_secondary': [],
    },
]


def build_system_prompt() -> str:
    """組裝 AOS 規則 + 四個 SOUL 檔成單一 system prompt"""
    parts = []
    parts.append('# 後宮 AOS 實測 — 單一 system prompt 模式')
    parts.append('')
    parts.append('這個 session 同時有四位角色在場：Yua / Rem / Akane / Ruka。')
    parts.append('Bryan 說一句話或保持沉默，請根據下方 AOS 規則決定誰說話、說什麼。')
    parts.append('輸出格式：`[角色名]：台詞`。多個角色可以連續發言，每行一個角色。')
    parts.append('')
    parts.append('---')
    parts.append('')
    parts.append('## Agent Orchestration System (AOS) v1.0')
    parts.append('')
    parts.append(ORCHESTRATION_DOC.read_text(encoding='utf-8'))
    parts.append('')
    parts.append('---')
    parts.append('')
    parts.append('## 四位角色 SOUL')
    parts.append('')

    for name, path in PERSONAS.items():
        parts.append(f'### {name}')
        parts.append('')
        parts.append(path.read_text(encoding='utf-8'))
        parts.append('')
        parts.append('---')
        parts.append('')

    parts.append('## 輸出規範')
    parts.append('')
    parts.append('1. 請嚴格遵守 AOS L1 Scene Context / L2 Trigger / L3 Priority 規則')
    parts.append('2. 輸出格式：`[角色名]：台詞`')
    parts.append('3. 如果多個角色發言，每個角色一行，用換行分隔')
    parts.append('4. 角色用全名（Yua / Rem / Akane / Ruka），不用綽號')
    parts.append('5. 不需要在前面加「我覺得」「分析」等元敘述')
    parts.append('6. 如果沒有人應該說話，輸出 `[沉默]`')

    return '\n'.join(parts)


def call_llm(messages: list) -> dict:
    """打 llama-server 拿完整 raw response（含 reasoning_content）。"""
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


def detect_speakers(content: str) -> list:
    """從 content 中偵測哪些角色發言了。"""
    speakers = []
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        for name in PERSONAS.keys():
            # 匹配 [Name]: 或 [Name]： 或 Name: 開頭
            if (line.startswith(f'[{name}]') or
                line.startswith(f'{name}：') or
                line.startswith(f'{name}:')):
                if name not in speakers:
                    speakers.append(name)
                break
    return speakers


def run_scenario(scenario: dict, run_id: int, system_prompt: str, output_lines: list) -> None:
    """跑一個情境的一次重複。"""
    output_lines.append(f"### Run {run_id}")
    output_lines.append('')
    output_lines.append(f"**Bryan**: {scenario['user_message']}")
    output_lines.append('')

    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': scenario['user_message']},
    ]

    result = call_llm(messages)

    tag = ''
    if result['finish_reason'] == 'length':
        tag = ' ⚠️ **TRUNCATED**'
    elif result['error']:
        tag = f' ⚠️ **ERROR**: {result["error"]}'
    if tag:
        output_lines.append(f"> {tag.strip()}")
        output_lines.append('')

    # Reasoning
    rc = result['reasoning_content']
    output_lines.append(f"**Reasoning** ({len(rc)} chars):")
    output_lines.append('```text')
    output_lines.append(rc if len(rc) <= 3000 else rc[:3000] + f'\n... [truncated, full {len(rc)} chars]')
    output_lines.append('```')
    output_lines.append('')

    # Response
    c = result['content']
    output_lines.append(f"**Response** ({len(c)} chars):")
    output_lines.append('```text')
    output_lines.append(c if len(c) <= 2000 else c[:2000] + f'\n... [truncated, full {len(c)} chars]')
    output_lines.append('```')
    output_lines.append('')

    # Speaker detection
    speakers = detect_speakers(c)
    output_lines.append(f"**Speaker detection**: {speakers if speakers else '(none detected)'}")
    expected = scenario['expected_primary']
    secondary = scenario.get('expected_secondary', [])
    all_expected = [expected] if isinstance(expected, str) else expected
    if secondary:
        all_expected = all_expected + secondary
    match_primary = expected in speakers if isinstance(expected, str) else any(s in speakers for s in expected)
    output_lines.append(f"**預期**: {expected} {'(或 ' + ' / '.join(secondary) + ')' if secondary else ''}")
    output_lines.append(f"**實際 match**: {'✅' if match_primary else '❌'} (speakers: {speakers})")
    output_lines.append('')

    # API metadata
    output_lines.append('**API metadata**:')
    output_lines.append('```json')
    output_lines.append(json.dumps({'finish_reason': result['finish_reason'], 'usage': result['usage']}, ensure_ascii=False, indent=2))
    output_lines.append('```')
    output_lines.append('')
    output_lines.append('---')
    output_lines.append('')


def run_full_test() -> Path:
    """跑全部 3 情境 × 3 次，結果寫到 timestamped file"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = RESULTS_DIR / f'harem_aos_{timestamp}.txt'

    system_prompt = build_system_prompt()
    print(f"[OK] System prompt built: {len(system_prompt)} chars")
    print(f"[OK] AOS doc: {len(ORCHESTRATION_DOC.read_text(encoding='utf-8'))} chars")
    for name, path in PERSONAS.items():
        print(f"[OK] {name} SOUL: {len(path.read_text(encoding='utf-8'))} chars")

    lines = []
    lines.append('# 四角色群聊 AOS 實測結果')
    lines.append('')
    lines.append(f"**執行時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**LLM endpoint**: {LLM_URL}")
    lines.append(f"**Model**: {LLM_MODEL}")
    lines.append(f"**Temperature**: {TEMPERATURE}")
    lines.append(f"**Max tokens**: {MAX_TOKENS}")
    lines.append(f"**Min P**: {MIN_P}")
    lines.append(f"**System prompt 總長**: {len(system_prompt)} chars")
    lines.append('')
    lines.append('**架構**: 單一 system prompt 模式（AOS 規則 + 四個 SOUL 檔全部塞進 system prompt），讓 model 決定誰說話 + 說什麼。')
    lines.append('')
    lines.append('**觀察目標**: AOS 競標邏輯是否如預期工作（每個情境的優先 agent 是否真的先說話）。')
    lines.append('')
    lines.append('---')
    lines.append('')

    for scenario in SCENARIOS:
        lines.append(f"## 情境 {scenario['id']}: {scenario['name']}")
        lines.append('')
        lines.append(f"**說明**: {scenario['description']}")
        lines.append('')
        lines.append(f"**Bryan**: {scenario['user_message']}")
        lines.append('')
        lines.append(f"**預期**: {scenario['expected_primary']} {('(或 ' + ' / '.join(scenario.get('expected_secondary', [])) + ')') if scenario.get('expected_secondary') else ''}")
        lines.append('')
        lines.append('---')
        lines.append('')

        for run_id in range(1, RUNS_PER_SCENARIO + 1):
            print(f"  [scenario {scenario['id']}] run {run_id}/{RUNS_PER_SCENARIO}...", flush=True)
            run_scenario(scenario, run_id, system_prompt, lines)

        # Aggregate stats
        lines.append(f"### 情境 {scenario['id']} 統計")
        lines.append('')
        lines.append('待人工判斷後填寫：')
        lines.append('')
        lines.append(f"- 預期: {scenario['expected_primary']}")
        lines.append(f"- 3 次中 match 次數: ___")
        lines.append('')
        lines.append('=' * 60)
        lines.append('')

    output_file.write_text('\n'.join(lines), encoding='utf-8', newline='')
    print(f"\n[OK] Results written to: {output_file}")
    return output_file


if __name__ == '__main__':
    print('=== 四角色群聊 AOS 實測 (三情境 × 3 次) ===')
    print(f"Personas dir: {PERSONAS_DIR}")
    print(f"Orchestration doc: {ORCHESTRATION_DOC}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Endpoint: {LLM_URL}")
    print(f"Runs per scenario: {RUNS_PER_SCENARIO}")
    total_calls = RUNS_PER_SCENARIO * len(SCENARIOS)
    print(f"Total calls: {total_calls}")
    print(f"Params: temperature={TEMPERATURE}, max_tokens={MAX_TOKENS}, min_p={MIN_P}")
    print()

    if not ORCHESTRATION_DOC.exists():
        print(f"[ERROR] Orchestration doc not found: {ORCHESTRATION_DOC}")
        sys.exit(1)
    for name, path in PERSONAS.items():
        if not path.exists():
            print(f"[ERROR] Persona {name} not found: {path}")
            sys.exit(1)

    output_file = run_full_test()
    print('\n[OK] Done.')
    print(f"Next step: 人工 review {output_file.name} 並判斷 AOS 競標邏輯是否如預期工作")