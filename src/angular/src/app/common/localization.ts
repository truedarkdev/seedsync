export class Localization {
    static escapeHtml(value: string): string {
        const replacements: Record<string, string> = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            "\"": "&quot;",
            "'": "&#39;"
        };
        return value.replace(/[&<>"']/g, character => replacements[character]);
    }

    static Error = class {
        public static readonly SERVER_DISCONNECTED = "Lost connection to the SeedSync service.";
    };

    static Notification = class {
        public static readonly CONFIG_RESTART = "Restart the app to apply new settings.";
        public static readonly CONFIG_APPLIED_IMMEDIATELY = "Setting applied immediately.";
        public static readonly CONFIG_VALUE_BLANK =
            (section: string, option: string) => `Setting ${section}.${option} cannot be blank.`
        public static readonly FTPS_TRANSFER_PASSWORD_REQUIRED = "FTPS requires a transfer password.";

        public static readonly AUTOQUEUE_PATTERN_EMPTY = "Cannot add an empty autoqueue pattern.";

        public static readonly STATUS_CONNECTION_WAITING = "Waiting for SeedSync service to respond...";
        public static readonly STATUS_LOG_WAITING = "Connected to SeedSync service. Waiting for log events...";
        public static readonly STATUS_REMOTE_SCAN_WAITING = "Waiting for remote server to respond...";
        public static readonly STATUS_REMOTE_SERVER_ERROR = (error: string) =>
            `Lost connection to remote server. Retrying automatically. \
             ${error ? "<br />" + error : ""}`

        public static readonly NEW_VERSION_AVAILABLE = (url: string) =>
            `A new version of SeedSync is available! \
             Click <a href="${url}" target="blank">here</a> to grab the latest version.`
    };

    static Modal = class {
        public static readonly DELETE_LOCAL_TITLE = "Delete Local File";
        public static readonly DELETE_LOCAL_MESSAGE =
            (name: string) => `Are you sure you want to delete <b>${Localization.escapeHtml(name)}</b> from the local server?`

        public static readonly DELETE_REMOTE_TITLE = "Delete Remote File";
        public static readonly DELETE_REMOTE_MESSAGE =
            (name: string) => `Are you sure you want to delete <b>${Localization.escapeHtml(name)}</b> from the remote server?`

        public static readonly DELETE_PATH_PAIR_TITLE = "Delete Path Pair";
        public static readonly DELETE_PATH_PAIR_MESSAGE =
            (name: string) => `Are you sure you want to delete path pair <b>${Localization.escapeHtml(name)}</b>?`

        public static readonly DELETE_LOCAL_BULK_TITLE = "Delete Local Files";
        public static readonly DELETE_LOCAL_BULK_MESSAGE =
            (names: string[]) => Localization.Modal.BULK_DELETE_MESSAGE(names, "local server")

        public static readonly DELETE_REMOTE_BULK_TITLE = "Delete Remote Files";
        public static readonly DELETE_REMOTE_BULK_MESSAGE =
            (names: string[]) => Localization.Modal.BULK_DELETE_MESSAGE(names, "remote server")

        private static readonly BULK_DELETE_MESSAGE = (names: string[], location: string) => {
            const listedNames = names.slice(0, 5)
                .map(name => `<li><b>${Localization.escapeHtml(name)}</b></li>`)
                .join("");
            const remainingCount = names.length - Math.min(names.length, 5);
            const remainingText = remainingCount > 0 ? `<br />And ${remainingCount} more file(s).` : "";
            return `Are you sure you want to delete ${names.length} selected file(s) from the ${location}?` +
                `<ul>${listedNames}</ul>${remainingText}`;
        }

        public static readonly RESTART_TITLE = "Restart SeedSync";
        public static readonly RESTART_MESSAGE =
            "Are you sure you want to restart the server?<br /><br />" +
            "Active downloads will pause briefly while the page reconnects."
    };

    static Log = class {
        public static readonly CONNECTED = "Connected to service";
        public static readonly DISCONNECTED = "Lost connection to service";
    };
}
