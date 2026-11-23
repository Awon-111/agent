"""Build a deduplicated orthopedic reference corpus from pinned official CSVs.

Uses only stdlib. Downloads public files without API keys; never executes data.
MedCaseReasoning validation/test articles are excluded across both sources.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import evidence

SOURCES = {
    'medcase': {'dataset': 'zou-lab/MedCaseReasoning', 'filename': 'medcasereasoning_core.csv',
                'repository': 'https://github.com/kevinwu23/Stanford-MedCaseReasoning',
                'license': 'CC-BY-4.0', 'attribution': 'Wu et al. (2025), MedCaseReasoning'},
    'pmc': {'dataset': 'zhengyun21/PMC-Patients', 'filename': 'PMC-Patients.csv',
            'repository': 'https://github.com/pmc-patients/pmc-patients',
            'license': 'CC-BY-NC-SA-4.0', 'attribution': 'Zhao et al. (2023), PMC-Patients'},
}


def classify(title):
    """Use title evidence only, preventing incidental body mentions from qualifying."""
    text = title.lower()
    if re.search(r'\b(scoliosis|kyphoscoliosis|kyphosis|spinal deformit\w*)\b', text):
        return 'spine'
    if (re.search(r'\b(hip|knee|femoral|acetabul\w*)\b', text)
            and re.search(r'\b(arthroplast\w*|replacement|prosthe\w*|periprosthetic)\b', text)):
        return 'joint'
    if (re.search(r'\bfractur\w*\b', text)
            and re.search(r'\b(tibia\w*|femur|femoral|radius|radial)\b', text)):
        return 'trauma'
    return None


def get_json(url):
    with urlopen(Request(url, headers={'User-Agent': 'OrthoEvidenceResearch/2.0'}), timeout=45) as stream:
        return json.load(stream)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download_ranges(url, size, target):
    """Use bounded HTTP ranges; some data CDNs stall on unbounded streams."""
    chunk_size = 8 * 1024 * 1024
    def part(start):
        end = min(size - 1, start + chunk_size - 1)
        path = target.with_suffix('.range-%d' % start)
        if path.exists() and path.stat().st_size == end - start + 1:
            return path
        for attempt in range(4):
            try:
                pieces = []
                width = 2 * 1024 * 1024 if attempt == 0 else 1024 * 1024
                for sub_start in range(start, end + 1, width):
                    sub_end = min(end, sub_start + width - 1)
                    req = Request(url + '&probe=%d-%d-%d' % (sub_start, sub_end, attempt),
                                  headers={'Range': 'bytes=%d-%d' % (sub_start, sub_end), 'User-Agent': 'OrthoEvidenceResearch/2.0'})
                    deadline = time.monotonic() + 35
                    with urlopen(req, timeout=15) as response:
                        expected_range = 'bytes %d-%d/%d' % (sub_start, sub_end, size)
                        if response.status != 206 or response.headers.get('Content-Range') != expected_range:
                            raise ValueError('Server did not honor the requested byte range')
                        chunks, received = [], 0
                        while received <= sub_end - sub_start:
                            if time.monotonic() > deadline:
                                raise TimeoutError('Byte range exceeded deadline')
                            chunk = response.read(min(64 * 1024, sub_end - sub_start + 1 - received))
                            if not chunk:
                                break
                            received += len(chunk)
                            chunks.append(chunk)
                        pieces.append(b''.join(chunks))
                content = b''.join(pieces)
                if len(content) != end - start + 1:
                    raise ValueError('Incomplete byte range')
                path.write_bytes(content)
                return path
            except Exception as exc:
                if attempt == 3:
                    raise
                print('Retrying smaller ranges:', target.name, start, type(exc).__name__, flush=True)
                time.sleep(2 ** attempt)
    partial = target.with_suffix('.part')
    with ThreadPoolExecutor(max_workers=2) as pool, partial.open('wb') as out:
        written = 0
        for path in pool.map(part, range(0, size, chunk_size)):
            content = path.read_bytes()
            out.write(content)
            written += len(content)
            print(target.name, written, '/', size, 'bytes', flush=True)
    return partial


def download(source, cache):
    meta = get_json('https://huggingface.co/api/datasets/' + source['dataset'])
    revision = meta['sha']
    tree = get_json('https://huggingface.co/api/datasets/' + source['dataset'] + '/tree/' + revision)
    entry = next(row for row in tree if row['path'] == source['filename'])
    expected, size = entry['lfs']['oid'], entry['size']
    if size > 1_000_000_000:
        raise ValueError('Public file exceeds the 1 GB download limit')
    target = cache / (source['dataset'].replace('/', '_') + '-' + revision[:12] + '.csv')
    url = 'https://huggingface.co/datasets/' + source['dataset'] + '/resolve/' + revision + '/' + source['filename'] + '?download=true'
    if target.exists() and target.stat().st_size == size and sha256(target) == expected:
        print('Verified cache:', source['dataset'], flush=True)
    else:
        partial = download_ranges(url, size, target)
        if partial.stat().st_size != size or sha256(partial) != expected:
            raise ValueError('File size/SHA256 mismatch; corpus remains unchanged')
        os.replace(partial, target)
        for start in range(0, size, 8 * 1024 * 1024):
            # Only remove segment files created by this downloader after the
            # assembled file passes the official checksum.
            target.with_suffix('.range-%d' % start).unlink(missing_ok=True)
        print('Downloaded and SHA256 verified:', source['dataset'], flush=True)
    return target, {'dataset': source['dataset'], 'revision': revision, 'url': url,
                    'sha256': expected, 'bytes': size, 'license': source['license'],
                    'repository': source['repository']}


def read_rows(path):
    csv.field_size_limit(10_000_000)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        yield from csv.DictReader(stream)


def make_record(pmcid, scenario, row, source, source_split):
    text = row.get('case_prompt') if source_split == 'train' else row.get('patient')
    if not isinstance(text, str) or len(text.strip()) < 100:
        return None
    return {'id': pmcid, 'scenario': scenario, 'title': row['title'],
            'case_text': text[:24000], 'article_excerpt': row.get('text', '')[:18000],
            'source_url': 'https://pmc.ncbi.nlm.nih.gov/articles/' + pmcid + '/',
            'journal': row.get('journal', ''), 'publication_date': row.get('publication_date', ''),
            'dataset': source['dataset'], 'repository': source['repository'], 'license': source['license'],
            'split': source_split, 'source_patient_uid': row.get('patient_uid', ''),
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'attribution': source['attribution'] + '; original article authors credited at source_url',
            'transformations': 'Orthopedic title filtering; one narrative per article; deduplicated by PMCID; text truncated.',
            'record_unit': 'article_case_prompt' if source_split == 'train' else 'one_patient_summary_per_article',
            'evidence_level': 'Published case narrative; no clinician adjudication or efficacy validation',
            'article_license': 'Consult original article; dataset license does not replace article-specific notices'}


def build(medcase_path, pmc_path=None):
    heldout, train_ids, records, counts = set(), set(), {}, Counter()
    # Get every held-out article before adding another source; no cross-source leakage.
    for row in read_rows(medcase_path):
        counts['medcase_scanned'] += 1
        pmcid = row.get('pmcid', '')
        if row.get('split') in ('val', 'validation', 'test'):
            heldout.add(pmcid)
        elif row.get('split') == 'train':
            train_ids.add(pmcid)
    for row in read_rows(medcase_path):
        pmcid = row.get('pmcid', '')
        scenario = classify(row.get('title', ''))
        if not scenario or row.get('split') != 'train' or pmcid in heldout or not re.fullmatch(r'PMC\d+', pmcid):
            continue
        record = make_record(pmcid, scenario, row, SOURCES['medcase'], 'train')
        if record:
            records.setdefault(pmcid, record)
    if pmc_path:
        for row in read_rows(pmc_path):
            counts['pmc_scanned'] += 1
            scenario = classify(row.get('title', ''))
            if not scenario:
                continue
            # Directory names such as PMC007xxxxxx are buckets, not article IDs.
            match = re.search(r'PMC\d+(?=\.xml$)', row.get('file_path', ''))
            if not match:
                continue
            pmcid = match.group()
            if pmcid in heldout:
                counts['pmc_heldout_excluded'] += 1
                continue
            if pmcid in records:
                counts['duplicate_article_rows_excluded'] += 1
                continue
            record = make_record(pmcid, scenario, row, SOURCES['pmc'], 'reference')
            if record:
                records[pmcid] = record
    counts['heldout_articles_excluded'] = len(heldout)
    counts['medcase_training_rows'] = len(train_ids)
    return list(records.values()), dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['medcase', 'all'], default='all')
    args = parser.parse_args()
    cache = evidence.CORPUS.parent / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    medcase, source_meta = download(SOURCES['medcase'], cache)
    metadata = [source_meta]
    pmc = None
    if args.source == 'all':
        pmc, source_meta = download(SOURCES['pmc'], cache)
        metadata.append(source_meta)
    rows, stats = build(medcase, pmc)
    if any(not any(r['scenario'] == s for r in rows) for s in evidence.TOPICS):
        raise ValueError('Missing scenario coverage; old corpus retained')
    staging = evidence.CORPUS.with_suffix('.staging')
    staging.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    evidence.load_records(staging)
    manifest = {'dataset': 'multiple' if pmc else SOURCES['medcase']['dataset'],
                'count': len(rows), 'scenarios': dict(Counter(r['scenario'] for r in rows)),
                'datasets': dict(Counter(r['dataset'] for r in rows)), 'sources': metadata,
                'sha256': sha256(staging), 'stats': stats,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'selection': 'Title-only orthopedic classification; one narrative per article; medcase train only; all medcase val/test PMCIDs excluded from every source',
                'purpose': 'Research retrieval corpus, not trained weights or clinical efficacy validation'}
    # Keep a recoverable backup; historical plan evidence snapshots are untouched.
    if evidence.CORPUS.exists():
        backup = evidence.CORPUS.with_name('cases-before-expansion.jsonl')
        if not backup.exists():
            backup.write_bytes(evidence.CORPUS.read_bytes())
    os.replace(staging, evidence.CORPUS)
    evidence.CORPUS.with_name('manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=True, indent=2), flush=True)


if __name__ == '__main__':
    main()
