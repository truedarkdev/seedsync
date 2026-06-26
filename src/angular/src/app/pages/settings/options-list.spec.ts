import {OptionType} from "./option.component";
import {
    OPTIONS_CONTEXT_AUTOQUEUE,
    OPTIONS_CONTEXT_CONNECTIONS,
    OPTIONS_CONTEXT_DISCOVERY,
    OPTIONS_CONTEXT_OTHER,
    OPTIONS_CONTEXT_SERVER,
    OPTIONS_CONTEXT_TRANSFER_PROTOCOL,
    OPTIONS_CONTEXT_VALIDATE
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

    it("defines inline transfer verification under Validation", () => {
        const xferVerify = OPTIONS_CONTEXT_VALIDATE.options.find(
            option => option.valuePath[1] === "xfer_verify"
        )!;

        expect(OPTIONS_CONTEXT_VALIDATE.header).toBe("Validation");
        expect(xferVerify.type).toBe(OptionType.Checkbox);
        expect(xferVerify.label).toBe("Verify transfers inline (recommended)");
        expect(xferVerify.valuePath).toEqual(["validate", "xfer_verify"]);
        expect(xferVerify.description).toContain("xfer:verify");
    });

    it("keeps log level and log format in Other Settings", () => {
        const logLevel = OPTIONS_CONTEXT_OTHER.options.find(option => option.valuePath[1] === "log_level")!;
        const logFormat = OPTIONS_CONTEXT_OTHER.options.find(option => option.valuePath[1] === "log_format")!;

        expect(OPTIONS_CONTEXT_OTHER.header).toBe("Other Settings");
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
