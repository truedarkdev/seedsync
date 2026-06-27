import {OptionType} from "./option.component";

export interface IOption {
    type: OptionType;
    label: string;
    valuePath: [string, string];
    description: string | null;
    choices?: {label: string; value: any;}[];
    disabledWhenSftp?: boolean;
    disabledWhenTransferBackend?: string[];
    disabled?: boolean;
}
export interface IOptionsGroup {
    header: string;
    options: IOption[];
}
export interface IOptionsContext {
    header: string;
    id: string;
    options: IOption[];
    groups?: IOptionsGroup[];
}

export const OPTIONS_CONTEXT_SERVER: IOptionsContext = {
    header: "Server",
    id: "server",
    options: [
        {
            type: OptionType.Text,
            label: "Server Address",
            valuePath: ["lftp", "remote_address"],
            description: null
        },
        {
            type: OptionType.Text,
            label: "Server User",
            valuePath: ["lftp", "remote_username"],
            description: null
        },
        {
            type: OptionType.Password,
            label: "Server Password",
            valuePath: ["lftp", "remote_password"],
            description: "Leave blank when using SSH key authentication. FTPS requires a transfer password."
        },
        {
            type: OptionType.Checkbox,
            label: "Use password-less key-based authentication",
            valuePath: ["lftp", "use_ssh_key"],
            description: null
        },
        {
            type: OptionType.Text,
            label: "Server Directory",
            valuePath: ["lftp", "remote_path"],
            description: "Path to your files on the remote server."
        },
        {
            type: OptionType.Text,
            label: "Local Directory",
            valuePath: ["lftp", "local_path"],
            description: "Downloaded files are placed here."
        },
        {
            type: OptionType.Text,
            label: "Remote SSH Port",
            valuePath: ["lftp", "remote_port"],
            description: null,
        },
        {
            type: OptionType.Text,
            label: "Server Script Path",
            valuePath: ["lftp", "remote_path_to_scan_script"],
            description: "Where to install scanner script on remote server"
        },
        {
            type: OptionType.Text,
            label: "Remote Python Path",
            valuePath: ["lftp", "remote_python_path"],
            description: "Defaults to python3; leave blank to use the default."
        }
    ]
};

export const OPTIONS_CONTEXT_TRANSFER_PROTOCOL: IOptionsContext = {
    header: "Transfer Protocol",
    id: "transfer-protocol",
    options: [
        {
            type: OptionType.Select,
            label: "Transfer Backend",
            valuePath: ["lftp", "transfer_backend"],
            description: "Choose the transfer engine. LFTP remains the default; rclone currently supports SFTP only.",
            choices: [
                {label: "LFTP", value: "lftp"},
                {label: "rclone", value: "rclone"},
            ],
        },
        {
            type: OptionType.Select,
            label: "Transfer Protocol",
            valuePath: ["lftp", "protocol"],
            description: "SFTP is the default. FTPS applies only to bulk file transfers; file discovery still uses SSH.",
            choices: [
                {label: "SFTP", value: "sftp"},
                {label: "FTPS", value: "ftps"},
            ],
            disabledWhenTransferBackend: ["rclone"],
        },
        {
            type: OptionType.Text,
            label: "Remote FTP Port",
            valuePath: ["lftp", "remote_ftp_port"],
            description: "Used only when Transfer Protocol is FTPS.",
            disabledWhenSftp: true,
            disabledWhenTransferBackend: ["rclone"],
        },
        {
            type: OptionType.Checkbox,
            label: "Verify FTPS certificate",
            valuePath: ["lftp", "ftp_ssl_verify_certificate"],
            description: "Recommended. Turn off only for self-signed or legacy servers.",
            disabledWhenSftp: true,
            disabledWhenTransferBackend: ["rclone"],
        },
    ]
};

