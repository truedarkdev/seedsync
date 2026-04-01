const DEFAULT_APP_BASE_URL = "http://myapp:8800/";
const DEFAULT_SELENIUM_ADDRESS = "http://chrome:4444/wd/hub";

function readEnv(name: string, defaultValue: string): string {
    const value = process.env[name];
    return value && value.trim() !== "" ? value : defaultValue;
}

export class Urls {
    static readonly APP_BASE_URL = readEnv("SEEDSYNC_E2E_APP_BASE_URL", DEFAULT_APP_BASE_URL);
    static readonly SELENIUM_ADDRESS = readEnv("SEEDSYNC_E2E_SELENIUM_ADDRESS", DEFAULT_SELENIUM_ADDRESS);
}
