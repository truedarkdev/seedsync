#!/bin/bash

set -euo pipefail

DEFAULT_ID=1000
APP_USER=seedsync
APP_HOME_DIR=/home/seedsync
CONFIG_DIR=/config
SETTINGS_FILE="${CONFIG_DIR}/settings.cfg"
SCRIPT_PATH="/app/python/seedsync.py"
DEFAULT_LOCAL_PATH="/downloads/"
DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION="${SEEDSYNC_BROWSER_HANDOVER_RECOVERY_VERSION:-}"
DOWNLOADS_DIR=/downloads
MOUNTS_DIR=/mounts
USER_HOME="$APP_HOME_DIR"

bootstrap_default_config() {
    if [ ! -f "${SETTINGS_FILE}" ]; then
        echo "Generating default SeedSync config in ${CONFIG_DIR}"
    fi

    generate_default_config

    if [ ! -f "${SETTINGS_FILE}" ]; then
        echo "Skipping config bootstrap because ${SETTINGS_FILE} does not exist"
        return 0
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

    if [ -n "${DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION}" ]; then
        if grep -Eq '^[[:space:]]*browser_handover_recovery_version[[:space:]]*=' "${SETTINGS_FILE}"; then
            echo "Setting browser_handover_recovery_version to ${DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION}"
            replace_browser_handover_recovery_version
        fi
    fi
}

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

replace_browser_handover_recovery_version() {
    sed -i -E "s|^[[:space:]]*browser_handover_recovery_version[[:space:]]*=.*$|browser_handover_recovery_version = ${DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION}|" "${SETTINGS_FILE}"
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

ensure_ssh_host_key_config() {
    local ssh_config="${USER_HOME}/.ssh/config"

    if [ ! -f "${ssh_config}" ]; then
        printf '%s\n' "StrictHostKeyChecking accept-new" > "${ssh_config}"
    elif ! grep -Eq '^[[:space:]]*StrictHostKeyChecking[[:space:]]+' "${ssh_config}"; then
        if [ -s "${ssh_config}" ]; then
            printf '\n%s\n' "StrictHostKeyChecking accept-new" >> "${ssh_config}"
        else
            printf '%s\n' "StrictHostKeyChecking accept-new" > "${ssh_config}"
        fi
    fi

    safe_chown "home SSH config" "${ssh_config}"
    chmod 600 "${ssh_config}" 2>/dev/null || true
}

export CONFIG_DIR SETTINGS_FILE SCRIPT_PATH DEFAULT_LOCAL_PATH DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION

if [ "${1:-}" = "--bootstrap-default-config" ]; then
    bootstrap_default_config
    exit 0
fi

validate_id() {
    local label="$1"
    local value="$2"

    case "$value" in
        ""|*[!0-9]*)
            echo "ERROR: ${label} must be a non-empty numeric ID" >&2
            exit 1
            ;;
    esac

    if [ "$value" -eq 0 ]; then
        echo "ERROR: ${label} must not be 0" >&2
        exit 1
    fi
}

resolve_id() {
    local env_name="$1"
    local default_value="$2"

    if [ "${!env_name+x}" != x ]; then
        printf '%s\n' "$default_value"
        return
    fi

    local value="${!env_name}"
    validate_id "$env_name" "$value"
    printf '%s\n' "$value"
}

ensure_group() {
    local group_id="$1"
    local group_name

    group_name="$(getent group "$group_id" | cut -d: -f1 || true)"
    if [ -n "$group_name" ]; then
        echo "Using existing group: $group_name (GID=$group_id)" >&2
        printf '%s\n' "$group_name"
        return
    fi

    if getent group "$APP_USER" >/dev/null 2>&1; then
        echo "Updating group $APP_USER to GID=$group_id" >&2
        groupmod -g "$group_id" "$APP_USER"
    else
        echo "Creating group: $APP_USER (GID=$group_id)" >&2
        groupadd -g "$group_id" "$APP_USER"
    fi

    printf '%s\n' "$APP_USER"
}

