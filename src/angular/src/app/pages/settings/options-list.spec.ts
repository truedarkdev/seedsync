import {OptionType} from "./option.component";
import {OPTIONS_CONTEXT_CONNECTIONS, OPTIONS_CONTEXT_SERVER, OPTIONS_CONTEXT_TRANSFER_PROTOCOL} from "./options-list";

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

    it("exposes remote python path under Server", () => {
        const remotePythonPath = OPTIONS_CONTEXT_SERVER.options.find(
            option => option.valuePath[1] === "remote_python_path"
        );

        expect(remotePythonPath.type).toBe(OptionType.Text);
        expect(remotePythonPath.label).toBe("Remote Python Path");
        expect(remotePythonPath.description).toContain("python3");
    });
});
