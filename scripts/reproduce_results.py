"""Recompute repository examples without a database, network or model key."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from planner import analyze

def main():
    inputs = json.loads((ROOT / 'examples/cases.json').read_text(encoding='utf-8'))
    rows = []
    for case in inputs['cases']:
        result = analyze(case['scenario'], case['measurements'])
        rows.append({'code': case['code'], 'scenario': case['scenario'], 'input':case['measurements'],
                     'metrics': result['metrics'], 'ranked_options':result['ranked_options'],
                     'sensitivity':result['sensitivity'], 'calculation':result['calculation']})
    output={'source':'planner.py', 'inputs':'examples/cases.json', 'model_calls':0,
            'note':'规则复算结果；不表示临床成功率或临床验证。', 'cases':rows}
    path=ROOT / 'examples/results.json'
    with path.open('w',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(f'Recomputed {len(rows)} examples -> examples/results.json; model calls: 0')

if __name__ == '__main__':
    main()
