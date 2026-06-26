import {Injectable} from "@angular/core";
import {BehaviorSubject, Observable} from "rxjs";
import {shareReplay, tap} from "rxjs/operators";

import {Config, IConfig} from "./config";
import {LoggerService} from "../utils/logger.service";
import {BaseWebService} from "../base/base-web.service";
import {Localization} from "../../common/localization";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {RestService, WebReaction} from "../utils/rest.service";


interface IRestartRequiredOptions {
    [section: string]: {
        [option: string]: boolean;
    };
}

interface IConfigResponse extends IConfig {
    restart_required?: IRestartRequiredOptions;
}


/**
 * ConfigService provides the store for the config
 */
@Injectable()
export class ConfigService extends BaseWebService {
    private readonly CONFIG_GET_URL = "/server/config/get";

    private readonly CONFIG_SET_URL =
        (section, option) => `/server/config/set/${section}/${option}`

    private _config: BehaviorSubject<Config> = new BehaviorSubject(null);
    private _restartRequiredOptions: IRestartRequiredOptions = {};

    constructor(_streamServiceProvider: StreamServiceRegistry,
                private _restService: RestService,
                private _logger: LoggerService) {
        super(_streamServiceProvider);
    }

    /**
     * Returns an observable that provides that latest Config
     * @returns {Observable<Config>}
     */
    get config(): Observable<Config> {
        return this._config.asObservable();
    }

    /**
     * Loads config from the backend without waiting for the SSE stream.
     */
    public refresh() {
        this.getConfig();
    }

    public requiresRestart(section: string, option: string): boolean {
        const sectionRestartOptions = this._restartRequiredOptions[section];
        return Boolean(sectionRestartOptions && sectionRestartOptions[option]);
    }

    /**
     * Sets a value in the config
     * @param {string} section
     * @param {string} option
     * @param value
     * @returns {WebReaction}
     */
    public set(section: string, option: string, value: any): Observable<WebReaction> {
        const normalizedValue = this.normalizeValue(section, option, value);
        const valueStr: string = String(normalizedValue);
        const allowBlankValue = section === "lftp" &&
            (
                option === "remote_password" ||
                option === "net_socket_buffer" ||
                option === "remote_python_path"
            );
        const currentConfig = this._config.getValue();
        if (!currentConfig || !currentConfig.has(section) || !currentConfig.get(section).has(option)) {
            return new Observable<WebReaction>(observer => {
                observer.next(new WebReaction(false, null, `Config has no option named ${section}.${option}`));
            });
        } else if (valueStr.length === 0 && !allowBlankValue) {
            return new Observable<WebReaction>(observer => {
                observer.next(new WebReaction(
                    false, null, Localization.Notification.CONFIG_VALUE_BLANK(section, option))
                );
            });
        } else if (this.wouldCreateBlankFtpsPassword(currentConfig, section, option, normalizedValue)) {
            return new Observable<WebReaction>(observer => {
                observer.next(new WebReaction(
                    false, null, Localization.Notification.FTPS_TRANSFER_PASSWORD_REQUIRED)
                );
            });
        } else {
            const url = this.CONFIG_SET_URL(section, option);
            return this._restService.sendPostRequest(url, {value: normalizedValue}).pipe(
                tap(reaction => {
                    if (reaction.success) {
                        // Update our copy and notify clients
                        const config = this._config.getValue();
                        const newConfig = new Config(config.updateIn([section, option], (_) => normalizedValue));
                        this._config.next(newConfig);
                    }
                }),
                shareReplay(1)
            );
        }
    }

    protected onConnected() {
        // Retry the get
        this.refresh();
    }

    protected onDisconnected() {
        // Send null config
        this._restartRequiredOptions = {};
        this._config.next(null);
    }

    private getConfig() {
        this._logger.debug("Getting config...");
        this._restService.sendRequest(this.CONFIG_GET_URL).subscribe({
            next: reaction => {
                if (reaction.success) {
                    try {
                        const config_json: IConfigResponse = JSON.parse(reaction.data);
                        this._restartRequiredOptions = config_json.restart_required || {};
                        this._config.next(new Config(config_json));
                    } catch (error) {
                        this._logger.error("Failed to parse config response");
                        this._restartRequiredOptions = {};
                        this._config.next(null);
                    }
                } else {
                    this._restartRequiredOptions = {};
                    this._config.next(null);
                }
            }
        });
    }

    private normalizeValue(section: string, option: string, value: any): any {
        if (section === "lftp" && option === "net_socket_buffer") {
            return this.normalizeNetSocketBufferValue(value);
        }
        if (section === "logging" && option === "log_format") {
            return this.normalizeLogFormatValue(value);
        }
        return value;
    }

    private normalizeNetSocketBufferValue(value: any): string {
        const valueStr = String(value).trim();
        if (valueStr.length === 0) {
            return valueStr;
        }
        if (/^[0-9]+[kmg]$/i.test(valueStr)) {
            return valueStr.slice(0, -1) + valueStr.slice(-1).toUpperCase();
        }
        return valueStr;
    }

    private normalizeLogFormatValue(value: any): string {
        if (value === null || value === undefined) {
            return "";
        }
        const valueStr = String(value).trim();
        if (valueStr.length === 0) {
            return valueStr;
        }
        return valueStr.toLowerCase();
    }

    private wouldCreateBlankFtpsPassword(currentConfig: Config, section: string, option: string, value: any): boolean {
        if (section !== "lftp") {
            return false;
        }

        const protocol = option === "protocol"
            ? String(value).trim().toLowerCase()
            : currentConfig.getValue("lftp", "protocol");
        const remotePassword = option === "remote_password"
            ? value
            : currentConfig.getValue("lftp", "remote_password");
        return protocol === "ftps" && this.isBlankText(remotePassword);
    }

    private isBlankText(value: any): boolean {
        return value === null || value === undefined || (typeof value === "string" && value.trim().length === 0);
    }
}

/**
 * ConfigService factory and provider
 */
export let configServiceFactory = (
    _streamServiceRegistry: StreamServiceRegistry,
    _restService: RestService,
    _logger: LoggerService
) => {
  const configService = new ConfigService(_streamServiceRegistry, _restService, _logger);
  configService.onInit();
  // Bootstrap config even if the SSE stream is still waiting on auth.
  configService.refresh();
  return configService;
};

// noinspection JSUnusedGlobalSymbols
export let ConfigServiceProvider = {
    provide: ConfigService,
    useFactory: configServiceFactory,
    deps: [StreamServiceRegistry, RestService, LoggerService]
};
