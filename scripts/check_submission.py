"""Validate the exact, non-secret repository submission snapshot."""
from pathlib import Path
import hashlib
import json
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
# Exact hashes of the two deliberately nonfunctional keys used by protocol tests.
# A test filename alone is never enough to exempt arbitrary credential-like text.
TEST_FIXTURE_HASHES={
    '6d2f3c1dea1c2716c39705030b86b3a29e931ffc420e5669f3ed7a007ef90c0e',
    'ccc1a69318b54b522578ba24fdf41491697c0a49a74315f2a8cdb6b8df833a61',
}

def validate():
    manifest_path=ROOT/'SUBMISSION_FILES.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    files=manifest['files']
    names=[x['path'] for x in files]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate submission paths')
    for row in files:
        name=row['path']
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid repository-relative path')
        if relative.name=='.env' or relative.suffix in {'.sqlite3','.log','.zip'} or '.cache' in relative.parts:
            raise ValueError('Private runtime file in submission: '+name)
        data=(ROOT/relative).read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['sha256'] or len(data)!=row['bytes']:
            raise ValueError('Submission hash mismatch: '+name)
        if name.endswith(('.py','.md','.json','.html','.css','.js','.yml','.txt','.example','.ps1')):
            text=data.decode('utf-8-sig')
            for token in re.findall(r'sk-[A-Za-z0-9_-]{20,}',text):
                if hashlib.sha256(token.encode()).hexdigest() not in TEST_FIXTURE_HASHES:
                    raise ValueError('Possible real API credential: '+name)
    corpus=ROOT/'data/evidence/cases.jsonl'
    meta=json.loads((ROOT/'data/evidence/manifest.json').read_text(encoding='utf-8'))
    records=[json.loads(x) for x in corpus.read_text(encoding='utf-8').splitlines() if x.strip()]
    if len(records)!=meta['count'] or hashlib.sha256(corpus.read_bytes()).hexdigest()!=meta['sha256']:
        raise ValueError('Evidence corpus does not match its source manifest')
    print(f'Checked {len(files)} files and {len(records)} article references; hashes match.')
    return manifest

if __name__=='__main__':
    try:
        validate()
    except (OSError,ValueError,KeyError) as exc:
        print('Submission validation failed: '+str(exc),file=sys.stderr)
        raise SystemExit(1)
