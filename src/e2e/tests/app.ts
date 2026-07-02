import {browser, by, element} from 'protractor';
import {promise} from "selenium-webdriver";
import Promise = promise.Promise;

import {Urls} from "../urls";
import {loadAngularRoute} from "./route-bootstrap";

export class App {
    navigateTo() {
        return loadAngularRoute(() => browser.get(Urls.APP_BASE_URL), "#top-content");
    }

    getTitle(): Promise<string> {
        return browser.getTitle();
    }

    getSidebarItems(): Promise<Array<string>> {
        return element.all(by.css("#sidebar a span.text")).map(function (elm) {
            return browser.executeScript("return arguments[0].innerHTML;", elm);
        });
    }

    getTopTitle(): Promise<string> {
        return browser.executeScript("return arguments[0].innerHTML;", element(by.css("#title")));
    }
}
