import {browser, by, element} from 'protractor';
import {promise} from "selenium-webdriver";
import WebdriverPromise = promise.Promise;

import {Urls} from "../urls";
import {loadAngularRoute} from "./route-bootstrap";

export class App {
    protected isTransientDomReadError(error: any): boolean {
        return error != null && (
            error.name === "NoSuchElementError" ||
            error.name === "StaleElementReferenceError"
        );
    }

    navigateTo() {
        return loadAngularRoute(() => browser.get(Urls.APP_BASE_URL), "#top-content");
    }

    getTitle(): WebdriverPromise<string> {
        return browser.getTitle();
    }

    getSidebarItems(): WebdriverPromise<Array<string>> {
        return element.all(by.css("#sidebar a span.text")).map(function (elm) {
            return browser.executeScript("return arguments[0].innerHTML;", elm);
        });
    }

    getTopTitle(expectedTitle?: string): WebdriverPromise<string> {
        let currentTitle = "";
        return browser.wait(async () => {
            try {
                const title = element(by.css("#title"));
                if (!await title.isDisplayed()) {
                    return false;
                }

                const visibleTitle = (await title.getText()).trim();
                if (visibleTitle === "" || (expectedTitle != null && visibleTitle !== expectedTitle)) {
                    return false;
                }

                currentTitle = visibleTitle;
                return true;
            } catch (error) {
                if (this.isTransientDomReadError(error)) {
                    return false;
                }
                throw error;
            }
        }, 10000).then(() => currentTitle);
    }
}
