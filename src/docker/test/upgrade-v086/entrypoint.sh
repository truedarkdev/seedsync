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
replace enabled "${SEEDSYNC_LAB_AUTOQUEUE_ENABLED:-true}"
replace patterns_only "${SEEDSYNC_LAB_AUTOQUEUE_PATTERNS_ONLY:-true}"
replace auto_extract "${SEEDSYNC_LAB_AUTOQUEUE_AUTO_EXTRACT:-true}"
case "${SEEDSYNC_LAB_TRANSIENT_MODE:-0}" in
  0)
    [ -z "${LFTP_HOME:-}" ] || { echo 'ambient LFTP_HOME is not accepted on stable runs' >&2; exit 1; }
    ;;
  1)
    [ "${LFTP_HOME:-}" = /config/.lftp ] || { echo 'transient LFTP_HOME must be /config/.lftp' >&2; exit 1; }
    replace num_max_parallel_downloads 1
    replace num_max_parallel_files_per_download 1
    replace num_max_connections_per_root_file 1
    replace num_max_connections_per_dir_file 1
    replace num_max_total_connections 1
    ;;
  *) echo 'invalid transient mode' >&2; exit 1 ;;
esac

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
