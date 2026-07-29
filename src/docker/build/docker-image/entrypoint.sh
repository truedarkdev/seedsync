#!/bin/bash

set -euo pipefail
umask 077

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

prepare_config_root() {
    local config_root="${1:-$CONFIG_DIR}"
    local test_delay_seconds="${2:-0}"
    local repair_test_delay_seconds="${3:-0}"
    python3 - "$config_root" "$USER_ID" "$GROUP_ID" "$DEFAULT_ID" "$test_delay_seconds" "$repair_test_delay_seconds" <<'PY'
import os
import re
import stat
import sys
import time

root, user_id_text, group_id_text, default_id_text, test_delay_text, repair_test_delay_text = sys.argv[1:]
uid, gid, default_uid = int(user_id_text), int(group_id_text), int(default_id_text)
try:
    test_delay_seconds = int(test_delay_text)
    repair_test_delay_seconds = int(repair_test_delay_text)
except ValueError:
    test_delay_seconds = repair_test_delay_seconds = -1
allowed_filesystems = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "tmpfs", "overlay"}
rejected_prefixes = ("fuse",)
rejected_filesystems = {"9p", "v9fs", "virtiofs", "cifs", "smb", "smb2", "smb3", "nfs", "nfs4", "vboxsf", "drvfs"}

def fail(reason):
    raise SystemExit(
        "ERROR: {} config-root contract failed: {}. Use a Docker named volume or a local POSIX "
        "filesystem ({}) owned by UID={}, GID={} with mode 0700; Windows/DrvFS, network, "
        "and shared filesystems are unsupported for /config.".format(
            root, reason, ", ".join(sorted(allowed_filesystems)), uid, gid,
        )
    )

def fail_after_access_revocation(reason):
    fail("{}; root access was already revoked and the anchored root remains mode 0000 for administrator recovery".format(reason))

def decode_mount_path(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)

def filesystem_type(path):
    normalized = os.path.normpath(path)
    winner = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            for line in stream:
                fields = line.rstrip("\n").split()
                separator = fields.index("-")
                mount_path = decode_mount_path(fields[4])
                if normalized == mount_path or normalized.startswith(mount_path.rstrip("/") + "/"):
                    if winner is None or len(mount_path) > len(winner[0]):
                        winner = (mount_path, fields[separator + 1])
    except (OSError, ValueError, IndexError):
        fail("unable to determine filesystem type")
    if winner is None:
        fail("unable to determine filesystem type")
    return winner[1]

def reject_nested_mounts(path):
    normalized = os.path.normpath(path)
    prefix = normalized.rstrip("/") + "/"
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            for line in stream:
                fields = line.rstrip("\n").split()
                mount_path = decode_mount_path(fields[4])
                if mount_path.startswith(prefix):
                    fail("{} contains a nested mount or device".format(mount_path))
    except (OSError, IndexError):
        fail("unable to determine nested mount topology")

def require_directory(fd, relative):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        fail("{} is not a directory".format(relative))
    return info

def require_unlinked_regular(info, root_device, relative):
    if not stat.S_ISREG(info.st_mode):
        fail("{} is not a regular file".format(relative))
    if info.st_dev != root_device:
        fail("{} crosses into a nested mount or device".format(relative))
    if info.st_nlink != 1:
        fail("{} has link count {}; regular config files must not have hard links".format(relative, info.st_nlink))

def require_admitted_descendant_owner(info, relative):
    if info.st_uid not in (0, default_uid, uid):
        fail(
            "{} owner UID {} is neither trusted root, the image default UID {}, nor the runtime UID {}; refusing before ownership repair. "
            "Restore the config from a trusted backup or repair its ownership before starting SeedSync".format(
                relative, info.st_uid, default_uid, uid,
            )
        )

def require_same_regular_identity(relative, root_device, *infos):
    for info in infos:
        require_unlinked_regular(info, root_device, relative)
        require_admitted_descendant_owner(info, relative)
    reference = infos[0]
    for info in infos[1:]:
        if (info.st_dev, info.st_ino) != (reference.st_dev, reference.st_ino):
            fail("{} changed identity while ownership was being repaired".format(relative))

def require_same_directory_identity(relative, root_device, *infos):
    for info in infos:
        if not stat.S_ISDIR(info.st_mode):
            fail("{} is not a directory".format(relative))
        if info.st_dev != root_device:
            fail("{} crosses into a nested mount or device".format(relative))
        require_admitted_descendant_owner(info, relative)
    reference = infos[0]
    for info in infos[1:]:
        if (info.st_dev, info.st_ino) != (reference.st_dev, reference.st_ino):
            fail("{} changed identity while ownership was being repaired".format(relative))

