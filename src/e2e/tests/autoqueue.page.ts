import {browser, by, element, ExpectedConditions} from 'protractor';
import {promise as webdriverPromise} from "selenium-webdriver";

import {Urls} from "../urls";
import {App} from "./app";
import {loadAngularRoute} from "./route-bootstrap";

type WebdriverPromise<T> = webdriverPromise.Promise<T>;

export class AutoQueuePage extends App {
    private getAutoQueueSectionToggle() {
        return element(by.css("#heading-autoqueue button"));
    }

    private getAutoQueueSection() {
        return element(by.css("#collapse-autoqueue"));
    }

    private async isAutoQueueSectionDisplayed(): Promise<boolean> {
        try {
            return await this.getAutoQueueSection().isDisplayed();
        } catch (error) {
            if (this.isTransientDomReadError(error)) {
                return false;
            }
            throw error;
        }
    }

    private getPatternInput() {
        return element(by.css("#add-pattern input"));
    }

    private getAddPatternButton() {
        return element(by.css("#add-pattern .button"));
    }

    private async waitForPatternInputReady(): Promise<void> {
        await browser.wait(async () => {
            try {
                const input = this.getPatternInput();
                return await input.isDisplayed() && await input.isEnabled();
            } catch (error) {
                if (this.isTransientDomReadError(error)) {
                    return false;
                }
                throw error;
            }
        }, 10000);
        await browser.wait(ExpectedConditions.elementToBeClickable(this.getPatternInput()), 10000);
    }

    private async openAutoQueueSettings(): Promise<void> {
        await browser.wait(ExpectedConditions.elementToBeClickable(this.getAutoQueueSectionToggle()), 10000);

        if (!await this.isAutoQueueSectionDisplayed()) {
            try {
                await this.getAutoQueueSectionToggle().click();
            } catch (error) {
                if (!this.isTransientDomReadError(error)) {
                    throw error;
                }
                if (!await this.isAutoQueueSectionDisplayed()) {
                    await this.getAutoQueueSectionToggle().click();
                }
            }
        }

        await browser.wait(() => this.isAutoQueueSectionDisplayed(), 10000);
    }

    private async waitForPatternActionReady(button): Promise<void> {
        await browser.wait(async () => {
            try {
                return await button.isDisplayed() &&
                    (await button.getAttribute("aria-disabled")) !== "true";
            } catch (error) {
                if (this.isTransientDomReadError(error)) {
                    return false;
                }
                throw error;
            }
        }, 10000);
    }

    async navigateTo(): Promise<void> {
        await loadAngularRoute(() => browser.get(Urls.APP_BASE_URL + "autoqueue"), "#heading-autoqueue button");
        await this.getTopTitle("Settings");
        await this.openAutoQueueSettings();
        await this.waitForPatternInputReady();
    }

    getPatterns(): WebdriverPromise<Array<string>> {
        return element.all(by.css("#autoqueue .pattern span.text")).map(function (elm) {
            return browser.executeScript("return arguments[0].innerHTML;", elm).then((content: string) => {
                return content.trim();
            });
        });
    }

    async addPattern(pattern: string) {
        const input = this.getPatternInput();
        let patterns = element.all(by.css("#autoqueue .pattern span.text"));
        const button = this.getAddPatternButton();

        await this.waitForPatternInputReady();

        const count = await patterns.count();
        await input.click();
        await input.clear();
        await input.sendKeys(pattern);

        await this.waitForPatternActionReady(button);
        await browser.wait(ExpectedConditions.elementToBeClickable(button), 10000);
        await button.click();

        await browser.wait(async () => {
            return (await patterns.count()) === count + 1;
        }, 10000);
    }

    async removePattern(index: number) {
        let patterns = element.all(by.css("#autoqueue .pattern span.text"));
        const button = element.all(by.css("#autoqueue .pattern")).get(index).element(by.css(".button"));

        await this.waitForPatternActionReady(button);
        await browser.wait(ExpectedConditions.elementToBeClickable(button), 10000);

        const count = await patterns.count();
        await button.click();

        await browser.wait(async () => {
            return (await patterns.count()) === count - 1;
        }, 10000);
    }
}
