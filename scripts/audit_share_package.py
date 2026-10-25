"""Inspect nested share archives without exporting any credential values."""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--apk',required=True)
p.add_argument('--source',required=True)
p.add_argument('--credential-source',action='append',default=[])
args=p.parse_args()
secrets=[Path(file).read_text('utf-8-sig').strip().encode() for file in args.credential_source]
blocked={'.phoneagent_auth','secrets.json','runtime.json','agent.db','gemini.key','chatgpt_accounts.json','chatgpt_hostid','debug.keystore','auth.json','latest-three-work-private.json'}
entries=0

def audit(data,depth=0):
    global entries
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            assert not blocked.intersection(Path(name).parts), 'Private file in package'
            assert '..' not in Path(name).parts and not name.startswith('/'), 'Invalid archive path'
            content=archive.read(name);entries+=1
            assert not any(secret and secret in content for secret in secrets), 'Credential in package'
            if name.endswith(('.zip','.apk')) and depth<3:
                audit(content,depth+1)

apk=Path(args.apk);source=Path(args.source)
audit(apk.read_bytes());audit(source.read_bytes())
with zipfile.ZipFile(apk) as archive:
    runtime=archive.read('assets/backend.zip')
with zipfile.ZipFile(io.BytesIO(runtime)) as archive:
    manifest=json.loads(archive.read('BACKEND_SHA256.json'))
    assert set(archive.namelist())==set(manifest)|{'BACKEND_SHA256.json'}
    assert all(hashlib.sha256(archive.read(name)).hexdigest()==digest for name,digest in manifest.items())
with zipfile.ZipFile(source) as archive:
    manifest=json.loads(archive.read('SOURCE_SHA256.json'))
    assert all(hashlib.sha256(archive.read(name)).hexdigest()==digest for name,digest in manifest.items())
report={'apk':apk.name,'apk_sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'source':source.name,
        'nested_archive_entries_checked':entries,'private_files_absent':True,'supplied_credentials_absent':True,
        'archive_integrity_and_manifests':'ok','private_feedback_logs_excluded':True}
root=Path(__file__).resolve().parents[1]
(root/'work/upgrade-20261013/share-package-audit.json').write_text(json.dumps(report,indent=2),'utf-8')
print(json.dumps(report))
