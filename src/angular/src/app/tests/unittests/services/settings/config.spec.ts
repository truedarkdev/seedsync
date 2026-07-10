import * as Immutable from "immutable";

import {Config} from "../../../../services/settings/config";

describe("Testing config record initialization", () => {
    let config: Config;

    it("keeps notification secrets out of the typed record", () => {
        const notificationsConfig = new Config({notifications: {
            enabled: true,
            provider: "apprise",
            webhook_url: "**REDACTED**",
            hmac_secret: "**REDACTED**",
            apprise_url: "**REDACTED**",
            webhook_url_configured: true,
            hmac_secret_configured: true,
            apprise_url_configured: true,
            apprise_tag: "seedbox",
        }});
        expect(notificationsConfig.notifications.enabled).toBeTrue();
        expect(notificationsConfig.notifications.webhook_url_configured).toBeTrue();
        expect(notificationsConfig.notifications.hmac_secret_configured).toBeTrue();
        expect(notificationsConfig.notifications.provider).toBe("apprise");
        expect(notificationsConfig.notifications.apprise_url_configured).toBeTrue();
        expect(notificationsConfig.notifications.apprise_tag).toBe("seedbox");
        expect((notificationsConfig.notifications as any).webhook_url).toBeUndefined();
        expect((notificationsConfig.notifications as any).hmac_secret).toBeUndefined();
        expect((notificationsConfig.notifications as any).apprise_url).toBeUndefined();
    });

    beforeEach(() => {
        const configJson = {
            general: {
                log_level: "DEBUG",
                verbose: false,
                exclude_patterns: "*.nfo,Sample/",
                breadcrumb_trace_enabled: true
            },
            lftp: {
                transfer_backend: "rclone",
                remote_address: "remote.server.com",
                remote_username: "some.user",
                remote_password: "my.password",
                remote_port: 3456,
                remote_path: "/some/remote/path",
                local_path: "/some/local/path",
                remote_path_to_scan_script: "/another/remote/path",
                remote_python_path: "/opt/python/bin/python3",
                use_ssh_key: true,
                num_max_parallel_downloads: 2,
                num_max_parallel_files_per_download: 8,
                num_max_connections_per_root_file: 32,
                num_max_connections_per_dir_file: 4,
                num_max_total_connections: 32,
                use_temp_file: true,
                rate_limit: "1M",
                net_socket_buffer: "8M",
                staging_path: "/some/local/path/incomplete",
                protocol: "ftps",
                remote_ftp_port: 2121,
                ftp_ssl_verify_certificate: true
            },
            validate: {
                xfer_verify: true
            },
            controller: {
                interval_ms_remote_scan: 30000,
                interval_ms_local_scan: 10000,
                interval_ms_downloading_scan: 1000,
                extract_path: "/path/to/extract",
                use_local_path_as_extract_path: true,
                managed_extract_folders_enabled: true,
            },
            web: {
                port: 8800
            },
            autoqueue: {
                enabled: true,
                patterns_only: false,
                auto_extract: true,
            },
            logging: {
                log_format: "MiXeD"
            }
        };
        config = new Config(configJson);
    });


    it("should initialize with correct values", () => {
        expect(config.general.log_level).toBe("DEBUG");
        expect(config.general.debug).toBe(true);
        expect(config.general.verbose).toBe(false);
        expect(config.general.exclude_patterns).toBe("*.nfo,Sample/");
        expect(config.general.breadcrumb_trace_enabled).toBe(true);
        expect(config.lftp.transfer_backend).toBe("rclone");
        expect(config.lftp.remote_address).toBe("remote.server.com");
        expect(config.lftp.remote_username).toBe("some.user");
        expect(config.lftp.remote_password).toBe("my.password");
        expect(config.lftp.remote_port).toBe(3456);
        expect(config.lftp.remote_path).toBe("/some/remote/path");
        expect(config.lftp.local_path).toBe("/some/local/path");
        expect(config.lftp.remote_path_to_scan_script).toBe("/another/remote/path");
        expect(config.lftp.remote_python_path).toBe("/opt/python/bin/python3");
        expect(config.lftp.use_ssh_key).toBe(true);
        expect(config.lftp.num_max_parallel_downloads).toBe(2);
        expect(config.lftp.num_max_parallel_files_per_download).toBe(8);
        expect(config.lftp.num_max_connections_per_root_file).toBe(32);
        expect(config.lftp.num_max_connections_per_dir_file).toBe(4);
        expect(config.lftp.num_max_total_connections).toBe(32);
        expect(config.lftp.use_temp_file).toBe(true);
        expect(config.lftp.rate_limit).toBe("1M");
        expect(config.lftp.net_socket_buffer).toBe("8M");
        expect(config.lftp.staging_path).toBe("/some/local/path/incomplete");
        expect(config.lftp.protocol).toBe("sftp");
        expect(config.lftp.remote_ftp_port).toBe(2121);
        expect(config.lftp.ftp_ssl_verify_certificate).toBe(true);
        expect(config.validate.xfer_verify).toBe(true);
        expect(config.controller.interval_ms_remote_scan).toBe(30000);
        expect(config.controller.interval_ms_local_scan).toBe(10000);
        expect(config.controller.interval_ms_downloading_scan).toBe(1000);
        expect(config.controller.extract_path).toBe("/path/to/extract");
        expect(config.controller.use_local_path_as_extract_path).toBe(true);
        expect(config.controller.managed_extract_folders_enabled).toBe(true);
        expect(config.web.port).toBe(8800);
        expect(config.autoqueue.enabled).toBe(true);
        expect(config.autoqueue.patterns_only).toBe(false);
        expect(config.autoqueue.auto_extract).toBe(true);
        expect(config.autoqueue.auto_delete_remote).toBe(false);
        expect(config.logging.log_format).toBe("MiXeD");
    });

    it("should be immutable", () => {
        expect(config instanceof Immutable.Record).toBe(true);
    });

    it("should have immutable members", () => {
        expect(config.general instanceof Immutable.Record).toBe(true);
        expect(config.lftp instanceof Immutable.Record).toBe(true);
        expect(config.controller instanceof Immutable.Record).toBe(true);
        expect(config.web instanceof Immutable.Record).toBe(true);
        expect(config.autoqueue instanceof Immutable.Record).toBe(true);
    });

    it("should allow missing sections and null-safe value lookup", () => {
        const partialConfig = new Config({general: {log_level: "DEBUG"}});

        expect(partialConfig.general.log_level).toBe("DEBUG");
        expect(partialConfig.general.debug).toBe(true);
        expect(partialConfig.general.breadcrumb_trace_enabled).toBe(null);
        expect(partialConfig.general.exclude_patterns).toBe("");
        expect(partialConfig.autoqueue.auto_delete_remote).toBe(false);
        expect(partialConfig.logging.log_format).toBe("standard");
        expect(partialConfig.getValue("general", "log_level")).toBe("DEBUG");
        expect(partialConfig.getValue("general", "debug")).toBe(true);
        expect(partialConfig.getValue("general", "breadcrumb_trace_enabled")).toBe(null);
        expect(partialConfig.getValue("general", "verbose")).toBe(null);
        expect(partialConfig.getValue("lftp", "remote_address")).toBe(null);
        expect(partialConfig.getValue("lftp", "transfer_backend")).toBe("lftp");
        expect(partialConfig.getValue("lftp", "net_socket_buffer")).toBe("8M");
        expect(partialConfig.getValue("lftp", "remote_python_path")).toBe("python3");
        expect(partialConfig.getValue("lftp", "protocol")).toBe("sftp");
        expect(partialConfig.getValue("lftp", "remote_ftp_port")).toBe(21);
        expect(partialConfig.getValue("lftp", "ftp_ssl_verify_certificate")).toBe(true);
        expect(partialConfig.validate.xfer_verify).toBe(true);
        expect(partialConfig.getValue("logging", "log_format")).toBe("standard");
        expect(partialConfig.getValue("missing", "value")).toBe(null);
    });

    it("should derive debug from legacy debug input when log_level is absent", () => {
        const legacyDebugConfig = new Config({general: {debug: "yes"}});

        expect(legacyDebugConfig.general.log_level).toBe("DEBUG");
        expect(legacyDebugConfig.general.debug).toBe(true);
        expect(legacyDebugConfig.getValue("general", "debug")).toBe(true);
    });

    it("should preserve logging format values exactly while defaulting blanks", () => {
        const preservedConfig = new Config({
            logging: {
                log_format: "MiXeD"
            }
        });
        const blankLoggingConfig = new Config({
            logging: {
                log_format: "   "
            }
        });

        expect(preservedConfig.logging.log_format).toBe("MiXeD");
        expect(blankLoggingConfig.logging.log_format).toBe("standard");
        expect(preservedConfig.getValue("logging", "log_format")).toBe("MiXeD");
        expect(blankLoggingConfig.getValue("logging", "log_format")).toBe("standard");
    });
});
