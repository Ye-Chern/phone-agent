#!/data/data/com.termux/files/usr/bin/bash
# Invoked by the APK first-run guide; no keys or user data are bundled.
set -euo pipefail
umask 077
src="$(cd "$(dirname "$0")" && pwd)"
dst="$HOME/.phoneagent"
python3 -c 'import cryptography' || { echo '请先安装：pkg install python python-cryptography'; exit 1; }
if ! python3 -c 'import jwt' 2>/dev/null; then
  python3 -m pip install 'PyJWT>=2.8,<3'
fi
python3 -m py_compile "$src/agent.py" "$src/phoneagent_core/"*.py
python3 - "$src" "$dst" <<'PY'
import hashlib,json,pathlib,shutil,sqlite3,sys,time
src,dst=map(pathlib.Path,sys.argv[1:])
manifest=json.loads((src/'BACKEND_SHA256.json').read_text('utf-8'))
for name,digest in manifest.items():
    file=(src/name).resolve()
    file.relative_to(src.resolve())
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
        raise SystemExit('安装包校验失败，请重新安装官方分享包')
dst.mkdir(parents=True,exist_ok=True);dst.chmod(0o700)
if (dst/'agent.db').exists():
    with sqlite3.connect(dst/'agent.db') as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'agent_runs' in tables and conn.execute("SELECT COUNT(*) FROM agent_runs WHERE status='running'").fetchone()[0]:
            raise SystemExit('请停止正在运行的PhoneAgent任务，再更新')
if (dst/'agent.py').exists():
    backup=dst/'backups'/('share-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=True)
    for name in ('agent.py','start.sh','watchdog.sh','runtime.json','secrets.json','gemini.key','web','phoneagent_core'):
        item=dst/name
        if item.is_dir():shutil.copytree(item,backup/name)
        elif item.is_file():shutil.copy2(item,backup/name)
    if (dst/'agent.db').exists():
        with sqlite3.connect(dst/'agent.db') as origin, sqlite3.connect(backup/'agent.db') as target:
            origin.backup(target)
print('INSTALL_SOURCE_VERIFIED')
PY
python3 - <<'PY'
import urllib.request
try:
    req=urllib.request.Request('http://127.0.0.1:8756/api/shutdown',data=b'{}',headers={'Content-Type':'application/json'})
    urllib.request.urlopen(req,timeout=5).close()
except Exception:pass
PY
sleep 2
python3 - "$src" "$dst" <<'PY'
import pathlib,shutil,sys
src,dst=map(pathlib.Path,sys.argv[1:])
for name in ('agent.py','start.sh','watchdog.sh','share_install.sh'):
    shutil.copy2(src/name,dst/name)
for folder in ('phoneagent_core','web'):
    shutil.copytree(src/folder,dst/folder,dirs_exist_ok=True)
example=src/'examples/skills/project-review'
if example.exists() and not (dst/'skills/project-review').exists():
    shutil.copytree(example,dst/'skills/project-review')
# Credentials, runtime policy, database and auth registration are left local.
PY
cd "$dst"
termux-wake-lock 2>/dev/null || true
nohup python3 agent.py >> service.log 2>&1 < /dev/null &
python3 - <<'PY'
import json,time,urllib.request
for _ in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8756/api/status',timeout=2) as r:status=json.load(r)
        if status.get('version')=='2.2.0':
            print('PHONEAGENT_INSTALL_OK 2.2.0')
            print('返回PhoneAgent，打开“API与连接”填写自己的API，或登录自己的ChatGPT账户。')
            break
    except Exception:pass
    time.sleep(1)
else:raise SystemExit('启动未完成，请打开Termux检查 ~/.phoneagent/service.log')
PY
