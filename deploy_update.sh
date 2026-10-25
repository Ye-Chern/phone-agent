#!/data/data/com.termux/files/usr/bin/bash
# Run in Termux: bash /sdcard/Download/phone-agent-update/deploy_update.sh
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
dst="$HOME/.phoneagent"
mkdir -p "$dst"
chmod 700 "$dst"
python3 -m py_compile "$src/agent.py" "$src/phoneagent_core/"*.py
python3 - "$dst" <<'PY'
import json, pathlib, shutil, sqlite3, sys, time
p = pathlib.Path(sys.argv[1])
if (p / 'agent.db').exists():
    with sqlite3.connect(p / 'agent.db') as c:
        if c.execute("SELECT COUNT(*) FROM agent_runs WHERE status='running'").fetchone()[0]:
            raise SystemExit('An existing task is running; stop it before updating.')
backup = p / 'backups' / ('update-' + time.strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
for name in ('agent.py', 'secrets.json', 'runtime.json', 'watchdog.sh', 'start.sh'):
    if (p / name).exists(): shutil.copy2(p / name, backup / name)
if (p / 'web').exists(): shutil.copytree(p / 'web', backup / 'web')
if (p / 'phoneagent_core').exists(): shutil.copytree(p / 'phoneagent_core', backup / 'phoneagent_core')
if (p / 'gemini.key').exists(): shutil.copy2(p / 'gemini.key', backup / 'gemini.key')
if (p / 'agent.db').exists():
    with sqlite3.connect(p / 'agent.db') as source:
        with sqlite3.connect(backup / 'agent.db') as target: source.backup(target)
print('Private backup:', backup)
PY
# Ask the existing server to stop before replacing its source.
python3 - <<'PY'
import urllib.request
try:
    req = urllib.request.Request('http://127.0.0.1:8756/api/shutdown', data=b'{}', headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=8).close()
except Exception:
    pass
PY
sleep 2
cp "$src/agent.py" "$dst/agent.py"
cp -r "$src/phoneagent_core" "$dst/"
cp "$src/start.sh" "$src/watchdog.sh" "$dst/"
mkdir -p "$dst/web"
cp -r "$src/web/". "$dst/web/"
if [ -d "$src/examples/skills/project-review" ] && [ ! -e "$dst/skills/project-review" ]; then
  mkdir -p "$dst/skills"
  cp -r "$src/examples/skills/project-review" "$dst/skills/"
fi
cd "$dst"
termux-wake-lock 2>/dev/null || true
nohup python3 agent.py >> service.log 2>&1 < /dev/null &
python3 - <<'PY'
import json, time, urllib.request
for _ in range(20):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8756/api/status', timeout=2) as r: s=json.load(r)
        if s.get('version') == '2.2.0':
            print('DEPLOY_OK', s['version'], s['default_model'])
            break
    except Exception: pass
    time.sleep(1)
else: raise SystemExit('DEPLOY_FAILED; inspect ~/.phoneagent/service.log. Private backup preserved.')
PY
