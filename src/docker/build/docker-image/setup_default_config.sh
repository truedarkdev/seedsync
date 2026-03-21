#!/bin/bash

set -e

CONFIG_DIR="/config"
SETTINGS_FILE="${CONFIG_DIR}/settings.cfg"
SCRIPT_PATH="/app/python/seedsync.py"
DEFAULT_LOCAL_PATH="/downloads/"

generate_default_config() {
    python "${SCRIPT_PATH}" \
        -c "${CONFIG_DIR}" \
        --html / \
        --scanfs / \
        --exit > /dev/null 2>&1 || true
}

replace_local_path() {
    sed -i -E "s|^[[:space:]]*local_path[[:space:]]*=.*$|local_path = ${DEFAULT_LOCAL_PATH}|" "${SETTINGS_FILE}"
}

append_local_path_to_lftp_section() {
    awk -v local_path="${DEFAULT_LOCAL_PATH}" '
        BEGIN { in_lftp = 0; inserted = 0 }
        /^\[Lftp\][[:space:]]*$/ {
            in_lftp = 1
            print
            next
        }
        /^\[[^]]+\][[:space:]]*$/ {
            if (in_lftp && !inserted) {
                print "local_path = " local_path
                inserted = 1
            }
            in_lftp = 0
            print
            next
        }
        {
            print
        }
        END {
            if (in_lftp && !inserted) {
                print "local_path = " local_path
            }
        }
    ' "${SETTINGS_FILE}" > "${SETTINGS_FILE}.tmp"
    mv "${SETTINGS_FILE}.tmp" "${SETTINGS_FILE}"
}

if [ ! -f "${SETTINGS_FILE}" ]; then
    echo "Generating default SeedSync config in ${CONFIG_DIR}"
fi

# Normalize the config schema first so placeholder repair happens last.
generate_default_config

if [ ! -f "${SETTINGS_FILE}" ]; then
    echo "Skipping config bootstrap because ${SETTINGS_FILE} does not exist"
    exit 0
fi

CURRENT_LOCAL_PATH="$(grep -E '^[[:space:]]*local_path[[:space:]]*=' "${SETTINGS_FILE}" | head -n 1 | sed -E 's/^[[:space:]]*local_path[[:space:]]*=[[:space:]]*//' | tr -d '\r')"

case "${CURRENT_LOCAL_PATH}" in
    ""|"<replace me>"|"<replace me>/")
        if grep -Eq '^[[:space:]]*local_path[[:space:]]*=' "${SETTINGS_FILE}"; then
            echo "Setting local_path to ${DEFAULT_LOCAL_PATH}"
            replace_local_path
        else
            echo "Adding local_path to the [Lftp] section"
            append_local_path_to_lftp_section
        fi
        ;;
    *)
        echo "Keeping existing local_path from settings.cfg"
        ;;
esac
