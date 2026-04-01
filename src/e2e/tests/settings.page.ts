import {browser, by, element} from 'protractor';
import {promise} from "selenium-webdriver";
import Promise = promise.Promise;

import {Urls} from "../urls";
import {App} from "./app";
import {loadAngularRoute} from "./route-bootstrap";

export class SettingsPage extends App {
    navigateTo() {
        return loadAngularRoute(() => browser.get(Urls.APP_BASE_URL + "settings"), "#title");
    }
}
