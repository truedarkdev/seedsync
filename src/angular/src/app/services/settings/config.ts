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
    verbose: boolean;
    breadcrumb_trace_enabled: boolean;
}
const DefaultGeneral: IGeneral = {
    log_level: "INFO",
    verbose: null,
    breadcrumb_trace_enabled: null
};
const GeneralRecord = Record(DefaultGeneral);

/*
 * LFTP
 */
interface ILftp {
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



/*
 * CONFIG
 */
export interface IConfig {
    general: IGeneral;
    lftp: ILftp;
    controller: IController;
    web: IWeb;
    autoqueue: IAutoQueue;
    logging: ILogging;

}
const DefaultConfig: IConfig = {
    general: null,
    lftp: null,
    controller: null,
    web: null,
    autoqueue: null,
    logging: null,
};
const ConfigRecord = Record(DefaultConfig);


export class Config extends ConfigRecord implements IConfig {
    general: IGeneral;
    lftp: ILftp;
    controller: IController;
    web: IWeb;
    autoqueue: IAutoQueue;
    logging: ILogging;

    constructor(props) {
        const general = Config.normalizeGeneral((props && props.general) || {});
        const logging = Config.normalizeLogging((props && props.logging) || {});
        // Create immutable members
        super({
            general: GeneralRecord(general),
            lftp: LftpRecord((props && props.lftp) || {}),
            controller: ControllerRecord((props && props.controller) || {}),
            web: WebRecord((props && props.web) || {}),
            autoqueue: AutoQueueRecord((props && props.autoqueue) || {}),
            logging: LoggingRecord(logging)
        });
    }

    private static normalizeGeneral(general: {[key: string]: any}): IGeneral {
        const normalized = typeof general.toJS === "function"
            ? {...general.toJS()}
            : {...general};
        if (normalized.log_level === undefined || normalized.log_level === null) {
            if (normalized.debug !== undefined) {
                const debugValue = typeof normalized.debug === "string"
                    ? normalized.debug.trim().toLowerCase()
                    : normalized.debug;
                normalized.log_level = ["y", "yes", "t", "true", "on", "1"].includes(String(debugValue))
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
        delete normalized.debug;
        return normalized as IGeneral;
    }

    private static normalizeLogging(logging: {[key: string]: any}): ILogging {
        const normalized = typeof logging.toJS === "function"
            ? {...logging.toJS()}
            : {...logging};
        if (typeof normalized.log_format === "string") {
            const logFormat = normalized.log_format.trim().toLowerCase();
            if (logFormat.length === 0) {
                delete normalized.log_format;
            } else {
                normalized.log_format = logFormat;
            }
        } else if (normalized.log_format === undefined || normalized.log_format === null) {
            delete normalized.log_format;
        }
        return normalized as ILogging;
    }

    getValue(section: string, option: string): any {
        const sectionRecord = this.get(section as any);
        if (sectionRecord && typeof sectionRecord.get === "function") {
            return sectionRecord.get(option);
        }
        return null;
    }
}