export const OPTIONS_CONTEXT_DISCOVERY: IOptionsContext = {
    header: "File Discovery",
    id: "file-discovery",
    options: [
        {
            type: OptionType.Text,
            label: "Exclude Patterns",
            valuePath: ["general", "exclude_patterns"],
            description: "Comma-separated glob patterns for remote files to skip. Matching files are hidden from the UI and never downloaded. Use a trailing slash for directory-only matches, like Sample/."
        },
        {
            type: OptionType.Text,
            label: "Remote Scan Interval (ms)",
            valuePath: ["controller", "interval_ms_remote_scan"],
            description: "How often the remote server is scanned for new files"
        },
        {
            type: OptionType.Text,
            label: "Local Scan Interval (ms)",
            valuePath: ["controller", "interval_ms_local_scan"],
            description: "How often the local directory is scanned"
        },
        {
            type: OptionType.Text,
            label: "Downloading Scan Interval (ms)",
            valuePath: ["controller", "interval_ms_downloading_scan"],
            description: "How often the downloading information is updated"
        },
    ]
};

export const OPTIONS_CONTEXT_CONNECTIONS: IOptionsContext = {
    header: "Connections",
    id: "connections",
    options: [
        {
            type: OptionType.Text,
            label: "Max Parallel Downloads",
            valuePath: ["lftp", "num_max_parallel_downloads"],
            description: "How many items download in parallel.\n" +
                         "(cmd:queue-parallel)"
        },
        {
            type: OptionType.Text,
            label: "Max Total Connections",
            valuePath: ["lftp", "num_max_total_connections"],
            description: "Maximum number of LFTP connections. Values above 32 are not recommended " +
                         "because some systems can hit select() file descriptor limits. " +
                         "Use 0 for no limit.\n" +
                         "(net:connection-limit)"
        },
        {
            type: OptionType.Text,
            label: "Max Connections Per File (Single-File)",
            valuePath: ["lftp", "num_max_connections_per_root_file"],
            description: "Number of connections for single-file download.\n" +
                         "(pget:default-n)"
        },
        {
            type: OptionType.Text,
            label: "Max Connections Per File (Directory)",
            valuePath: ["lftp", "num_max_connections_per_dir_file"],
            description: "Number of per-file connections for directory download.\n" +
                         "(mirror:use-pget-n)"
        },
        {
            type: OptionType.Text,
            label: "Max Parallel Files (Directory)",
            valuePath: ["lftp", "num_max_parallel_files_per_download"],
            description: "Maximum number of files to fetch in parallel for single directory download.\n" +
                         "(mirror:parallel-transfer-count)"
        },
        {
            type: OptionType.Checkbox,
            label: "Rename unfinished/downloading files",
            valuePath: ["lftp", "use_temp_file"],
            description: "Unfinished and downloading files will be named *.lftp"
        },
        {
            type: OptionType.Text,
            label: "Download Rate Limit",
            valuePath: ["lftp", "rate_limit"],
            description: "Limit download speed. Use 0 for unlimited, or values like 1M or 500K.\n" +
                         "(net:limit-rate)"
        },
        {
            type: OptionType.Text,
            label: "Socket Buffer",
            valuePath: ["lftp", "net_socket_buffer"],
            description: "Set LFTP's socket buffer size. Use values like 512K or 8M. Leave blank to skip setting net:socket-buffer.\n" +
                         "(net:socket-buffer)"
        },
        {
            type: OptionType.Text,
            label: "Staging Directory",
            valuePath: ["lftp", "staging_path"],
            description: "Optional directory for in-progress downloads. Leave empty to use the Local Directory plus /incomplete."
        },
    ]
};

