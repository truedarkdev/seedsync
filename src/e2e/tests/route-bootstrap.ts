import {browser} from 'protractor';

const ROUTE_READY_TIMEOUT = 10000;

async function waitForAngularBootstrap(readySelector: string): Promise<void> {
    await browser.wait(async () => {
        return browser.executeScript(
            "return document.readyState === 'complete' && " +
                "document.querySelector(arguments[0]) !== null && " +
                "!!((window.getAllAngularTestabilities && window.getAllAngularTestabilities().length > 0) || " +
                "(window.angular && window.angular.element(document.body).injector()));",
            readySelector
        );
    }, ROUTE_READY_TIMEOUT);
}

export async function loadAngularRoute(routeAction: () => any, readySelector: string): Promise<void> {
    const wasAngularEnabled = await browser.waitForAngularEnabled();
    await browser.waitForAngularEnabled(false);

    try {
        await routeAction();
        await waitForAngularBootstrap(readySelector);
    } finally {
        await browser.waitForAngularEnabled(wasAngularEnabled);
    }
}
