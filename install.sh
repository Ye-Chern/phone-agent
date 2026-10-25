#!/data/data/com.termux/files/usr/bin/bash
# PhoneAgent 首次安装脚本（在 Termux 里运行）
set -e
echo "[1/4] 检查 Python..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "未安装 Python，开始安装（需要几分钟，请保持网络）..."
  pkg update -y && pkg install -y python
fi
echo "[2/4] 安装 termux-api（可选，用于通知/剪贴板）..."
pkg install -y termux-api 2>/dev/null || true
echo "[3/4] 复制文件..."
SRC=~/storage/shared/Download/phone-agent
if [ -d "$SRC" ]; then
  mkdir -p ~/.phoneagent
  chmod 700 ~/.phoneagent
  cp "$SRC/agent.py" "$SRC/start.sh" "$SRC/install.sh" ~/.phoneagent/
  cp -r "$SRC/web" ~/.phoneagent/web
  cp -r "$SRC/phoneagent_core" ~/.phoneagent/
  if [ -d "$SRC/examples/skills/project-review" ] && [ ! -e ~/.phoneagent/skills/project-review ]; then
    mkdir -p ~/.phoneagent/skills
    cp -r "$SRC/examples/skills/project-review" ~/.phoneagent/skills/
  fi
else
  echo "!! 找不到 $SRC，请先把 phone-agent 文件夹放到手机 Download 目录"
  exit 1
fi
cd ~/.phoneagent
if [ ! -f secrets.json ]; then
  echo '{"deepseek_key":"","deepseek_model":"deepseek-flash","exa_key":""}' > secrets.json
  chmod 600 secrets.json
  echo '请先在 ~/.phoneagent/secrets.json 填写 deepseek_key，然后运行 bash start.sh'
  exit 0
fi
chmod +x *.sh 2>/dev/null || true
echo "[4/4] 启动..."
nohup python3 agent.py >> service.log 2>&1 < /dev/null &
sleep 2
tail -5 service.log
echo ""
echo "✅ 完成！用手机浏览器打开 http://127.0.0.1:8756/web/"