export const OPTIONS_CONTEXT_OTHER: IOptionsContext = {
    header: "Other Settings",
    id: "other-settings",
    groups: [
        {
            header: "Diagnostics",
            options: [
                {
                    type: OptionType.Select,
                    label: "Log Level",
                    valuePath: ["general", "log_level"],
                    description: "Controls how much detail is written to the logs.",
                    choices: [
                        {label: "Debug", value: "DEBUG"},
                        {label: "Info", value: "INFO"},
                        {label: "Warning", value: "WARNING"},
                        {label: "Error", value: "ERROR"},
                    ]
                },
                {
                    type: OptionType.Checkbox,
                    label: "Verbose LFTP Logging",
                    valuePath: ["general", "verbose"],
                    description: "Logs all LFTP command output. Set Log Level to Debug to see it."
                },
                {
                    type: OptionType.Select,
                    label: "Log Format",
                    valuePath: ["logging", "log_format"],
                    description: "Choose Standard or JSON log output.",
                    choices: [
                        {label: "Standard", value: "standard"},
                        {label: "JSON", value: "json"},
                    ]
                },
                {
                    type: OptionType.Checkbox,
                    label: "Enable breadcrumb trace recorder",
                    valuePath: ["general", "breadcrumb_trace_enabled"],
                    description: "Keeps a low-overhead recent-context window for debugging failures."
                },
            ]
        },
        {
            header: "Validation",
            options: [
                {
                    type: OptionType.Checkbox,
                    label: "Verify transfers inline (recommended)",
                    valuePath: ["validate", "xfer_verify"],
                    description: "Use lftp xfer:verify to validate files during transfer with the same hash command used by local validation."
                },
            ]
        },
        {
            header: "Application",
            options: [
                {
                    type: OptionType.Text,
                    label: "Web GUI Port",
                    valuePath: ["web", "port"],
                    description: null
                },
            ]
        },
    ],
    options: [
        {
            type: OptionType.Select,
            label: "Log Level",
            valuePath: ["general", "log_level"],
            description: "Controls how much detail is written to the logs.",
            choices: [
                {label: "Debug", value: "DEBUG"},
                {label: "Info", value: "INFO"},
                {label: "Warning", value: "WARNING"},
                {label: "Error", value: "ERROR"},
            ]
        },
        {
            type: OptionType.Checkbox,
            label: "Verbose LFTP Logging",
            valuePath: ["general", "verbose"],
            description: "Logs all LFTP command output. Set Log Level to Debug to see it."
        },
        {
            type: OptionType.Select,
            label: "Log Format",
            valuePath: ["logging", "log_format"],
            description: "Choose Standard or JSON log output.",
            choices: [
                {label: "Standard", value: "standard"},
                {label: "JSON", value: "json"},
            ]
        },
        {
            type: OptionType.Checkbox,
            label: "Enable breadcrumb trace recorder",
            valuePath: ["general", "breadcrumb_trace_enabled"],
            description: "Keeps a low-overhead recent-context window for debugging failures."
        },
        {
            type: OptionType.Checkbox,
            label: "Verify transfers inline (recommended)",
            valuePath: ["validate", "xfer_verify"],
            description: "Use lftp xfer:verify to validate files during transfer with the same hash command used by local validation."
        },
        {
            type: OptionType.Text,
            label: "Web GUI Port",
            valuePath: ["web", "port"],
            description: null
        },
    ]
};

export const OPTIONS_CONTEXT_AUTOQUEUE: IOptionsContext = {
    header: "AutoQueue",
    id: "autoqueue",
    options: [
        {
            type: OptionType.Checkbox,
            label: "Enable AutoQueue",
            valuePath: ["autoqueue", "enabled"],
            description: null
        },
        {
            type: OptionType.Checkbox,
            label: "Restrict to patterns",
            valuePath: ["autoqueue", "patterns_only"],
            description: "Only autoqueue files that match a pattern"
        },
        {
            type: OptionType.Checkbox,
            label: "Enable auto extraction",
            valuePath: ["autoqueue", "auto_extract"],
            description: "Automatically extract files"
        },
        {
            type: OptionType.Checkbox,
            label: "Delete remote files after download",
            valuePath: ["autoqueue", "auto_delete_remote"],
            description: "Opt in to remove remote files after download or extraction."
        },
    ]
};

export const OPTIONS_CONTEXT_EXTRACT: IOptionsContext = {
    header: "Archive Extraction",
    id: "extraction",
    options: [
        {
            type: OptionType.Checkbox,
            label: "Extract archives in the downloads directory",
            valuePath: ["controller", "use_local_path_as_extract_path"],
            description: null
        },
        {
            type: OptionType.Checkbox,
            label: "Use managed extract folders",
            valuePath: ["controller", "managed_extract_folders_enabled"],
            description: "Place each extracted archive in its own folder and hide its contents from local file results"
        },
        {
            type: OptionType.Text,
            label: "Extract Path",
            valuePath: ["controller", "extract_path"],
            description: "When option above is disabled, extract archives to this directory"
        },
    ]
};
