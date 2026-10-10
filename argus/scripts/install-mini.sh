#!/bin/bash
# 在 Mini 上安装/升级 Argus：固定到指定提交，写 launchd，重启服务。
# 用法：bash scripts/install-mini.sh <git-sha>
# 凭证文件 secrets.env 需事先放好（0600），本脚本不创建、不打印凭证。
set -euo pipefail

SHA="${1:?需要传入要部署的提交 sha}"
APP="$HOME/Library/Application Support/PraiseZhu/pr-qa-agent"
CODE="$APP/code"
LOGS="$HOME/Library/Logs/PraiseZhu/pr-qa-agent"
LA="$HOME/Library/LaunchAgents"
NODE="$(command -v node)"
PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$APP/state" "$APP/evidence" "$LOGS"
[ -f "$APP/secrets.env" ] || { echo "缺少 $APP/secrets.env"; exit 3; }
chmod 600 "$APP/secrets.env"

if [ ! -d "$CODE/.git" ]; then
  git -c credential.helper= -c 'credential.helper=!gh auth git-credential' clone https://github.com/PraiseZhu/project-argus.git "$CODE"
fi
git -C "$CODE" -c credential.helper= -c 'credential.helper=!gh auth git-credential' fetch --quiet origin
git -C "$CODE" checkout --quiet --detach "$SHA"

write_plist() {
  local label="$1" extra="$2" args="$3"
  cat > "$LA/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$NODE</string>
    <string>--disable-warning=ExperimentalWarning</string>
    <string>$CODE/src/cli.mjs</string>
$args
  </array>
  <key>WorkingDirectory</key><string>$CODE</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_ENV</string></dict>
  <!-- Background 类型只用能效核，Mivo 单测会慢 5-7 倍；改 Standard + Nice 15，前台程序仍优先拿 CPU -->
  <key>ProcessType</key><string>Standard</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/$label.out.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$label.err.log</string>
$extra
</dict>
</plist>
PLIST
  plutil -lint "$LA/$label.plist" >/dev/null
}

write_plist com.praise.argus.control \
  '  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>ExitTimeOut</key><integer>60</integer>' \
  '    <string>serve</string>'

write_plist com.praise.argus.daily \
  '  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>' \
  '    <string>daily-report</string>'

UID_N="$(id -u)"
for label in com.praise.argus.control com.praise.argus.daily; do
  launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
  # bootout 是异步的：control 要优雅停掉在跑的测试，等它真正卸载再重新加载
  for _ in $(seq 1 90); do
    launchctl print "gui/$UID_N/$label" >/dev/null 2>&1 || break
    sleep 1
  done
  launchctl bootstrap "gui/$UID_N" "$LA/$label.plist"
done
echo "已部署 $SHA"
launchctl print "gui/$UID_N/com.praise.argus.control" | awk '/state =|pid =/'
