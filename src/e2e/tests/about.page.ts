import {browser, by, element} from 'protractor';
import {promise} from "selenium-webdriver";
import Promise = promise.Promise;

import {Urls} from "../urls";
import {App} from "./app";
import {loadAngularRoute} from "./route-bootstrap";

export class AboutPage extends App {
    navigateTo() {
        return loadAngularRoute(() => browser.get(Urls.APP_BASE_URL + "about"), "#title");
    }

    getVersion(): Promise<string> {
        return element(by.css("#version")).getText();
    }
}
