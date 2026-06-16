#!/bin/bash

set -euo pipefail

DEFAULT_ID=1000
APP_USER=seedsync
APP_HOME_DIR=/home/seedsync
CONFIG_DIR=/config
DOWNLOADS_DIR=/downloads
MOUNTS_DIR=/mounts
USER_HOME="$APP_HOME_DIR"

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

USER_ID="$(resolve_id PUID "$DEFAULT_ID")"
GROUP_ID="$(resolve_id PGID "$DEFAULT_ID")"
GROUP_NAME="$(ensure_group "$GROUP_ID")"
USER_NAME="$APP_USER"
ensure_user "$USER_ID" "$GROUP_NAME"

# Keep the bootstrap config and the runtime home writable, but avoid recursing
# through large mounted trees unless their ownership really needs to be fixed.
mkdir -p "$CONFIG_DIR" "$DOWNLOADS_DIR" "$MOUNTS_DIR" "$USER_HOME"
mkdir -p "$USER_HOME/.ssh"
safe_chown_recursive "config directory" "$CONFIG_DIR"
safe_chown "home directory" "$USER_HOME"
safe_chown_recursive "home SSH directory" "$USER_HOME/.ssh"
safe_chown "downloads directory" "$DOWNLOADS_DIR"
safe_chown "mounts directory" "$MOUNTS_DIR"
chmod 700 "$USER_HOME/.ssh" 2>/dev/null || true

export HOME="$USER_HOME"
echo "Running as: $USER_NAME:$GROUP_NAME (UID=$USER_ID, GID=$GROUP_ID, HOME=$HOME)" >&2

if [ -n "${UMASK:-}" ]; then
    umask "$UMASK"
fi

exec gosu "$USER_NAME:$GROUP_NAME" "$@"
