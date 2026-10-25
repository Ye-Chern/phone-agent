"""Create the APK runtime payload from an explicit source-only allowlist."""
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = [root / name for name in ('agent.py','start.sh','watchdog.sh','share_install.sh','install.sh','requirements-phoneagent-auth.txt')]
for folder in ('phoneagent_core','web','examples/skills/project-review'):
    files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
blocked = {'secrets.json','runtime.json','agent.db','gemini.key','chatgpt_accounts.json','chatgpt_hostid','debug.keystore','.phoneagent_auth'}
manifest = {}
for file in sorted(set(files)):
    name = file.relative_to(root).as_posix()
    assert not blocked.intersection(file.relative_to(root).parts), name
    manifest[name] = hashlib.sha256(file.read_bytes()).hexdigest()
target = root / 'app/assets/backend.zip'
target.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name in manifest:
        archive.write(root/name,name)
    archive.writestr('BACKEND_SHA256.json',json.dumps(manifest,indent=2))
report={'files':len(manifest),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'private_data_included':False,'allowlist':'runtime source, bundled web assets, example skill; no work logs or user configuration'}
out=root/'work/upgrade-20261013';out.mkdir(parents=True,exist_ok=True)
(out/'backend-asset.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(report,ensure_ascii=False))
