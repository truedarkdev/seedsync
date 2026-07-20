#!/bin/sh
set -eu

CONFIG_DIR=${SEEDSYNC_CONFIG_DIR:-/config}
HTML_DIR=${SEEDSYNC_HTML_DIR:-/app/html}
SCANFS=${SEEDSYNC_SCANFS:-/usr/local/bin/scanfs}
mkdir -p "$CONFIG_DIR" /downloads /mounts /logs

if [ ! -s "$CONFIG_DIR/settings.cfg" ]; then
  python /app/python/seedsync.py -c "$CONFIG_DIR" --html "$HTML_DIR" --scanfs "$SCANFS" --exit >/dev/null 2>&1 || true
fi

settings="$CONFIG_DIR/settings.cfg"
if [ ! -s "$settings" ]; then
  echo 'legacy SeedSync config generation failed' >&2
  exit 1
fi

replace() {
  key="$1"
  value="$2"
  sed -i "s|^[[:space:]]*${key}[[:space:]]*=.*$|${key} = ${value}|" "$settings"
}

replace remote_address "${SEEDSYNC_LAB_REMOTE_HOST:-remote}"
replace remote_username "${SEEDSYNC_LAB_REMOTE_USER:-remoteuser}"
replace remote_password "${SEEDSYNC_LAB_REMOTE_PASSWORD:-remotepass}"
replace remote_port "${SEEDSYNC_LAB_REMOTE_PORT:-1234}"
replace remote_path "${SEEDSYNC_LAB_REMOTE_PATH:-/home/remoteuser/files}"
replace local_path "${SEEDSYNC_LAB_LOCAL_PATH:-/downloads}"
replace extract_path "${SEEDSYNC_LAB_EXTRACT_PATH:-/downloads}"

if [ -n "${SEEDSYNC_LAB_REMOTE_HOST:-}" ]; then
  attempts=0
  while ! python -c 'import socket,sys; s=socket.create_connection((sys.argv[1], int(sys.argv[2])), 1); s.close()' \
      "${SEEDSYNC_LAB_REMOTE_HOST}" "${SEEDSYNC_LAB_REMOTE_PORT:-1234}" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    [ "$attempts" -ge 60 ] && echo 'legacy SSH fixture did not become reachable' >&2 && exit 1
    sleep 1
  done
fi

exec python /app/python/seedsync.py \
  -c "$CONFIG_DIR" \
  --html "$HTML_DIR" \
  --scanfs "$SCANFS" \
  --logdir /logs
