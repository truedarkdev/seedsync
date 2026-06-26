import {OptionType} from "./option.component";
import {
    OPTIONS_CONTEXT_AUTOQUEUE,
    OPTIONS_CONTEXT_CONNECTIONS,
    OPTIONS_CONTEXT_DISCOVERY,
    OPTIONS_CONTEXT_OTHER,
    OPTIONS_CONTEXT_SERVER,
    OPTIONS_CONTEXT_TRANSFER_PROTOCOL
} from "./options-list";

describe("settings options list", () => {
    it("keeps transfer protocol options out of Connections", () => {
        const connectionPaths = OPTIONS_CONTEXT_CONNECTIONS.options.map(option => option.valuePath.join("."));

        expect(connectionPaths).not.toContain("lftp.protocol");
        expect(connectionPaths).not.toContain("lftp.remote_ftp_port");
        expect(connectionPaths).not.toContain("lftp.ftp_ssl_verify_certificate");
    });

    it("defines isolated FTPS controls under Transfer Protocol", () => {
        const protocol = OPTIONS_CONTEXT_TRANSFER_PROTOCOL.options.find(
            option => option.valuePath[1] === "protocol"
        );
        const ftpPort = OPTIONS_CONTEXT_TRANSFER_PROTOCOL.options.find(
            option => option.valuePath[1] === "remote_ftp_port"
        );
        const verifyCertificate = OPTIONS_CONTEXT_TRANSFER_PROTOCOL.options.find(
            option => option.valuePath[1] === "ftp_ssl_verify_certificate"
        );

        expect(OPTIONS_CONTEXT_TRANSFER_PROTOCOL.header).toBe("Transfer Protocol");
        expect(protocol.type).toBe(OptionType.Select);
        expect(protocol.choices).toEqual([
            {label: "SFTP", value: "sftp"},
            {label: "FTPS", value: "ftps"},
        ]);
        expect(ftpPort.type).toBe(OptionType.Text);
        expect(ftpPort.disabledWhenSftp).toBe(true);
        expect(verifyCertificate.type).toBe(OptionType.Checkbox);
        expect(verifyCertificate.disabledWhenSftp).toBe(true);
    });

    it("groups other settings into diagnostics, validation, and application", () => {
        const [diagnostics, validation, application] = OPTIONS_CONTEXT_OTHER.groups!;
        const logLevel = diagnostics.options.find(option => option.valuePath[1] === "log_level")!;
        const logFormat = diagnostics.options.find(option => option.valuePath[1] === "log_format")!;
        const breadcrumbTrace = diagnostics.options.find(option => option.valuePath[1] === "breadcrumb_trace_enabled")!;
        const xferVerify = validation.options[0]!;
        const webGuiPort = application.options[0]!;

        expect(OPTIONS_CONTEXT_OTHER.header).toBe("Other Settings");
        expect(OPTIONS_CONTEXT_OTHER.groups!.map(group => group.header)).toEqual([
            "Diagnostics",
            "Validation",
            "Application",
        ]);
        expect(OPTIONS_CONTEXT_OTHER.options.map(option => option.valuePath.join("."))).toEqual([
            "general.log_level",
            "logging.log_format",
            "general.breadcrumb_trace_enabled",
            "validate.xfer_verify",
            "web.port",
        ]);

        expect(logLevel.type).toBe(OptionType.Select);
        expect(logLevel.label).toBe("Log Level");
        expect(logLevel.valuePath).toEqual(["general", "log_level"]);
        expect(logLevel.choices).toEqual([
            {label: "Debug", value: "DEBUG"},
            {label: "Info", value: "INFO"},
            {label: "Warning", value: "WARNING"},
            {label: "Error", value: "ERROR"},
        ]);
        expect(logFormat.type).toBe(OptionType.Select);
        expect(logFormat.label).toBe("Log Format");
        expect(logFormat.valuePath).toEqual(["logging", "log_format"]);
        expect(logFormat.choices).toEqual([
            {label: "Standard", value: "standard"},
            {label: "JSON", value: "json"},
        ]);
        expect(breadcrumbTrace.type).toBe(OptionType.Checkbox);
        expect(breadcrumbTrace.label).toBe("Enable breadcrumb trace recorder");
        expect(breadcrumbTrace.valuePath).toEqual(["general", "breadcrumb_trace_enabled"]);
        expect(breadcrumbTrace.description).toContain("recent-context window");

        expect(xferVerify.type).toBe(OptionType.Checkbox);
        expect(xferVerify.label).toBe("Verify transfers inline (recommended)");
        expect(xferVerify.valuePath).toEqual(["validate", "xfer_verify"]);
        expect(xferVerify.description).toContain("xfer:verify");

        expect(webGuiPort.type).toBe(OptionType.Text);
        expect(webGuiPort.label).toBe("Web GUI Port");
        expect(webGuiPort.valuePath).toEqual(["web", "port"]);
    });

    it("exposes remote python path under Server", () => {
        const remotePythonPath = OPTIONS_CONTEXT_SERVER.options.find(
            option => option.valuePath[1] === "remote_python_path"
        );

        expect(remotePythonPath.type).toBe(OptionType.Text);
        expect(remotePythonPath.label).toBe("Remote Python Path");
        expect(remotePythonPath.description).toContain("python3");
    });

    it("exposes exclude patterns in File Discovery", () => {
        const excludePatterns = OPTIONS_CONTEXT_DISCOVERY.options.find(
            option => option.valuePath[1] === "exclude_patterns"
        )!;

        expect(OPTIONS_CONTEXT_DISCOVERY.header).toBe("File Discovery");
        expect(excludePatterns.type).toBe(OptionType.Text);
        expect(excludePatterns.label).toBe("Exclude Patterns");
        expect(excludePatterns.description).toContain("directory-only");
        expect(excludePatterns.valuePath).toEqual(["general", "exclude_patterns"]);
    });

    it("keeps the legacy directory fields in Server and the global AutoQueue toggle", () => {
        const serverDirectory = OPTIONS_CONTEXT_SERVER.options.find(
            option => option.valuePath[1] === "remote_path"
        )!;
        const localDirectory = OPTIONS_CONTEXT_SERVER.options.find(
            option => option.valuePath[1] === "local_path"
        )!;
        const autoqueueEnabled = OPTIONS_CONTEXT_AUTOQUEUE.options.find(
            option => option.valuePath[1] === "enabled"
        )!;

        expect(serverDirectory.type).toBe(OptionType.Text);
        expect(serverDirectory.label).toBe("Server Directory");
        expect(serverDirectory.description).toContain("remote server");

        expect(localDirectory.type).toBe(OptionType.Text);
        expect(localDirectory.label).toBe("Local Directory");
        expect(localDirectory.description).toContain("Downloaded files");

        expect(autoqueueEnabled.type).toBe(OptionType.Checkbox);
        expect(autoqueueEnabled.label).toBe("Enable AutoQueue");
        expect(autoqueueEnabled.disabled).toBeUndefined();
    });
});
