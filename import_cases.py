"""Download an orthopedic training-split subset from the official HF dataset viewer."""
import argparse
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from evidence import CORPUS, TOPICS, load_records

DATASET = 'zou-lab/MedCaseReasoning'
REPO = 'https://github.com/kevinwu23/Stanford-MedCaseReasoning'


def fetch(params):
    url = 'https://datasets-server.huggingface.co/rows?' + urlencode(params)
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'OrthoEvidenceResearch/1.0'}), timeout=45) as response:
                raw = response.read(25_000_001)
            if len(raw) > 25_000_000:
                raise ValueError('数据页过大')
            return json.loads(raw), url
        except HTTPError as exc:
            retry = exc.code in (429, 500, 502, 503, 504)
            exc.close()
            if attempt == 2 or not retry:
                raise
            time.sleep(10 * (attempt + 1))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--per-scenario', type=int, default=60)
    parser.add_argument('--max-rows', type=int, default=3000, help='Number of training rows to scan, at most 13092')
    parser.add_argument('--refresh', action='store_true', help='Ignore cached public training pages')
    parser.add_argument('--replace-expanded-corpus', action='store_true', help='Explicitly replace an expanded multi-source corpus with this small subset')
    args = parser.parse_args()
    if (not args.replace_expanded_corpus and any(r.get('dataset') == 'zhengyun21/PMC-Patients' for r in load_records())):
        parser.error('当前为多来源扩充库。请使用 expand_cases.py；如确需替换为小子集，显式添加 --replace-expanded-corpus。')
    if not 1 <= args.per_scenario <= 500:
        parser.error('--per-scenario must be 1..500')
    if not 100 <= args.max_rows <= 13092:
        parser.error('--max-rows must be 100..13092')
    records, seen, sources = [], set(), []
    cache = CORPUS.parent / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    def page(offset):
        params = {'dataset': DATASET, 'config': 'default', 'split': 'train',
                  'offset': offset, 'length': min(100, args.max_rows-offset)}
        cached = cache / ('train-%d-%d.json' % (offset, params['length']))
        if cached.exists() and not args.refresh:
            data = json.loads(cached.read_text(encoding='utf-8'))
        else:
            data = fetch(params)
            cached.write_text(json.dumps(data), encoding='utf-8')
            time.sleep(1)
        return data
    # The viewer's filter/search endpoints can be unavailable. Scan bounded rows
    # from the train endpoint and apply reproducible local title filters instead.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pages = list(pool.map(page, range(0, args.max_rows, 100)))
    for data, url in pages:
        sources.append(url)
    scanned = [item for data, _ in pages for item in data.get('rows', [])]
    for scenario, terms in TOPICS.items():
        # Title filtering limits incidental mentions; do not use held-out test rows.
        terms = {'spine': ['scoliosis'], 'joint': ['arthroplasty', 'hip replacement', 'knee replacement'],
                 'trauma': ['tibial fracture', 'femoral fracture', 'radius fracture', 'tibia fracture']}[scenario]
        count, offset = 0, 0
        while count < args.per_scenario:
            batch = [item for item in scanned if any(term in item['row'].get('title', '').lower() for term in terms)][:args.per_scenario]
            if not batch:
                break
            for item in batch:
                row = item['row']
                pmcid = str(row.get('pmcid', ''))
                if not re.fullmatch(r'PMC\d+', pmcid) or pmcid in seen or not row.get('case_prompt'):
                    continue
                seen.add(pmcid)
                # Store source narratives, not generated diagnostic reasoning as clinical ground truth.
                records.append({'id': pmcid, 'scenario': scenario, 'title': row['title'],
                                'case_text': row['case_prompt'], 'article_excerpt': row.get('text', '')[:18000],
                                'source_url': 'https://pmc.ncbi.nlm.nih.gov/articles/' + pmcid + '/',
                                'journal': row.get('journal', ''), 'publication_date': row.get('publication_date', ''),
                                'dataset': DATASET, 'repository': REPO, 'license': 'CC-BY-4.0',
                                'split': 'train', 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                                'attribution': 'Wu et al., MedCaseReasoning (2025); original authors credited in source_url',
                                'transformations': 'Orthopedic title filtering; article text truncated to 18000 characters.'})
                count += 1
            offset += len(batch)
            break
        print(scenario, count)
    if any(not any(r['scenario'] == s for r in records) for s in TOPICS):
        raise ValueError('至少一个场景无数据；保留旧病例库。')
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    temp = CORPUS.with_suffix('.tmp')
    temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    load_records(temp)
    manifest = {'dataset': DATASET, 'repository': REPO, 'license': 'CC-BY-4.0', 'split': 'train',
                'count': len(records), 'sha256': hashlib.sha256(temp.read_bytes()).hexdigest(),
                'scanned_training_rows': len(scanned),
                'source_requests': sources, 'created_at': datetime.now(timezone.utc).isoformat(),
                'purpose': 'retrieval corpus, not trained model weights; no validation/test rows imported'}
    os.replace(temp, CORPUS)
    CORPUS.with_name('manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved', len(records), 'public case reports to', CORPUS)


if __name__ == '__main__':
    main()
