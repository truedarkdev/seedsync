import {Record} from "immutable";

/**
 * Backend config
 * Note: Naming convention matches that used in the JSON
 */

/*
 * GENERAL
 */
interface IGeneral {
    log_level: string;
    debug: boolean;
    verbose: boolean;
    exclude_patterns: string;
    breadcrumb_trace_enabled: boolean;
}
const DefaultGeneral: IGeneral = {
    log_level: "INFO",
    debug: false,
    verbose: null,
    exclude_patterns: "",
    breadcrumb_trace_enabled: null
};
const GeneralRecord = Record(DefaultGeneral);

/*
 * LFTP
 */
interface ILftp {
    transfer_backend: string;
    remote_address: string;
    remote_username: string;
    remote_password: string;
    remote_port: number;
    remote_path: string;
    local_path: string;
    remote_path_to_scan_script: string;
    remote_python_path: string;
    use_ssh_key: boolean;
    num_max_parallel_downloads: number;
    num_max_parallel_files_per_download: number;
    num_max_connections_per_root_file: number;
    num_max_connections_per_dir_file: number;
    num_max_total_connections: number;
    use_temp_file: boolean;
    rate_limit: string;
    net_socket_buffer: string;
    staging_path: string;
    protocol: string;
    remote_ftp_port: number;
    ftp_ssl_verify_certificate: boolean;
}
const DefaultLftp: ILftp = {
    transfer_backend: "lftp",
    remote_address: null,
    remote_username: null,
    remote_password: null,
    remote_port: null,
    remote_path: null,
    local_path: null,
    remote_path_to_scan_script: null,
    remote_python_path: "python3",
    use_ssh_key: null,
    num_max_parallel_downloads: null,
    num_max_parallel_files_per_download: null,
    num_max_connections_per_root_file: null,
    num_max_connections_per_dir_file: null,
    num_max_total_connections: null,
    use_temp_file: null,
    rate_limit: "0",
    net_socket_buffer: "8M",
    staging_path: "",
    protocol: "sftp",
    remote_ftp_port: 21,
    ftp_ssl_verify_certificate: true,
};
const LftpRecord = Record(DefaultLftp);

/*
 * VALIDATE
 */
interface IValidate {
    xfer_verify: boolean;
}
const DefaultValidate: IValidate = {
    xfer_verify: true,
};
const ValidateRecord = Record(DefaultValidate);

/*
 * CONTROLLER
 */
interface IController {
    interval_ms_remote_scan: number;
    interval_ms_local_scan: number;
    interval_ms_downloading_scan: number;
    extract_path: string;
    use_local_path_as_extract_path: boolean;
    managed_extract_folders_enabled: boolean;
}
const DefaultController: IController = {
    interval_ms_remote_scan: null,
    interval_ms_local_scan: null,
    interval_ms_downloading_scan: null,
    extract_path: null,
    use_local_path_as_extract_path: null,
    managed_extract_folders_enabled: null,
};
const ControllerRecord = Record(DefaultController);

/*
 * WEB
 */
interface IWeb {
    port: number;
}
const DefaultWeb: IWeb = {
    port: null
};
const WebRecord = Record(DefaultWeb);

/*
 * AUTOQUEUE
 */
interface IAutoQueue {
    enabled: boolean;
    patterns_only: boolean;
    auto_extract: boolean;
    auto_delete_remote: boolean;
}
const DefaultAutoQueue: IAutoQueue = {
    enabled: null,
    patterns_only: null,
    auto_extract: null,
    auto_delete_remote: false,
};
const AutoQueueRecord = Record(DefaultAutoQueue);

/*
 * LOGGING
 */
interface ILogging {
    log_format: string;
}
const DefaultLogging: ILogging = {
    log_format: "standard",
};
const LoggingRecord = Record(DefaultLogging);

export interface INotifications {
    enabled: boolean;
    provider: "webhook" | "apprise";
    webhook_url_configured: boolean;
    hmac_secret_configured: boolean;
    apprise_url_configured: boolean;
    apprise_tag: string;
    allow_private_networks: boolean;
    download_complete: boolean;
    extraction_complete: boolean;
    delete_complete: boolean;
}
const DefaultNotifications: INotifications = {
    enabled: false,
    provider: "webhook",
    webhook_url_configured: false,
    hmac_secret_configured: false,
    apprise_url_configured: false,
    apprise_tag: "",
    allow_private_networks: false,
    download_complete: true,
    extraction_complete: true,
    delete_complete: true,
};
const NotificationsRecord = Record(DefaultNotifications);



/*
 * CONFIG
 */
