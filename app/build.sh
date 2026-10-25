#!/data/data/com.termux/files/usr/bin/bash
# PhoneAgent 原生应用构建脚本（在 Termux 里运行）
set -e
cd "$(dirname "$0")"

# 总是从共享存储同步最新源码，避免旧文件被误用
if [ -d /sdcard/Download/phone-agent-app ]; then
  cp -f /sdcard/Download/phone-agent-app/src/com/moye/phoneagent/MainActivity.java src/com/moye/phoneagent/MainActivity.java
  cp -f /sdcard/Download/phone-agent-app/AndroidManifest.xml AndroidManifest.xml
fi

# The distributable backend ZIP is generated from sanitized project sources.
# It contains no account data, API keys, or conversation database.
mkdir -p assets
if [ -f /sdcard/Download/phone-agent-app/assets/backend.zip ]; then
  cp -f /sdcard/Download/phone-agent-app/assets/backend.zip assets/backend.zip
fi
if [ ! -f assets/backend.zip ]; then
  echo "!! 缺少 assets/backend.zip；请先准备不含个人数据的后端包"
  exit 1
fi

echo "== 源码校验和 =="
md5sum src/com/moye/phoneagent/MainActivity.java /sdcard/Download/phone-agent-app/src/com/moye/phoneagent/MainActivity.java 2>/dev/null || true

AJ="$HOME/.phoneagent-app/android.jar"
if [ ! -f "$AJ" ]; then
  echo "!! 找不到 android.jar ($AJ)"
  exit 1
fi
echo "android.jar: $AJ"

rm -rf out
mkdir -p out/gen out/classes out/dex

echo "[1/5] 生成资源..."
aapt package -f -m -M AndroidManifest.xml -S res -A assets -I "$AJ" -J out/gen

echo "[2/5] 编译 Java..."
find src out/gen -name "*.java" > out/sources.txt
JV=$(find "$PREFIX/lib/jvm" -name javac -type f 2>/dev/null | head -1)
if [ -n "$JV" ]; then
  "$JV" -nowarn -source 1.8 -target 1.8 \
    -bootclasspath "$AJ:$HOME/.phoneagent-app/core-lambda-stubs.jar" \
    -d out/classes @out/sources.txt
else
  echo "!! 找不到 javac (openjdk)，请: pkg install openjdk-21"
  exit 1
fi

echo "[3/5] 转换 dex..."
find out/classes -name "*.class" > out/classlist.txt
d8 --min-api 24 --output out/dex @out/classlist.txt

echo "[4/5] 打包 APK..."
(cd out/dex && aapt package -f -M ../../AndroidManifest.xml -S ../../res -A ../../assets -I "$AJ" -F ../unsigned.apk)
(cd out/dex && aapt add ../unsigned.apk classes.dex)

echo "[5/5] 签名..."
KS=debug.keystore
apksigner sign --ks "$KS" --ks-pass pass:phoneagent --key-pass pass:phoneagent \
  --out PhoneAgent.apk out/unsigned.apk

cp PhoneAgent.apk /sdcard/Download/PhoneAgent.apk 2>/dev/null || true
echo ""
echo "✅ 构建完成: $(pwd)/PhoneAgent.apk"
