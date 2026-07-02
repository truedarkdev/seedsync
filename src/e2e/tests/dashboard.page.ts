import {browser, by, element, ExpectedConditions} from 'protractor';
import {promise as webdriverPromise} from "selenium-webdriver";

import {Urls} from "../urls";
import {App} from "./app";
import {loadAngularRoute} from "./route-bootstrap";

type WebdriverPromise<T> = webdriverPromise.Promise<T>;

export class File {
    constructor(public name,
                public status,
                public size,
                public progress?,
                public speed?,
                public eta?) {
    }
}

export class FileActionButtonState {
    constructor(public title,
                public isEnabled) {
    }
}

export class DashboardPage extends App {
    private getFileRows() {
        return element.all(by.css("#file-list .file"));
    }

    private getFileRow(index: number) {
        return this.getFileRows().get(index);
    }

    private getFileRowById(fileId: string) {
        return this.getFileRows()
            .filter(row => {
                return row.getAttribute("data-file-id").then(value => value === fileId);
            })
            .first();
    }

    private getActionButtons(row) {
        return row.element(by.css(".actions")).all(by.css(".button"));
    }

    private async isActionContainerVisible(row): Promise<boolean> {
        const actions = row.element(by.css(".actions"));
        try {
            return await actions.isPresent();
        } catch {
            return false;
        }
    }

    private waitForActionButtons(index: number) {
        return browser.wait(async () => this.isActionContainerVisible(this.getFileRow(index)), 10000);
    }

    private waitForActionButtonsById(fileId: string) {
        return browser.wait(async () => this.isActionContainerVisible(this.getFileRowById(this.requireFileId(fileId))), 10000);
    }

    private waitForFileRowCount(minimumCount: number) {
        return browser.wait(() => {
            return this.getFileRows().count().then(count => count >= minimumCount);
        }, 10000);
    }

    private async getActionButtonByTitle(fileId: string, title: string) {
        await this.waitForActionButtonsById(fileId);
        return this.getActionButtons(this.getFileRowById(this.requireFileId(fileId)))
            .filter(buttonElm => {
                return browser.executeScript(
                    "return arguments[0].innerHTML;",
                    buttonElm.element(by.css("div.text span"))
                ).then((content: string) => (content || "").trim() === title);
            })
            .first();
    }

    private getFileProgress(index: number) {
        return this.getFileRow(index)
            .element(by.css(".size .progress-bar"))
            .getAttribute("aria-valuenow")
            .then(value => +value);
    }

    private getOptionalRowText(index: number, containerSelector: string, spanSelector: string) {
        return this.getFileRow(index)
            .element(by.css(containerSelector))
            .isElementPresent(by.css("span"))
            .then(value => {
                if(value) {
                    return this.getFileRow(index)
                        .element(by.css(spanSelector))
                        .getText()
                        .then(text => (text || "").trim());
                }
                return "";
            });
    }

    private requireFileIndex(name: string, index: number) {
        if (index < 0) {
            throw new Error("Unable to find dashboard row for " + name);
        }

        return index;
    }

    private requireFileId(fileId: string) {
        if (fileId == null || fileId === "") {
            throw new Error("Unable to find dashboard row identity");
        }

        return fileId;
    }

    async navigateTo(): Promise<void> {
        await loadAngularRoute(() => browser.get(Urls.APP_BASE_URL + "dashboard"), "#file-list .file");
        // Wait for the files list to show up.
        await browser.wait(ExpectedConditions.presenceOf(
            element.all(by.css("#file-list .file")).first()
        ), 10000);
        await browser.wait(ExpectedConditions.visibilityOf(
            element.all(by.css("#file-list .file")).first()
        ), 10000);
        await this.waitForFileRowCount(1);
    }

    async reload(): Promise<void> {
        await loadAngularRoute(() => browser.refresh(), "#file-list .file");
        await browser.wait(ExpectedConditions.presenceOf(
            element.all(by.css("#file-list .file")).first()
        ), 10000);
        await browser.wait(ExpectedConditions.visibilityOf(
            element.all(by.css("#file-list .file")).first()
        ), 10000);
        await this.waitForFileRowCount(1);
    }

