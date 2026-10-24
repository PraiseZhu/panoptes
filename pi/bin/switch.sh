#!/bin/bash
# switch.sh pi|grok [--with-outbound] [--dry-run]
#   Sets /workspace/fleet-mode, prints the Grok routines to pause (pi) or resume (grok).
#   --with-outbound: pi -> create outbound-enabled ; grok -> remove it. Without the flag outbound-enabled is untouched.
#   pi -> touch takeover/{cindy,boxhealth}; grok -> remove them.
#   --dry-run: print what would change, change nothing.
set -eu
B=${PI_STATE:-/workspace/mivo-qa/pi}; MODE=${1:?usage: switch.sh pi|grok [--with-outbound] [--dry-run]}; shift
OUT=0; DRY=0; for a in "$@"; do case $a in --with-outbound) OUT=1;; --dry-run) DRY=1;; *) echo "bad arg $a"; exit 2;; esac; done
[[ $MODE == pi || $MODE == grok ]] || { echo "mode must be pi|grok"; exit 2; }
do_() { if [ $DRY = 1 ]; then echo "DRY: $*"; else eval "$@"; fi; }
echo "fleet-mode: $(cat /workspace/fleet-mode 2>/dev/null || echo unset) -> $MODE"
do_ "echo $MODE > /workspace/fleet-mode"
if [ $OUT = 1 ]; then
  if [ $MODE = pi ]; then do_ "touch $B/outbound-enabled"; else do_ "rm -f $B/outbound-enabled"; fi
fi
echo "outbound-enabled: $([ -e $B/outbound-enabled ] && echo present || echo absent)$([ $DRY = 1 ] && [ $OUT = 1 ] && echo " (would become $([ $MODE = pi ] && echo present || echo absent))")"
# Pi takeover markers for duties that would double-run (Cindy keep-alive, box-health): pi -> create, grok -> remove
for t in cindy boxhealth dailyretro roster prfix-event infrafix-event; do   # 10-23: Pi line owns these in pi mode
  if [ $MODE = pi ]; then do_ "mkdir -p $B/takeover && touch $B/takeover/$t"; else do_ "rm -f $B/takeover/$t"; fi
done
VERB=$([ $MODE = pi ] && echo PAUSE || echo RESUME)
cat <<T

$VERB these Grok Bot routines (in the Grok Bot app; bot-to-bot scheduling inside Grok stays on Grok):
  Mivo QA 日报     : mivo-qa-daily-report 08:30, retro 09:56, followup 15:17, followup-scan 06:17, handoff 回执 18:30/00:00, ledger 23:30, roster 1/15 08:23
  Mivo 测试项目经理: 「Mivo 测试线每日核对」10:30, praise-pr-2「Praise PR 白天催办」10–20 每 2h
  Mivo QA 运维     : panoptes-env-guard (hourly), panoptes-daily-review 10:57  (mivo-qa-on-merge may stay: catch-up cron covers it)
  Praise PR        : 9:41 复盘, 22:23 清理
  Mivo 合并官      : mivo-merge-sweep :13/:43
  备份管家         : 「grokbot-box 推送监控」:40
  电脑运维         : box-health :17        (Pi job gated by takeover/boxhealth)
  Cindy 管家       : 「Cindy 保活」@every 5m (Pi job gated by takeover/cindy)
  知识沉淀         : 周五 8:04 选题, 工作日 10:04 X 风格收集 (needs X_BEARER_TOKEN + takeover/xstyle)
  Jasper           : 9:00 简报
T
if [ $MODE = pi ]; then
  echo; echo "Pi takeover markers present: $(ls $B/takeover 2>/dev/null | tr '\n' ' ')"
  echo "Gated jobs without marker stay off: roster dailyretro xstyle (touch $B/takeover/<name> to enable)"
  [ -e $B/outbound-enabled ] || [ $OUT = 1 ] || echo "NOTE: outbound still closed (files only). Re-run with --with-outbound to let bin/outbound.py send."
fi
