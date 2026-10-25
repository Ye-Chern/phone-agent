#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8756/api/status', timeout=3).close()" 2>/dev/null; then
  echo 'PhoneAgent is already running.'
  exit 0
fi
termux-wake-lock 2>/dev/null || true
nohup python3 agent.py >> service.log 2>&1 < /dev/null &
echo 'PhoneAgent started. Open http://127.0.0.1:8756/web/'