    getFiles(): WebdriverPromise<Array<File>> {
        return element.all(by.css("#file-list .file")).map(function (elm) {
            let name = browser.executeScript(
                "return arguments[0].firstChild && arguments[0].firstChild.textContent;",
                elm.element(by.css(".name .title"))
            ).then((content: string) => (content || "").trim());
            let statusElm = elm.element(by.css(".content .status"));
            let status = statusElm.isElementPresent(by.css("span.text")).then(value => {
                if(value) {
                    return browser.executeScript(
                        "return arguments[0].innerHTML;",
                        statusElm.element(by.css("span.text"))
                    ).then((content: string) => (content || "").trim());
                } else {
                    return "";
                }
            });
            let size = elm.element(by.css(".size .size_info")).getText();
            return new File(name, status, size);
        });
    }

    getFileByName(name: string) {
        return this.getFiles().then(files => {
            return files.find(file => file.name === name);
        });
    }

    getFileIdByIndex(index: number) {
        return this.getFileRow(index).getAttribute("data-file-id");
    }

    getFileIdByName(name: string) {
        return this.findFileIndexByName(name).then(index => this.getFileIdByIndex(this.requireFileIndex(name, index)));
    }

    getFileNameByIndex(index: number) {
        return browser.executeScript(
            "return arguments[0].firstChild && arguments[0].firstChild.textContent;",
            this.getFileRow(index).element(by.css(".name .title"))
        ).then((content: string) => (content || "").trim());
    }

    getFileStatusByIndex(index: number) {
        const statusElm = this.getFileRow(index).element(by.css(".content .status"));
        return statusElm.isElementPresent(by.css("span.text")).then(value => {
            if(value) {
                return browser.executeScript(
                    "return arguments[0].innerHTML;",
                    statusElm.element(by.css("span.text"))
                ).then((content: string) => (content || "").trim());
            }
            return "";
        });
    }

    getFileStatusById(fileId: string) {
        const statusElm = this.getFileRowById(this.requireFileId(fileId)).element(by.css(".content .status"));
        return statusElm.isElementPresent(by.css("span.text")).then(value => {
            if(value) {
                return browser.executeScript(
                    "return arguments[0].innerHTML;",
                    statusElm.element(by.css("span.text"))
                ).then((content: string) => (content || "").trim());
            }
            return "";
        });
    }

    getFileProgressById(fileId: string) {
        return this.getFileRowById(this.requireFileId(fileId))
            .element(by.css(".size .progress-bar"))
            .getAttribute("aria-valuenow")
            .then(value => +value);
    }

    getFileProgressByName(name: string) {
        return this.findFileIndexByName(name).then(index => this.getFileProgress(this.requireFileIndex(name, index)));
    }

    getFileSpeedById(fileId: string) {
        return this.getFileRowById(this.requireFileId(fileId))
            .element(by.css(".speed"))
            .isElementPresent(by.css("span"))
            .then(value => {
                if(value) {
                    return this.getFileRowById(fileId)
                        .element(by.css(".speed span"))
                        .getText()
                        .then(text => (text || "").trim());
                }
                return "";
            });
    }

    getFileSpeedByName(name: string) {
        return this.findFileIndexByName(name).then(index => {
            return this.getOptionalRowText(this.requireFileIndex(name, index), ".speed", ".speed span");
        });
    }

    getFileEtaById(fileId: string) {
        return this.getFileRowById(this.requireFileId(fileId))
            .element(by.css(".eta"))
            .isElementPresent(by.css("span"))
            .then(value => {
                if(value) {
                    return this.getFileRowById(fileId)
                        .element(by.css(".eta span"))
                        .getText()
                        .then(text => (text || "").trim());
                }
                return "";
            });
    }

    getFileEtaByName(name: string) {
        return this.findFileIndexByName(name).then(index => {
            return this.getOptionalRowText(this.requireFileIndex(name, index), ".eta", ".eta span");
        });
    }