ensure_user() {
    local user_id="$1"
    local group_name="$2"
    local user_name
    local user_home

    user_name="$(getent passwd "$user_id" | cut -d: -f1 || true)"
    if [ -n "$user_name" ]; then
        user_home="$(getent passwd "$user_name" | cut -d: -f6 || true)"
        if [ -z "$user_home" ]; then
            user_home="$APP_HOME_DIR"
        fi
        USER_HOME="$user_home"
        USER_NAME="$user_name"
        echo "Using existing user: $user_name (UID=$user_id, HOME=$USER_HOME)" >&2
        return
    fi

    if getent passwd "$APP_USER" >/dev/null 2>&1; then
        echo "Updating user $APP_USER to UID=$user_id and GID=$group_name" >&2
        usermod -u "$user_id" -g "$group_name" "$APP_USER"
    else
        echo "Creating user: $APP_USER (UID=$user_id, GID=$group_name, HOME=$APP_HOME_DIR)" >&2
        useradd -u "$user_id" -g "$group_name" -d "$APP_HOME_DIR" -m -s /bin/bash "$APP_USER"
    fi

    USER_HOME="$APP_HOME_DIR"
    USER_NAME="$APP_USER"
}

safe_chown() {
    local label="$1"
    shift

    if chown "$USER_ID:$GROUP_ID" "$@" 2>/dev/null; then
        echo "Updated ownership: $label" >&2
    else
        echo "Skipping ownership update for $label (permission denied or unsupported)" >&2
    fi
}

safe_chown_recursive() {
    local label="$1"
    shift

    if chown -R "$USER_ID:$GROUP_ID" "$@" 2>/dev/null; then
        echo "Updated ownership: $label" >&2
    else
        echo "Skipping ownership update for $label (permission denied or unsupported)" >&2
    fi
}

check_writable_path() {
    local path="$1"

    if ! setpriv --reuid="$USER_ID" --regid="$GROUP_ID" --clear-groups -- bash -c '
        path="$1"
        test_file=$(mktemp "$path/.seedsync_write_test.XXXXXX") || {
            printf "ERROR: failed to create writable-path probe under %s\n" "$path" >&2
            exit 1
        }
        rm -f -- "$test_file" || {
            printf "ERROR: failed to remove writable-path probe %s under %s\n" "$test_file" "$path" >&2
            exit 1
        }
    ' bash "$path"; then
        echo "ERROR: $path is not writable by $USER_NAME:$GROUP_NAME (UID=$USER_ID,GID=$GROUP_ID)" >&2
        exit 1
    fi
}

validate_umask() {
    local umask_value="$1"

    case "$umask_value" in
        (*[!0-7]*)
            printf 'ERROR: invalid UMASK value %s; expected octal digits 0-7\n' "$umask_value" >&2
            exit 1
            ;;
    esac
}

if [ -n "${UMASK:-}" ]; then
    validate_umask "$UMASK"
    umask "$UMASK"
fi

USER_ID="$(resolve_id PUID "$DEFAULT_ID")"
GROUP_ID="$(resolve_id PGID "$DEFAULT_ID")"
GROUP_NAME="$(ensure_group "$GROUP_ID")"
USER_NAME="$APP_USER"
ensure_user "$USER_ID" "$GROUP_NAME"

# Keep the bootstrap config and the runtime home writable, but avoid recursing
# through large mounted trees unless their ownership really needs to be fixed.
mkdir -p "$CONFIG_DIR" "$DOWNLOADS_DIR" "$MOUNTS_DIR" "$USER_HOME"
mkdir -p "$USER_HOME/.ssh"
mkdir -p /staging
safe_chown_recursive "config directory" "$CONFIG_DIR"
safe_chown "home directory" "$USER_HOME"
safe_chown_recursive "home SSH directory" "$USER_HOME/.ssh"
safe_chown "downloads directory" "$DOWNLOADS_DIR"
safe_chown "mounts directory" "$MOUNTS_DIR"
safe_chown "staging directory" /staging
chmod 700 "$USER_HOME/.ssh" 2>/dev/null || true
ensure_ssh_host_key_config

check_writable_path "$DOWNLOADS_DIR"
if mountpoint -q /staging 2>/dev/null; then
    check_writable_path /staging
fi

unset BASH_ENV ENV

export HOME="$USER_HOME"
echo "Running as: $USER_NAME:$GROUP_NAME (UID=$USER_ID, GID=$GROUP_ID, HOME=$HOME)" >&2

export -f append_local_path_to_lftp_section bootstrap_default_config generate_default_config replace_browser_handover_recovery_version replace_local_path

# Keep bootstrap and command/argument forwarding in the existing non-root
# shell, but make tini the resulting PID 1 so it forwards signals and reaps
# children spawned by the application.
exec setpriv --reuid="$USER_ID" --regid="$GROUP_ID" --clear-groups -- tini -g -- bash -lc 'set -euo pipefail; bootstrap_default_config; exec "$@"' bash "$@"
