import {fakeAsync, TestBed, tick} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import * as Immutable from "immutable";

import {ConfigService} from "../../../../services/settings/config.service";
import {LoggerService} from "../../../../services/utils/logger.service";
import {Config} from "../../../../services/settings/config";
import {Localization} from "../../../../common/localization";
import {MockStreamServiceRegistry} from "../../../mocks/mock-stream-service.registry";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {RestService} from "../../../../services/utils/rest.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";


// noinspection JSUnusedLocalSymbols
const DoNothing = {next: reaction => {}};


describe("Testing config service", () => {
    let mockRegistry: MockStreamServiceRegistry;
    let httpMock: HttpTestingController;
    let configService: ConfigService;

    const expectConfigSetRequest = (section: string, option: string, value: any, response: string = "{}") => {
        const request = httpMock.expectOne(`/server/config/set/${section}/${option}`);
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({value});
        request.flush(response);
    };

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                HttpClientTestingModule
            ],
            providers: [
                ConfigService,
                LoggerService,
                RestService,
                ConnectedService,
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        mockRegistry = TestBed.get(StreamServiceRegistry);
        httpMock = TestBed.get(HttpTestingController);
        configService = TestBed.get(ConfigService);

        // Connect the services
        mockRegistry.connect();

        // Finish test config init
        configService.onInit();
    });

    it("should create an instance", () => {
        expect(configService).toBeDefined();
    });

    it("should refresh config before any SSE connection is established", () => {
        const standaloneRegistry = {
            connectedService: new ConnectedService()
        } as StreamServiceRegistry;
        const standaloneService = new ConfigService(
            standaloneRegistry,
            TestBed.get(RestService),
            TestBed.get(LoggerService)
        );

        standaloneService.onInit();

        let latestConfig = null;
        standaloneService.config.subscribe({
            next: config => {
                latestConfig = config;
            }
        });

        httpMock.expectOne("/server/config/get").flush({});
        standaloneService.refresh();
        httpMock.expectOne("/server/config/get").flush({
            general: {
                log_level: "DEBUG"
            }
        });

        expect(latestConfig).not.toBe(null);
        expect(latestConfig.general.log_level).toBe("DEBUG");
        httpMock.verify();
    });

    it("should parse config json correctly", () => {
        const configJson = {
            general: {
                log_level: "DEBUG",
                verbose: false,
                breadcrumb_trace_enabled: true
            },
            lftp: {
                remote_address: "remote.server.com",
                remote_username: "some.user",
                remote_password: "my.password",
                remote_path: "/some/remote/path",
                local_path: "/some/local/path",
                remote_path_to_scan_script: "/another/remote/path",
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
                ftp_ssl_verify_certificate: true,
            },
            validate: {
                xfer_verify: true,
            },
            restart_required: {
                general: {
                    log_level: true,
                    verbose: false,
                    breadcrumb_trace_enabled: false
                },
                lftp: {
                    remote_address: true,
                    net_socket_buffer: false
                },
                validate: {
                    xfer_verify: false
                }
            },
            controller: {
                interval_ms_remote_scan: 30000,
                interval_ms_local_scan: 10000,
                interval_ms_downloading_scan: 1000
            },
            web: {
                port: 8800
            },
            autoqueue: {
                enabled: true,
                patterns_only: false
            },
            logging: {
                log_format: "JSON"
            }
        };
        httpMock.expectOne("/server/config/get").flush(configJson);

        configService.config.subscribe({
            next: config => {
                expect(config.general.log_level).toBe("DEBUG");
                expect(config.general.verbose).toBe(false);
                expect(config.general.breadcrumb_trace_enabled).toBe(true);
                expect(config.lftp.remote_address).toBe("remote.server.com");
                expect(config.lftp.remote_username).toBe("some.user");
                expect(config.lftp.remote_password).toBe("my.password");
                expect(config.lftp.remote_path).toBe("/some/remote/path");
                expect(config.lftp.local_path).toBe("/some/local/path");
                expect(config.lftp.remote_path_to_scan_script).toBe("/another/remote/path");
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
                expect(config.lftp.protocol).toBe("ftps");
                expect(config.lftp.remote_ftp_port).toBe(2121);
                expect(config.lftp.ftp_ssl_verify_certificate).toBe(true);
                expect(config.validate.xfer_verify).toBe(true);
                expect(configService.requiresRestart("general", "log_level")).toBe(true);
                expect(configService.requiresRestart("general", "verbose")).toBe(false);
                expect(configService.requiresRestart("general", "breadcrumb_trace_enabled")).toBe(false);
                expect(configService.requiresRestart("lftp", "remote_address")).toBe(true);
                expect(configService.requiresRestart("lftp", "net_socket_buffer")).toBe(false);
                expect(configService.requiresRestart("validate", "xfer_verify")).toBe(false);
                expect(config.controller.interval_ms_remote_scan).toBe(30000);
                expect(config.controller.interval_ms_local_scan).toBe(10000);
                expect(config.controller.interval_ms_downloading_scan).toBe(1000);
                expect(config.web.port).toBe(8800);
                expect(config.autoqueue.enabled).toBe(true);
                expect(config.autoqueue.patterns_only).toBe(false);
                expect(config.autoqueue.auto_delete_remote).toBe(false);
                expect(config.logging.log_format).toBe("json");
            }
        });

        httpMock.verify();
    });

    it("should expose backend restart metadata from the config response", () => {
        httpMock.expectOne("/server/config/get").flush({
            general: {
                log_level: "DEBUG",
                verbose: false,
                breadcrumb_trace_enabled: true
            },
            lftp: {
                remote_path: "/remote/server/path",
                net_socket_buffer: "8M"
            },
            restart_required: {
                general: {
                    log_level: true,
                    verbose: false,
                    breadcrumb_trace_enabled: false
                },
                lftp: {
                    remote_path: true,
                    net_socket_buffer: false
                }
            }
        });

        expect(configService.requiresRestart("general", "log_level")).toBe(true);
        expect(configService.requiresRestart("general", "verbose")).toBe(false);
        expect(configService.requiresRestart("general", "breadcrumb_trace_enabled")).toBe(false);
        expect(configService.requiresRestart("lftp", "remote_path")).toBe(true);
        expect(configService.requiresRestart("lftp", "net_socket_buffer")).toBe(false);
    });

    it("should get null on get error 404", () => {
        httpMock.expectOne("/server/config/get").flush(
        "Not found",
        {status: 404, statusText: "Bad Request"}
        );

        configService.config.subscribe({
            next: config => {
                expect(config).toBe(null);
            }
        });

        httpMock.verify();
    });

    it("should get null on get network error", () => {
        httpMock.expectOne("/server/config/get").error(new ErrorEvent("mock error"));

        configService.config.subscribe({
            next: config => {
                expect(config).toBe(null);
            }
        });

        httpMock.verify();
    });

    it("should get null on malformed json response", fakeAsync(() => {
        httpMock.expectOne("/server/config/get").flush({general: {log_level: "DEBUG"}});

        let latestConfig = null;
        configService.config.subscribe({
            next: config => {
                latestConfig = config;
            }
        });

        expect(latestConfig).not.toBe(null);
        expect(latestConfig.general.log_level).toBe("DEBUG");

        const consoleErrorSpy = spyOn(console, "error");
        const malformedResponse = "not-json";

        (<any>configService).onConnected();
        const request = httpMock.expectOne("/server/config/get");
        expect(() => request.flush(malformedResponse)).not.toThrow();

        tick();

        expect(latestConfig).toBe(null);
        expect(consoleErrorSpy).toHaveBeenCalled();
        const errorCall = consoleErrorSpy.calls.mostRecent();
        expect(errorCall.args).toEqual(["Failed to parse config response"]);
        expect(JSON.stringify(errorCall.args)).not.toContain(malformedResponse);
        expect(errorCall.args.some(arg => arg instanceof SyntaxError)).toBe(false);
        httpMock.verify();
    }));

    it("should get null on disconnect", fakeAsync(() => {
        const configExpected = [
            new Config({lftp: {remote_address: "first"}}),
            null
        ];

        httpMock.expectOne("/server/config/get").flush({lftp: {remote_address: "first"}});
        let configSubscriberIndex = 0;

        configService.config.subscribe({
            next: config => {
                expect(Immutable.is(config, configExpected[configSubscriberIndex++])).toBe(true);
            }
        });

        // status disconnect
        mockRegistry.disconnect();
        tick();

        httpMock.verify();
        expect(configSubscriberIndex).toBe(2);
    }));

    it("should retry GET on disconnect", fakeAsync(() => {
        // first connect
        httpMock.expectOne("/server/config/get").flush("{}");


        // status disconnect
        mockRegistry.disconnect();
        tick();

        // status reconnect
        mockRegistry.connect();
        tick();
        httpMock.expectOne("/server/config/get").flush("{}");

        httpMock.verify();
    }));

    it("should send a POST on a set log level option", () => {
        // first connect
        httpMock.expectOne("/server/config/get").flush("{}");

        let configSubscriberIndex = 0;
        configService.set("general", "log_level", "DEBUG").subscribe({
           next: reaction => {
               configSubscriberIndex++;
               expect(reaction.success).toBe(true);
           }
        });

        // set request
        expectConfigSetRequest("general", "log_level", "DEBUG");

        expect(configSubscriberIndex).toBe(1);
        httpMock.verify();
    });

    it("should send a POST on setting breadcrumb trace recording", () => {
        httpMock.expectOne("/server/config/get").flush({general: {breadcrumb_trace_enabled: false}});

        configService.set("general", "breadcrumb_trace_enabled", true).subscribe({
           next: reaction => {
               expect(reaction.success).toBe(true);
           }
        });

        expectConfigSetRequest("general", "breadcrumb_trace_enabled", true);
        httpMock.verify();
    });

    it("should send a POST on setting log format", () => {
        httpMock.expectOne("/server/config/get").flush({logging: {log_format: "standard"}});

        configService.set("logging", "log_format", "STANDARD").subscribe(DoNothing);

        expectConfigSetRequest("logging", "log_format", "standard");
        httpMock.verify();
    });

    it("should preserve log level values before sending config updates", () => {
        httpMock.expectOne("/server/config/get").flush({general: {log_level: "INFO"}});

        configService.set("general", "log_level", "INFO").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "INFO");

        configService.set("general", "log_level", "DEBUG").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "DEBUG");

        httpMock.verify();
    });

    it("should send correct POST requests on setting config options", () => {
        // first connect
        httpMock.expectOne("/server/config/get").flush("{}");

        configService.set("general", "log_level", "DEBUG").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "DEBUG");
        configService.set("general", "log_level", "INFO").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "INFO");
        configService.set("general", "log_level", "WARNING").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "WARNING");
        configService.set("general", "log_level", "ERROR").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "ERROR");
        configService.set("general", "log_level", "CRITICAL").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "CRITICAL");
        configService.set("general", "log_level", "test").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "test");
        configService.set("general", "log_level", "test space").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "test space");
        configService.set("general", "log_level", "test/slash").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "test/slash");
        configService.set("general", "log_level", "test\"doublequote").subscribe(
            DoNothing
        );
        expectConfigSetRequest("general", "log_level", "test\"doublequote");
        configService.set("general", "log_level", "/test/leadingslash").subscribe(DoNothing);
        expectConfigSetRequest("general", "log_level", "/test/leadingslash");

        httpMock.verify();
    });

    it("should return error on setting non-existing section", () => {
        // first connect
        httpMock.expectOne("/server/config/get").flush("{}");

        let configSubscriberIndex = 0;
        configService.set("bad_section", "log_level", "DEBUG").subscribe({
           next: reaction => {
               configSubscriberIndex++;
               expect(reaction.success).toBe(false);
               expect(reaction.errorMessage).toBe("Config has no option named bad_section.log_level");
           }
        });

        expect(configSubscriberIndex).toBe(1);
        httpMock.verify();
    });

    it("should return error on setting non-existing option", () => {
        // first connect
        httpMock.expectOne("/server/config/get").flush("{}");

        let configSubscriberIndex = 0;
        configService.set("general", "bad_option", "DEBUG").subscribe({
           next: reaction => {
               configSubscriberIndex++;
               expect(reaction.success).toBe(false);
               expect(reaction.errorMessage).toBe("Config has no option named general.bad_option");
           }
        });

        expect(configSubscriberIndex).toBe(1);
        httpMock.verify();
    });

    it("should return error on empty value", () => {
        // first connect
        httpMock.expectOne("/server/config/get").flush("{}");

        let configSubscriberIndex = 0;
        configService.set("general", "log_level", "").subscribe({
           next: reaction => {
               configSubscriberIndex++;
               expect(reaction.success).toBe(false);
               expect(reaction.errorMessage).toBe("Setting general.log_level cannot be blank.");
           }
        });

        expect(configSubscriberIndex).toBe(1);
        httpMock.verify();
    });

    it("should allow empty remote password values for SFTP key auth", () => {
        httpMock.expectOne("/server/config/get").flush({
            lftp: {
                remote_password: "initial",
                use_ssh_key: true,
                protocol: "sftp",
            }
        });

        configService.set("lftp", "remote_password", "").subscribe(DoNothing);

        expectConfigSetRequest("lftp", "remote_password", "");
        httpMock.verify();
    });

    it("should reject ftps when the transfer password would be blank", () => {
        httpMock.expectOne("/server/config/get").flush({
            lftp: {
                remote_password: "",
                use_ssh_key: true,
                protocol: "sftp",
            }
        });

        let configSubscriberIndex = 0;
        configService.set("lftp", "protocol", "ftps").subscribe({
            next: reaction => {
                configSubscriberIndex++;
                expect(reaction.success).toBe(false);
                expect(reaction.errorMessage).toBe(Localization.Notification.FTPS_TRANSFER_PASSWORD_REQUIRED);
            }
        });

        expect(configSubscriberIndex).toBe(1);
        httpMock.expectNone("/server/config/set/lftp/protocol");
        httpMock.verify();
    });

    it("should allow empty net_socket_buffer values", () => {
        httpMock.expectOne("/server/config/get").flush({lftp: {net_socket_buffer: "8M"}});

        configService.set("lftp", "net_socket_buffer", "").subscribe(DoNothing);

        expectConfigSetRequest("lftp", "net_socket_buffer", "");
        httpMock.verify();
    });

    it("should allow empty remote_python_path values for backend default fallback", () => {
        httpMock.expectOne("/server/config/get").flush({lftp: {remote_python_path: "python3"}});

        configService.set("lftp", "remote_python_path", "").subscribe(DoNothing);

        expectConfigSetRequest("lftp", "remote_python_path", "");
        httpMock.verify();
    });

    it("should return error on empty log_format values", () => {
        httpMock.expectOne("/server/config/get").flush({logging: {log_format: "standard"}});

        let configSubscriberIndex = 0;
        configService.set("logging", "log_format", "").subscribe({
            next: reaction => {
                configSubscriberIndex++;
                expect(reaction.success).toBe(false);
                expect(reaction.errorMessage).toBe("Setting logging.log_format cannot be blank.");
            }
        });

        expect(configSubscriberIndex).toBe(1);
        httpMock.verify();
    });

    it("should normalize net_socket_buffer before sending config updates", () => {
        httpMock.expectOne("/server/config/get").flush({lftp: {net_socket_buffer: "2M"}});

        const configExpected = [
            new Config({lftp: {net_socket_buffer: "2M"}}),
            new Config({lftp: {net_socket_buffer: "8M"}})
        ];
        let configSubscriberIndex = 0;
        configService.config.subscribe({
            next: config => {
                expect(Immutable.is(config, configExpected[configSubscriberIndex++])).toBe(true);
            }
        });

        configService.set("lftp", "net_socket_buffer", "8m").subscribe(DoNothing);

        expectConfigSetRequest("lftp", "net_socket_buffer", "8M");
        expect(configSubscriberIndex).toBe(2);
        httpMock.verify();
    });

    it("should send updated config on a successful set", () => {
        const configJson = {general: {log_level: "INFO"}};
        // first connect
        httpMock.expectOne("/server/config/get").flush(configJson);

        const expectedLogLevels = ["INFO", "DEBUG"];
        let configSubscriberIndex = 0;
        configService.config.subscribe({
            next: config => {
                expect(config.general.log_level).toBe(expectedLogLevels[configSubscriberIndex++]);
            }
        });

        // issue the set
        const setRequest = configService.set("general", "log_level", "DEBUG");
        httpMock.expectNone("/server/config/set/general/log_level");
        expect(configSubscriberIndex).toBe(1);

        setRequest.subscribe(DoNothing);

        // set request
        expectConfigSetRequest("general", "log_level", "DEBUG", "");

        expect(configSubscriberIndex).toBe(2);
        httpMock.verify();
    });

    it("should send updated config once for multiple subscribers on a successful set", () => {
        const configJson = {general: {log_level: "INFO"}};
        // first connect
        httpMock.expectOne("/server/config/get").flush(configJson);

        const expectedLogLevels = ["INFO", "DEBUG"];
        let configSubscriberIndex = 0;
        configService.config.subscribe({
            next: config => {
                expect(config.general.log_level).toBe(expectedLogLevels[configSubscriberIndex++]);
            }
        });

        const setRequest = configService.set("general", "log_level", "DEBUG");
        httpMock.expectNone("/server/config/set/general/log_level");
        expect(configSubscriberIndex).toBe(1);

        let reactionCount = 0;
        const reactionObserver = {
            next: () => {
                reactionCount++;
            }
        };
        setRequest.subscribe(reactionObserver);
        setRequest.subscribe(reactionObserver);

        // set request
        expectConfigSetRequest("general", "log_level", "DEBUG", "");

        expect(reactionCount).toBe(2);
        expect(configSubscriberIndex).toBe(2);
        httpMock.verify();
    });

    it("should NOT send updated config on a failed set", () => {
        const configJson = {general: {log_level: "INFO"}};
        // first connect
        httpMock.expectOne("/server/config/get").flush(configJson);

        const configExpected = [
            new Config({general: {log_level: "INFO"}})
        ];
        let configSubscriberIndex = 0;
        configService.config.subscribe({
            next: config => {
                expect(Immutable.is(config, configExpected[configSubscriberIndex++])).toBe(true);
            }
        });

        // issue the set
        configService.set("general", "log_level", "DEBUG").subscribe(DoNothing);

        // set request
        const request = httpMock.expectOne("/server/config/set/general/log_level");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({value: "DEBUG"});
        request.flush(
            "Not found",
            {status: 404, statusText: "Bad Request"}
        );

        expect(configSubscriberIndex).toBe(1);
        httpMock.verify();
    });
});
