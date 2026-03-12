import {browser, by, element, ExpectedConditions} from 'protractor';
import {promise} from "selenium-webdriver";
import Promise = promise.Promise;

import {Urls} from "../urls";
import {App} from "./app";

export class AutoQueuePage extends App {
    navigateTo() {
        return browser.get(Urls.APP_BASE_URL + "autoqueue").then(() => {
            return browser.waitForAngular().then(() => {
                return browser.wait(
                    ExpectedConditions.presenceOf(element(by.css("#add-pattern input"))),
                    10000
                );
            });
        });
    }

    getPatterns(): Promise<Array<string>> {
        return element.all(by.css("#autoqueue .pattern span.text")).map(function (elm) {
            return browser.executeScript("return arguments[0].innerHTML;", elm).then((content: string) => {
                return content.trim();
            });
        });
    }

    addPattern(pattern: string) {
        let input = element(by.css("#add-pattern input"));
        let patterns = element.all(by.css("#autoqueue .pattern span.text"));
        let button = element(by.css("#add-pattern .button"));
        return patterns.count().then(count => {
            return input.clear().then(() => {
                return input.sendKeys(pattern);
            }).then(() => {
                return button.click().then(() => {
                    return browser.waitForAngular().then(() => {
                        return browser.wait(() => {
                            return patterns.count().then(updatedCount => updatedCount === count + 1);
                        }, 10000);
                    });
                });
            });
        });
    }

    removePattern(index: number) {
        let patterns = element.all(by.css("#autoqueue .pattern span.text"));
        let button = element.all(by.css("#autoqueue .pattern")).get(index).element(by.css(".button"));
        return patterns.count().then(count => {
            return button.click().then(() => {
                return browser.waitForAngular().then(() => {
                    return browser.wait(() => {
                        return patterns.count().then(updatedCount => updatedCount === count - 1);
                    }, 10000);
                });
            });
        });
    }
}
