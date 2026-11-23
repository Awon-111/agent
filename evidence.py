"""Offline, auditable lexical retrieval over public case reports (no model training)."""
from __future__ import annotations

import hashlib
import copy
import json
import math
import re
from collections import Counter
from pathlib import Path
from functools import lru_cache

CORPUS = Path(__file__).resolve().parent / 'data' / 'evidence' / 'cases.jsonl'
TOPICS = {
    'spine': ['scoliosis', 'spinal deformity', 'Cobb', 'fusion'],
    'joint': ['arthroplasty', 'hip replacement', 'knee replacement', 'prosthesis'],
    'trauma': ['fracture', 'fixation', 'tibia', 'femur', 'radius'],
}
ALIASES = {'侧弯': 'scoliosis', '脊柱': 'spinal', '髋': 'hip', '膝': 'knee',
           '置换': 'arthroplasty', '骨折': 'fracture', '钢板': 'plate', '髓内钉': 'intramedullary nail',
           '胫骨': 'tibia', '股骨': 'femur', '桡骨': 'radius', '感染': 'infection'}
NOTICE = '公开病例仅供类比，存在发表偏倚；检索分数不是疗效或置信度，不能替代指南与医生判断。'
SOURCE_POLICIES = {
    'zou-lab/MedCaseReasoning': ('CC-BY-4.0', 'train'),
    'zhengyun21/PMC-Patients': ('CC-BY-NC-SA-4.0', 'reference'),
}
SOURCE_REPOSITORIES = {
    'zou-lab/MedCaseReasoning': 'https://github.com/kevinwu23/Stanford-MedCaseReasoning',
    'zhengyun21/PMC-Patients': 'https://github.com/pmc-patients/pmc-patients',
}


def tokens(text):
    for zh, en in ALIASES.items():
        text = text.replace(zh, ' ' + en + ' ')
    return re.findall(r'[a-z][a-z0-9]+', text.lower())


@lru_cache(maxsize=4)
def _read_snapshot(filename, modified_ns, size):
    raw = Path(filename).read_bytes()
    rows, seen = [], set()
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (row['id'] in seen or row['scenario'] not in TOPICS or not row['case_text']
                or not re.fullmatch(r'PMC\d+', row['id'])
                or row['source_url'] != 'https://pmc.ncbi.nlm.nih.gov/articles/' + row['id'] + '/'
                or (row['license'], row['split']) != SOURCE_POLICIES.get(row.get('dataset', 'zou-lab/MedCaseReasoning'))):
            raise ValueError('病例库格式或来源校验失败，请重新导入。')
        seen.add(row['id'])
        rows.append(row)
    return rows, hashlib.sha256(raw).hexdigest()


def _file_key(path=None):
    file = (Path(path) if path else CORPUS).resolve()
    try:
        stat = file.stat()
    except FileNotFoundError:
        return None
    return str(file), stat.st_mtime_ns, stat.st_size


def _snapshot(path=None):
    key = _file_key(path)
    return _read_snapshot(*key) if key else ([], None)


def load_records(path=None):
    # Callers cannot poison the shared cache. Hash and records come from the
    # same byte snapshot even when a new corpus is atomically published.
    return copy.deepcopy(_snapshot(path)[0])


def status():
    try:
        rows = load_records()
        return {'available': bool(rows), 'count': len(rows),
                'scenarios': {s: sum(r['scenario'] == s for r in rows) for s in TOPICS},
                'method': 'BM25 + 场景与部位过滤', 'trained_weights': False,
                'dataset': ' / '.join(sorted({r.get('dataset', 'zou-lab/MedCaseReasoning').split('/')[-1] for r in rows})),
                'license': ' / '.join(sorted({r['license'] for r in rows})),
                'datasets': [{'name': name, 'count': sum(r.get('dataset', 'zou-lab/MedCaseReasoning') == name for r in rows),
                              'license': SOURCE_POLICIES[name][0], 'repository': SOURCE_REPOSITORIES[name]} for name in sorted({r.get('dataset', 'zou-lab/MedCaseReasoning') for r in rows})],
                'repository': 'https://github.com/kevinwu23/Stanford-MedCaseReasoning', 'notice': NOTICE}
    except (ValueError, KeyError, OSError):
        return {'available': False, 'count': 0, 'error': '病例库损坏，请重新导入。', 'notice': NOTICE}


@lru_cache(maxsize=16)
def _search_index(filename, modified_ns, size, scenario, site):
    rows, corpus_hash = _read_snapshot(filename, modified_ns, size)
    candidates = [r for r in rows if r['scenario'] == scenario]
    # A tibial fracture must not silently become a femoral fracture analogy.
    if site:
        variants = {'femur': ['femur', 'femoral'], 'tibia': ['tibia', 'tibial'],
                    'radius': ['radius', 'radial'], 'hip': ['hip'], 'knee': ['knee']}[site]
        candidates = [r for r in candidates if any(v in tokens(r['title'] + ' ' + r['case_text']) for v in variants)]
    docs = [Counter(tokens(r['title'] + ' ' + r['title'] + ' ' + r['case_text'])) for r in candidates]
    lengths = [sum(d.values()) for d in docs]
    frequencies = Counter(term for d in docs for term in d)
    avg = sum(lengths) / max(1, len(docs))
    return candidates, docs, lengths, frequencies, avg, corpus_hash


def retrieve(case, question='', limit=4, path=None):
    scenario = case.get('scenario')
    if scenario not in TOPICS:
        raise ValueError('不支持的检索场景。')
    measures = case.get('measurements', {})
    site = measures.get('fracture_site') if scenario == 'trauma' else measures.get('joint_type') if scenario == 'joint' else None
    key = _file_key(path)
    candidates, docs, lengths, frequencies, avg, corpus_hash = (
        _search_index(*key, scenario, site) if key else ([], [], [], Counter(), 0, None))
    query = ' '.join(TOPICS[scenario]) + ' ' + str(question)[:4000] + ' ' + case.get('clinical_summary', '')[:3000]
    query += ' ' + ' '.join(str(v) for v in measures.values() if isinstance(v, str))
    q = set(tokens(query))
    ranked = []
    for row, doc, length in zip(candidates, docs, lengths):
        score = 0.0
        matched = sorted(q & doc.keys())
        for term in matched:
            df = frequencies[term]
            idf = math.log(1 + (len(docs) - df + .5) / (df + .5))
            freq = doc[term]
            score += idf * freq * 2.2 / (freq + 1.2 * (.25 + .75 * length / max(1, avg)))
        if score:
            ranked.append((score, row, matched))
    ranked.sort(key=lambda x: (-x[0], x[1]['id']))
    items = []
    for index, (score, row, matched) in enumerate(ranked[:max(1, min(8, limit))], 1):
        items.append({**row, 'citation_id': 'E' + str(index), 'retrieval_score': round(score, 4),
                      'matched_terms': matched, 'case_text': row['case_text'][:5000],
                      'article_excerpt': row.get('article_excerpt', '')[:6000],
                      'applicability': '仅按场景和词项匹配；年龄、分型、合并症及随访差异需逐项人工核对。'})
    return {'items': items, 'status': 'retrieved' if items else 'insufficient', 'notice': NOTICE,
            'corpus_sha256': corpus_hash,
            'query_terms': sorted(q), 'method': 'BM25; scenario/site filter',
            'limitations': ['病例库是公开报告的筛选子集，未经过骨科专家标注。',
                            '原始叙述可能包含多名患者；不能假定所有治疗和结局都对应同一患者。',
                            '原文节选不完整，缺失字段不推断。']}
