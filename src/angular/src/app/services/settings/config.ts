import {Record} from "immutable";

/**
 * Backend config
 * Note: Naming convention matches that used in the JSON
 */

/*
 * GENERAL
 */
interface IGeneral {
    debug: boolean;
    verbose: boolean;
    breadcrumb_trace_enabled: boolean;
}
const DefaultGeneral: IGeneral = {
    debug: null,
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
    use_ssh_key: boolean;
    num_max_parallel_downloads: number;
    num_max_parallel_files_per_download: number;
    num_max_connections_per_root_file: number;
    num_max_connections_per_dir_file: number;
    num_max_total_connections: number;
    use_temp_file: boolean;
    rate_limit: string;
    staging_path: string;
}
const DefaultLftp: ILftp = {
    remote_address: null,
    remote_username: null,
    remote_password: null,
    remote_port: null,
    remote_path: null,
    local_path: null,
    remote_path_to_scan_script: null,
    use_ssh_key: null,
    num_max_parallel_downloads: null,
    num_max_parallel_files_per_download: null,
    num_max_connections_per_root_file: null,
    num_max_connections_per_dir_file: null,
    num_max_total_connections: null,
    use_temp_file: null,
    rate_limit: "0",
    staging_path: "",
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
}
const DefaultAutoQueue: IAutoQueue = {
    enabled: null,
    patterns_only: null,
    auto_extract: null,
};
const AutoQueueRecord = Record(DefaultAutoQueue);



/*
 * CONFIG
 */
export interface IConfig {
    general: IGeneral;
    lftp: ILftp;
    controller: IController;
    web: IWeb;
    autoqueue: IAutoQueue;

}
const DefaultConfig: IConfig = {
    general: null,
    lftp: null,
    controller: null,
    web: null,
    autoqueue: null,
};
const ConfigRecord = Record(DefaultConfig);


export class Config extends ConfigRecord implements IConfig {
    general: IGeneral;
    lftp: ILftp;
    controller: IController;
    web: IWeb;
    autoqueue: IAutoQueue;

    constructor(props) {
        // Create immutable members
        super({
            general: GeneralRecord((props && props.general) || {}),
            lftp: LftpRecord((props && props.lftp) || {}),
            controller: ControllerRecord((props && props.controller) || {}),
            web: WebRecord((props && props.web) || {}),
            autoqueue: AutoQueueRecord((props && props.autoqueue) || {})
        });
    }

    getValue(section: string, option: string): any {
        const sectionRecord = this.get(section as any);
        if (sectionRecord && typeof sectionRecord.get === "function") {
            return sectionRecord.get(option);
        }
        return null;
    }
}
