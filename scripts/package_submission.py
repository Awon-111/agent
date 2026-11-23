"""Package only the files recorded in SUBMISSION_FILES.json."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_submission import ROOT, validate

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT.parent/'agent-main-submission.zip')
    args=parser.parse_args()
    manifest=validate()
    names=[x['path'] for x in manifest['files']]+['SUBMISSION_FILES.json']
    with ZipFile(args.output,'w',ZIP_DEFLATED,compresslevel=9) as archive:
        for name in sorted(names):
            archive.write(ROOT/name,'agent-main/'+name)
    with ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP validation failed')
        if len(archive.namelist())!=len(names):
            raise ValueError('ZIP file count mismatch')
        for name in names:
            if archive.read('agent-main/'+name)!=(ROOT/name).read_bytes():
                raise ValueError('ZIP content differs from repository: '+name)
    print(f'Created {args.output.name}: {len(names)} files, byte-for-byte checked.')

if __name__=='__main__':
    main()