def require_revoked_root_state(root_fd, initial_info, expected_uid, expected_gid, phase):
    descriptor_info = os.fstat(root_fd)
    path_info = os.lstat(root)
    if (
        not stat.S_ISDIR(descriptor_info.st_mode)
        or stat.S_IMODE(descriptor_info.st_mode) != 0
        or descriptor_info.st_uid != expected_uid
        or descriptor_info.st_gid != expected_gid
        or (descriptor_info.st_dev, descriptor_info.st_ino) != (initial_info.st_dev, initial_info.st_ino)
        or (path_info.st_dev, path_info.st_ino) != (descriptor_info.st_dev, descriptor_info.st_ino)
    ):
        fail_after_access_revocation("root identity, owner, or mode changed {}".format(phase))

def require_admitted_root_owner(root_info):
    if root_info.st_uid not in (0, uid):
        fail(
            "root owner UID {} is neither trusted root nor the runtime UID {}; refusing before any ownership or tree mutation. "
            "On Linux, repair the root with sudo chown {}:{} {} and sudo chmod 700 {}; on Windows, use the named-volume Compose override".format(
                root_info.st_uid, uid, uid, gid, root, root,
            )
        )
    if stat.S_IMODE(root_info.st_mode) & 0o022:
        fail(
            "root mode {:04o} grants group or other write access; refusing before any tree read or mutation. "
            "On Linux, run sudo chmod go-w {} and sudo chown {}:{} {}; on Windows, use the named-volume Compose override".format(
                stat.S_IMODE(root_info.st_mode), root, uid, gid, root,
            )
        )

def validate_tree(directory_fd, root_device, relative="."):
    for entry in os.scandir(directory_fd):
        child_relative = entry.name if relative == "." else relative + "/" + entry.name
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            fail("{} contains a symlink".format(child_relative))
        if info.st_dev != root_device:
            fail("{} crosses into a nested mount or device".format(child_relative))
        require_admitted_descendant_owner(info, child_relative)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(
                entry.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                child_info = require_directory(child_fd, child_relative)
                if child_info.st_dev != root_device:
                    fail("{} crosses into a nested mount or device".format(child_relative))
                require_admitted_descendant_owner(child_info, child_relative)
                validate_tree(child_fd, root_device, child_relative)
            finally:
                os.close(child_fd)
        else:
            require_unlinked_regular(info, root_device, child_relative)
            require_admitted_descendant_owner(info, child_relative)

def repair_tree(directory_fd, root_device):
    for entry in os.scandir(directory_fd):
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or info.st_dev != root_device:
            fail("configuration tree changed while ownership was being repaired")
        if stat.S_ISDIR(info.st_mode):
            try:
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                fail("{} cannot be opened without following links ({})".format(entry.name, exc.__class__.__name__))
            try:
                child_info = require_directory(child_fd, entry.name)
                current_name_info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                require_same_directory_identity(entry.name, root_device, info, child_info, current_name_info)
                os.fchown(child_fd, uid, gid)
                final_child_info = os.fstat(child_fd)
                final_name_info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                require_same_directory_identity(entry.name, root_device, final_child_info, final_name_info)
                repair_tree(child_fd, root_device)
            finally:
                os.close(child_fd)
        else:
            require_unlinked_regular(info, root_device, entry.name)
            file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if hasattr(os, "O_CLOEXEC"):
                file_flags |= os.O_CLOEXEC
            try:
                file_fd = os.open(entry.name, file_flags, dir_fd=directory_fd)
            except OSError as exc:
                fail("{} cannot be opened without following links ({})".format(entry.name, exc.__class__.__name__))
            try:
                descriptor_info = os.fstat(file_fd)
                name_info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                require_same_regular_identity(entry.name, root_device, info, descriptor_info, name_info)
                os.fchown(file_fd, uid, gid)
                final_descriptor_info = os.fstat(file_fd)
                final_name_info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                require_same_regular_identity(entry.name, root_device, final_descriptor_info, final_name_info)
            finally:
                os.close(file_fd)

if os.path.islink(root):
    fail("root is a symlink")
try:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
except OSError as exc:
    fail("cannot open a real root directory ({})".format(exc.__class__.__name__))
try:
    root_info = require_directory(root_fd, ".")
    filesystem = filesystem_type(root)
    if filesystem in rejected_filesystems or filesystem.startswith(rejected_prefixes) or filesystem not in allowed_filesystems:
        fail("filesystem type {} is not an allowed local POSIX filesystem".format(filesystem))
    reject_nested_mounts(root)
    require_admitted_root_owner(root_info)
    if test_delay_seconds < 0 or test_delay_seconds > 10 or repair_test_delay_seconds < 0 or repair_test_delay_seconds > 10:
        fail("config-only test delays must be integers from 0 through 10 seconds")
    try:
        os.fchmod(root_fd, 0)
    except OSError as exc:
        fail("cannot revoke root access before ownership transition ({})".format(exc.__class__.__name__))
    require_revoked_root_state(root_fd, root_info, root_info.st_uid, root_info.st_gid, "after access revocation")
    # This closes new pathname traversal for every UID, including the runtime
    # UID after root ownership changes. It cannot revoke a descriptor that a
    # same-UID process opened before preparation, so callers must not share
    # writable config descriptors with untrusted processes during startup.
    if test_delay_seconds:
        time.sleep(test_delay_seconds)
        require_revoked_root_state(root_fd, root_info, root_info.st_uid, root_info.st_gid, "during the bounded config-only test delay")
    validate_tree(root_fd, root_info.st_dev)
    if root_info.st_uid == 0 or root_info.st_gid != gid:
        try:
            os.fchown(root_fd, uid, gid)
        except OSError as exc:
            fail_after_access_revocation("cannot assign the runtime owner ({})".format(exc.__class__.__name__))
    require_revoked_root_state(root_fd, root_info, uid, gid, "after ownership transition")
    if repair_test_delay_seconds:
        time.sleep(repair_test_delay_seconds)
        require_revoked_root_state(root_fd, root_info, uid, gid, "during the bounded repair-phase test delay")
    repair_tree(root_fd, root_info.st_dev)
    validate_tree(root_fd, root_info.st_dev)
    path_info = os.lstat(root)
    final_info = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(final_info.st_mode)
        or final_info.st_uid != uid
        or final_info.st_gid != gid
        or stat.S_IMODE(final_info.st_mode) != 0
        or (path_info.st_dev, path_info.st_ino) != (final_info.st_dev, final_info.st_ino)
    ):
        fail_after_access_revocation("root owner, mode, or identity changed before access restoration")
    try:
        os.fchmod(root_fd, 0o700)
    except OSError as exc:
        fail_after_access_revocation("cannot restore runtime-private root access ({})".format(exc.__class__.__name__))
    final_info = os.fstat(root_fd)
    if stat.S_IMODE(final_info.st_mode) != 0o700:
        fail("root mode did not restore to runtime-private access")
