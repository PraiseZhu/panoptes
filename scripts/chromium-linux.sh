#!/bin/sh
# panoptes: local Chromium launcher for Linux QA boxes.
# Install: sudo install -m 755 scripts/chromium-linux.sh /usr/local/bin/chromium
# The Mivo profile points BD2_CHROME at /usr/local/bin/chromium (a path Mivo's browser-layer trusts).
#
# Same behaviour as Debian's /usr/bin/chromium launcher (sources /etc/chromium.d/*, same CHROMIUM_FLAGS),
# except the /dev/shm check: Debian's /etc/chromium.d/dev-shm runs `[ "$shm_avail" -lt ... ]` on the raw
# `findmnt` output, which has one line per mount when /dev/shm is stacked (e.g. inside bubblewrap with
# --dev /dev) and prints "/usr/bin/chromium: 9: [: ...: unexpected operator". Here only the first line
# is used. The Debian package files stay untouched.
set -u

LIBDIR=/usr/lib/chromium
APPNAME=chromium

# Rarely used launcher options (help, gdb, temp profile): hand over to the Debian launcher unchanged.
for a in "$@"; do
  case "$a" in
    -h|--help|-help|-g|--debug|--temp-profile) exec /usr/bin/chromium "$@" ;;
    --) break ;;
  esac
done

CHROMIUM_FLAGS="${CHROMIUM_FLAGS:-}"
for file in /etc/chromium.d/*; do
  case "$file" in
    /etc/chromium.d/README|/etc/chromium.d/dev-shm|*.dpkg*) continue ;;
  esac
  # shellcheck disable=SC1090
  . "$file"
done

# Fixed /dev/shm check (see header); 3.8 GB threshold as in Debian's dev-shm
shm_avail=$(findmnt -bnr -o avail -T /dev/shm 2>/dev/null | head -n1)
case "$shm_avail" in ''|*[!0-9]*) shm_avail=0 ;; esac
if [ "$shm_avail" -lt 4080218931 ]; then
  CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-dev-shm-usage"
fi

export CHROME_WRAPPER="${CHROME_WRAPPER:-/usr/local/bin/$APPNAME}"
export CHROME_DESKTOP="chromium.desktop"
export CHROME_VERSION_EXTRA="built on Debian GNU/Linux (panoptes wrapper)"

case " $CHROMIUM_FLAGS $* " in
  *" --enable-remote-extensions "*) ;;
  *)
    CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-background-networking"
    ext=$(echo "$CHROMIUM_FLAGS" | tr ' ' '\n' | grep -- '--load-extension' | cut -d= -f2 | tr '\n' ,)
    CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-extensions-except=$ext"
    ;;
esac
export CHROMIUM_FLAGS

# shellcheck disable=SC2086  # CHROMIUM_FLAGS is a word list, as in the Debian launcher
exec "$LIBDIR/$APPNAME" $CHROMIUM_FLAGS "$@"
