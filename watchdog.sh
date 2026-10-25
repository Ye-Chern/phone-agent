#!/data/data/com.termux/files/usr/bin/bash
# Do not truncate structured logs when restarting the service.
termux-wake-lock 2>/dev/null || true
cd "$HOME/.phoneagent" || exit 1
if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8756/api/status', timeout=8).close()" 2>/dev/null; then
  exit 0
fi
# Bracket expression avoids matching this script's own command line.
pkill -f 'python3 agen[t].py' 2>/dev/null || true
sleep 1
nohup python3 agent.py >> service.log 2>&1 < /dev/null &