    findFileIndexByName(name: string) {
        return this.getFiles().then(files => {
            return files.findIndex(file => file.name === name);
        });
    }

    findFileIndexByStatus(status: string) {
        return this.getFiles().then(files => {
            return files.findIndex(file => file.status === status);
        });
    }

    async selectFile(index: number) {
        const row = this.getFileRow(index);
        const wasVisible = await this.isActionContainerVisible(row);
        await browser.wait(ExpectedConditions.elementToBeClickable(row), 10000);
        await row.click();
        await browser.wait(async () => {
            return (await this.isActionContainerVisible(this.getFileRow(index))) !== wasVisible;
        }, 10000);
    }

    async selectFileById(fileId: string) {
        const row = this.getFileRowById(this.requireFileId(fileId));
        const wasVisible = await this.isActionContainerVisible(row);
        await browser.wait(ExpectedConditions.elementToBeClickable(row), 10000);
        await row.click();
        await browser.wait(async () => {
            return (await this.isActionContainerVisible(this.getFileRowById(this.requireFileId(fileId)))) !== wasVisible;
        }, 10000);
    }

    selectFileByName(name: string) {
        return this.findFileIndexByName(name).then(index => {
            return this.selectFile(this.requireFileIndex(name, index));
        });
    }

    isFileActionsVisible(index: number) {
        return this.isActionContainerVisible(this.getFileRow(index));
    }

    isFileActionsVisibleByName(name: string) {
        return this.findFileIndexByName(name).then(index => this.isFileActionsVisible(this.requireFileIndex(name, index)));
    }

    async getFileActions(index: number): Promise<Array<FileActionButtonState>> {
        await this.waitForActionButtons(index);
        const row = this.getFileRow(index);
        return this.getActionButtons(row)
            .map(buttonElm => {
                let title = browser.executeScript(
                    "return arguments[0].innerHTML;",
                    buttonElm.element(by.css("div.text span"))
                ).then((content: string) => (content || "").trim());
                let isEnabled = buttonElm.getAttribute("disabled").then(value => {
                    return value == null;
                });
                return new FileActionButtonState(title, isEnabled);
            });
    }

    getFileActionsByName(name: string) {
        return this.findFileIndexByName(name).then(index => this.getFileActions(this.requireFileIndex(name, index)));
    }

    stopFileById(fileId: string) {
        return this.getActionButtonByTitle(fileId, "Stop").then(button => {
            return browser.wait(ExpectedConditions.elementToBeClickable(button), 10000).then(() => {
                return button.click().then(() => {
                    return browser.waitForAngular();
                });
            });
        });
    }

    queueFileById(fileId: string) {
        return this.getActionButtonByTitle(fileId, "Queue").then(button => {
            return browser.wait(ExpectedConditions.elementToBeClickable(button), 10000).then(() => {
                return button.click().then(() => {
                    return browser.waitForAngular();
                });
            });
        });
    }

    waitForFileStatusById(fileId: string, expectedStatus: string) {
        return browser.wait(() => {
            return this.getFileRowById(fileId).isPresent().then(present => {
                if (!present) {
                    return false;
                }

                return this.getFileStatusById(fileId).then(status => status === expectedStatus);
            });
        }, 20000);
    }

    waitForFileStatusNotById(fileId: string, unexpectedStatus: string) {
        return browser.wait(() => {
            return this.getFileRowById(fileId).isPresent().then(present => {
                if (!present) {
                    return false;
                }

                return this.getFileStatusById(fileId).then(status => status !== unexpectedStatus);
            });
        }, 20000);
    }

    private async getActionStateByTitle(fileId: string, title: string) {
        const button = await this.getActionButtonByTitle(fileId, title);
        const value = await button.getAttribute("disabled");
        return new FileActionButtonState(title, value == null);
    }

    getFileActionByTitle(fileId: string, title: string) {
        return this.getActionStateByTitle(this.requireFileId(fileId), title);
    }
}