finally:
    os.close(root_fd)
print("Verified config root: {} filesystem={} owner={}:{} mode=700".format(root, filesystem, uid, gid), file=sys.stderr)
PY
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

if [ "${1:-}" = "--prepare-config-root" ]; then
    config_root_test_delay="${SEEDSYNC_CONFIG_ROOT_TEST_DELAY_SECONDS:-0}"
    config_root_repair_test_delay="${SEEDSYNC_CONFIG_ROOT_REPAIR_TEST_DELAY_SECONDS:-0}"
    case "$config_root_test_delay" in
        (''|*[!0-9]*)
            printf 'ERROR: SEEDSYNC_CONFIG_ROOT_TEST_DELAY_SECONDS must be an integer from 0 through 10 for --prepare-config-root\n' >&2
            exit 1
            ;;
    esac
    if [ "$config_root_test_delay" -gt 10 ]; then
        printf 'ERROR: SEEDSYNC_CONFIG_ROOT_TEST_DELAY_SECONDS must be an integer from 0 through 10 for --prepare-config-root\n' >&2
        exit 1
    fi
    case "$config_root_repair_test_delay" in
        (''|*[!0-9]*)
            printf 'ERROR: SEEDSYNC_CONFIG_ROOT_REPAIR_TEST_DELAY_SECONDS must be an integer from 0 through 10 for --prepare-config-root\n' >&2
            exit 1
            ;;
    esac
    if [ "$config_root_repair_test_delay" -gt 10 ]; then
        printf 'ERROR: SEEDSYNC_CONFIG_ROOT_REPAIR_TEST_DELAY_SECONDS must be an integer from 0 through 10 for --prepare-config-root\n' >&2
        exit 1
    fi
    prepare_config_root "${2:-$CONFIG_DIR}" "$config_root_test_delay" "$config_root_repair_test_delay"
    exit 0
fi

GROUP_NAME="$(ensure_group "$GROUP_ID")"
USER_NAME="$APP_USER"
ensure_user "$USER_ID" "$GROUP_NAME"

# Keep the bootstrap config and the runtime home writable, but avoid recursing
# through large mounted trees unless their ownership really needs to be fixed.
mkdir -p "$CONFIG_DIR"
prepare_config_root
mkdir -p "$DOWNLOADS_DIR" "$MOUNTS_DIR" "$USER_HOME"
mkdir -p "$USER_HOME/.ssh"
mkdir -p /staging
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