export interface IConfig {
    general: IGeneral;
    lftp: ILftp;
    validate: IValidate;
    controller: IController;
    web: IWeb;
    autoqueue: IAutoQueue;
    logging: ILogging;
    notifications: INotifications;

}
const DefaultConfig: IConfig = {
    general: null,
    lftp: null,
    validate: null,
    controller: null,
    web: null,
    autoqueue: null,
    logging: null,
    notifications: null,
};
const ConfigRecord = Record(DefaultConfig);


export class Config extends ConfigRecord implements IConfig {
    general: IGeneral;
    lftp: ILftp;
    validate: IValidate;
    controller: IController;
    web: IWeb;
    autoqueue: IAutoQueue;
    logging: ILogging;
    notifications: INotifications;

    constructor(props) {
        const general = Config.normalizeGeneral((props && props.general) || {});
        const logging = Config.normalizeLogging((props && props.logging) || {});
        // Create immutable members
        super({
            general: GeneralRecord(general),
            lftp: LftpRecord(Config.normalizeLftp((props && props.lftp) || {})),
            validate: ValidateRecord((props && props.validate) || {}),
            controller: ControllerRecord((props && props.controller) || {}),
            web: WebRecord((props && props.web) || {}),
            autoqueue: AutoQueueRecord((props && props.autoqueue) || {}),
            logging: LoggingRecord(logging),
            notifications: NotificationsRecord((props && props.notifications) || {})
        });
    }

    private static normalizeGeneral(general: {[key: string]: any}): IGeneral {
        const normalized = typeof general.toJS === "function"
            ? {...general.toJS()}
            : {...general};
        if (normalized.log_level === undefined || normalized.log_level === null) {
            if (normalized.debug !== undefined) {
                normalized.log_level = Config.normalizeLegacyDebugValue(normalized.debug)
                    ? "DEBUG"
                    : "INFO";
            }
        }
        if (typeof normalized.log_level === "string") {
            const logLevel = normalized.log_level.trim();
            if (logLevel.length === 0) {
                delete normalized.log_level;
            } else {
                normalized.log_level = logLevel.toUpperCase();
            }
        } else if (normalized.log_level === undefined || normalized.log_level === null) {
            delete normalized.log_level;
        }
        if (normalized.exclude_patterns === undefined || normalized.exclude_patterns === null) {
            delete normalized.exclude_patterns;
        }
        normalized.debug = Config.isDebugLogLevel(normalized.log_level);
        return normalized as IGeneral;
    }

    private static normalizeLogging(logging: {[key: string]: any}): ILogging {
        const normalized = typeof logging.toJS === "function"
            ? {...logging.toJS()}
            : {...logging};
        if (typeof normalized.log_format === "string") {
            if (normalized.log_format.trim().length === 0) {
                delete normalized.log_format;
            }
        } else if (normalized.log_format === undefined || normalized.log_format === null) {
            delete normalized.log_format;
        }
        return normalized as ILogging;
    }

    private static normalizeLftp(lftp: {[key: string]: any}): ILftp {
        const normalized = typeof lftp.toJS === "function"
            ? {...lftp.toJS()}
            : {...lftp};
        const transferBackend = Config.normalizeTransferBackendValue(normalized.transfer_backend);
        normalized.transfer_backend = transferBackend;
        if (transferBackend === "rclone") {
            normalized.protocol = "sftp";
        }
        return normalized as ILftp;
    }

    getValue(section: string, option: string): any {
        const sectionRecord = this.get(section as any);
        if (sectionRecord && typeof sectionRecord.get === "function") {
            return sectionRecord.get(option);
        }
        return null;
    }

    private static normalizeLegacyDebugValue(debugValue: any): boolean {
        if (debugValue === null || debugValue === undefined) {
            return false;
        }
        if (typeof debugValue === "string") {
            const normalizedDebugValue = debugValue.trim().toLowerCase();
            if (normalizedDebugValue.length === 0) {
                return false;
            }
            return ["y", "yes", "t", "true", "on", "1", "debug"].includes(normalizedDebugValue);
        }
        return Boolean(debugValue);
    }

    private static isDebugLogLevel(logLevel: any): boolean {
        return typeof logLevel === "string" && logLevel.trim().toUpperCase() === "DEBUG";
    }

    private static normalizeTransferBackendValue(value: any): string {
        if (typeof value !== "string") {
            return "lftp";
        }
        const normalizedValue = value.trim().toLowerCase();
        return ["lftp", "rclone"].includes(normalizedValue) ? normalizedValue : "lftp";
    }
}
