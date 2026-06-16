import {Injectable} from "@angular/core";
import {BehaviorSubject, Observable} from "rxjs";

import {Config, IConfig} from "./config";
import {LoggerService} from "../utils/logger.service";
import {BaseWebService} from "../base/base-web.service";
import {Localization} from "../../common/localization";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {RestService, WebReaction} from "../utils/rest.service";


/**
 * ConfigService provides the store for the config
 */
@Injectable()
export class ConfigService extends BaseWebService {
    private readonly CONFIG_GET_URL = "/server/config/get";

    private readonly CONFIG_SET_URL =
        (section, option) => `/server/config/set/${section}/${option}`

    private _config: BehaviorSubject<Config> = new BehaviorSubject(null);

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
            (option === "remote_password" || option === "net_socket_buffer");
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
        } else {
            const url = this.CONFIG_SET_URL(section, option);
            const obs = this._restService.sendPostRequest(url, {value: normalizedValue});
            obs.subscribe({
                next: reaction => {
                    if (reaction.success) {
                        // Update our copy and notify clients
                        const config = this._config.getValue();
                        const newConfig = new Config(config.updateIn([section, option], (_) => normalizedValue));
                        this._config.next(newConfig);
                    }
                }
            });
            return obs;
        }
    }

    protected onConnected() {
        // Retry the get
        this.getConfig();
    }

    protected onDisconnected() {
        // Send null config
        this._config.next(null);
    }

    private getConfig() {
        this._logger.debug("Getting config...");
        this._restService.sendRequest(this.CONFIG_GET_URL).subscribe({
            next: reaction => {
                if (reaction.success) {
                    try {
                        const config_json: IConfig = JSON.parse(reaction.data);
                        this._config.next(new Config(config_json));
                    } catch (error) {
                        this._logger.error("Failed to parse config response");
                        this._config.next(null);
                    }
                } else {
                    this._config.next(null);
                }
            }
        });
    }

    private normalizeValue(section: string, option: string, value: any): any {
        if (section === "lftp" && option === "net_socket_buffer") {
            return this.normalizeNetSocketBufferValue(value);
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
  return configService;
};

// noinspection JSUnusedGlobalSymbols
export let ConfigServiceProvider = {
    provide: ConfigService,
    useFactory: configServiceFactory,
    deps: [StreamServiceRegistry, RestService, LoggerService]
};
