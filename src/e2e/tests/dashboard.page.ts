import {browser, by, element, ExpectedConditions} from 'protractor';
import {promise} from "selenium-webdriver";
import Promise = promise.Promise;

import {Urls} from "../urls";
import {App} from "./app";

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
    private getFileRow(index: number) {
        return element.all(by.css("#file-list .file")).get(index);
    }

    private getFileRowById(fileId: string) {
        return element.all(by.css("#file-list .file"))
            .filter(row => {
                return row.getAttribute("data-file-id").then(value => value === fileId);
            })
            .first();
    }

    private getActionButtonByTitle(row, title: string) {
        return row
            .element(by.css(".actions"))
            .all(by.css(".button"))
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

    navigateTo() {
        return browser.get(Urls.APP_BASE_URL + "dashboard").then(value => {
            return browser.waitForAngular().then(() => {
                // Wait for the files list to show up
                return browser.wait(ExpectedConditions.presenceOf(
                    element.all(by.css("#file-list .file")).first()
                ), 10000).then(() => {
                    return browser.wait(ExpectedConditions.visibilityOf(
                        element.all(by.css("#file-list .file")).first()
                    ), 10000);
                });
            });
        })
    }

    reload() {
        return browser.refresh().then(() => {
            return browser.waitForAngular().then(() => {
                return browser.wait(ExpectedConditions.presenceOf(
                    element.all(by.css("#file-list .file")).first()
                ), 10000).then(() => {
                    return browser.wait(ExpectedConditions.visibilityOf(
                        element.all(by.css("#file-list .file")).first()
                    ), 10000);
                });
            });
        });
    }

    getFiles(): Promise<Array<File>> {
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

    selectFile(index: number) {
        return this.getFileRow(index).click().then(() => {
            return browser.waitForAngular();
        });
    }

    selectFileById(fileId: string) {
        return this.getFileRowById(this.requireFileId(fileId)).click().then(() => {
            return browser.waitForAngular();
        });
    }

    selectFileByName(name: string) {
        return this.findFileIndexByName(name).then(index => {
            return this.selectFile(this.requireFileIndex(name, index));
        });
    }

    isFileActionsVisible(index: number) {
        return this.getFileRow(index)
                            .element(by.css(".actions")).isDisplayed();
    }

    getFileActions(index: number): Promise<Array<FileActionButtonState>> {
        return this.getFileRow(index)
            .element(by.css(".actions"))
            .all(by.css(".button"))
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
        return this.getActionButtonByTitle(this.getFileRowById(this.requireFileId(fileId)), "Stop").click().then(() => {
            return browser.waitForAngular();
        });
    }

    queueFileById(fileId: string) {
        return this.getActionButtonByTitle(this.getFileRowById(this.requireFileId(fileId)), "Queue").click().then(() => {
            return browser.waitForAngular();
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

    private getActionStateByTitle(row, title: string) {
        return this.getActionButtonByTitle(row, title)
            .getAttribute("disabled")
            .then(value => new FileActionButtonState(title, value == null));
    }

    getFileActionByTitle(fileId: string, title: string) {
        return this.getActionStateByTitle(this.getFileRowById(this.requireFileId(fileId)), title);
    }
}
