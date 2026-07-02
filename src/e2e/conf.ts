// Because this file imports from  protractor, you'll need to have it as a
// project dependency. Please see the reference config: lib/config.ts for more
// information.
//
// Why you might want to create your config with typescript:
// Editors like Microsoft Visual Studio Code will have autocomplete and
// description hints.
//
// To run this example, first transpile it to javascript with `npm run tsc`,
// then run `protractor conf.js`.
import {browser, Config} from 'protractor';

import {Urls} from "./urls";

let SpecReporter = require('jasmine-spec-reporter').SpecReporter;

const LEGACY_EXPECTATION_QUEUE_KEY = "__seedSyncLegacyExpectationQueue";
const LEGACY_EXPECTATION_PATCH_KEY = "__seedSyncLegacyExpectationPatchApplied";
const LEGACY_WRAPPED_MATCHER_KEY = "__seedSyncLegacyWrappedMatcher";
const REMEMBERED_BROWSER_SESSION_COOKIE_NAME = "seedsync_ui_session";
const REMEMBERED_BROWSER_SESSION_SECRET = process.env.SEEDSYNC_E2E_BROWSER_SESSION_SECRET || "";
const REMEMBERED_BROWSER_SESSION_TIMEOUT_MS = 10000;
const REMEMBERED_BROWSER_SESSION_POLL_MS = 250;
const REMEMBERED_BROWSER_SESSION_PAGE_SNIPPET_LIMIT = 900;
const REMEMBERED_BROWSER_SESSION_BODY_SNIPPET_LIMIT = 400;
const REMEMBERED_BROWSER_SHELL_TITLE = "SeedSync";

interface PageSignature {
    currentUrl: string;
    title: string;
    bodyText: string;
    pageSource: string;
    angularTestabilityCount: number;
    angularJsInjectorPresent: boolean;
}

function normalizeText(value: string): string {
    return (value || "").replace(/\s+/g, " ").trim();
}

function limitText(value: string, limit: number): string {
    const normalized = normalizeText(value);
    if (normalized.length <= limit) {
        return normalized;
    }

    return normalized.slice(0, limit) + "...";
}

function hasRememberedBrowserShellMarkers(pageSource: string): boolean {
    const normalizedPageSource = normalizeText(pageSource).toLowerCase();
    return normalizedPageSource.indexOf("<app-root") !== -1 &&
        normalizedPageSource.indexOf('id="top-sidebar"') !== -1 &&
        normalizedPageSource.indexOf('id="top-content"') !== -1 &&
        normalizedPageSource.indexOf('id="title-bar"') !== -1 &&
        normalizedPageSource.indexOf("<router-outlet") !== -1;
}

function hasBootstrapMarkers(pageSource: string, bodyText: string): boolean {
    const normalizedPageSource = normalizeText(pageSource).toLowerCase();
    const normalizedBodyText = normalizeText(bodyText).toLowerCase();
    return normalizedPageSource.indexOf("bootstrap-page") !== -1 ||
        normalizedPageSource.indexOf("bootstrap-form") !== -1 ||
        normalizedPageSource.indexOf("browser access") !== -1 ||
        normalizedBodyText.indexOf("browser access") !== -1;
}

function hasAngularBootstrap(signature: PageSignature): boolean {
    return signature.angularTestabilityCount > 0 || signature.angularJsInjectorPresent;
}

function isRememberedBrowserShell(signature: PageSignature): boolean {
    return normalizeText(signature.title) === REMEMBERED_BROWSER_SHELL_TITLE &&
        hasRememberedBrowserShellMarkers(signature.pageSource) &&
        !hasBootstrapMarkers(signature.pageSource, signature.bodyText) &&
        hasAngularBootstrap(signature);
}

async function capturePageSignature(): Promise<PageSignature> {
    const [currentUrl, title, pageSource, bodyText] = await Promise.all([
        browser.getCurrentUrl(),
        browser.getTitle(),
        browser.getPageSource(),
        browser.executeScript("return document.body ? document.body.innerText : '';")
    ]);
    const angularState: any = await browser.executeScript(
        "return {" +
        "angularTestabilityCount: (window.getAllAngularTestabilities && window.getAllAngularTestabilities().length) || 0," +
        "angularJsInjectorPresent: !!(window.angular && window.angular.element && document.body && window.angular.element(document.body).injector())" +
        "};"
    );

    return {
        currentUrl: String(currentUrl || ""),
        title: String(title || ""),
        bodyText: String(bodyText || ""),
        pageSource: String(pageSource || ""),
        angularTestabilityCount: Number(angularState && angularState.angularTestabilityCount) || 0,
        angularJsInjectorPresent: !!(angularState && angularState.angularJsInjectorPresent)
    };
}

function describePageSignature(signature: PageSignature): string {
    const normalizedPageSource = normalizeText(signature.pageSource).toLowerCase();
    const normalizedBodyText = normalizeText(signature.bodyText).toLowerCase();

    return [
        "url=" + normalizeText(signature.currentUrl),
        "title=" + normalizeText(signature.title),
        "body=" + limitText(signature.bodyText, REMEMBERED_BROWSER_SESSION_BODY_SNIPPET_LIMIT),
        "pageSource=" + limitText(signature.pageSource, REMEMBERED_BROWSER_SESSION_PAGE_SNIPPET_LIMIT),
        "shellMarkers={appRoot:" + (normalizedPageSource.indexOf("<app-root") !== -1) +
            ", topSidebar:" + (normalizedPageSource.indexOf('id="top-sidebar"') !== -1) +
            ", topContent:" + (normalizedPageSource.indexOf('id="top-content"') !== -1) +
            ", titleBar:" + (normalizedPageSource.indexOf('id="title-bar"') !== -1) +
            ", routerOutlet:" + (normalizedPageSource.indexOf("<router-outlet") !== -1) + "}",
        "angularReady={testabilities:" + signature.angularTestabilityCount +
            ", angularJsInjector:" + signature.angularJsInjectorPresent + "}",
        "bootstrapMarkers={bootstrapPage:" + (normalizedPageSource.indexOf("bootstrap-page") !== -1) +
            ", bootstrapForm:" + (normalizedPageSource.indexOf("bootstrap-form") !== -1) +
            ", browserAccessSource:" + (normalizedPageSource.indexOf("browser access") !== -1) +
            ", browserAccessBody:" + (normalizedBodyText.indexOf("browser access") !== -1) + "}"
    ].join("\n");
}

async function waitForRememberedBrowserShell(): Promise<void> {
    let lastSignature: PageSignature | null = null;
    const deadline = Date.now() + REMEMBERED_BROWSER_SESSION_TIMEOUT_MS;

    while (Date.now() < deadline) {
        lastSignature = await capturePageSignature();
        if (isRememberedBrowserShell(lastSignature)) {
            return;
        }

        await browser.sleep(REMEMBERED_BROWSER_SESSION_POLL_MS);
    }

    if (lastSignature === null) {
        lastSignature = await capturePageSignature();
    }

    throw new Error(
        "Remembered-session bootstrap did not reach an Angular-testable shell before Angular sync was restored.\n" +
        "Expected title SeedSync with app shell markers (#top-sidebar, #top-content, #title-bar, <router-outlet>)\n" +
        "and Angular testability before restoring Protractor sync.\n" +
        describePageSignature(lastSignature)
    );
}

function isPromiseLike(value: any): value is Promise<any> {
    return !!value && (typeof value === "object" || typeof value === "function") && typeof value.then === "function";
}

function waitForLegacyExpectationQueue(pendingExpectations: Array<Promise<any>>): Promise<void> {
    if (pendingExpectations.length === 0) {
        return Promise.resolve();
    }

    const batch = pendingExpectations.splice(0, pendingExpectations.length);
    return Promise.all(batch).then(() => waitForLegacyExpectationQueue(pendingExpectations));
}

function wrapLegacyMatcherContainer(container: any, pendingExpectations: Array<Promise<any>>): any {
    if (!container || typeof container !== "object") {
        return container;
    }

    if (container[LEGACY_WRAPPED_MATCHER_KEY]) {
        return container;
    }

    const wrappedContainer = new Proxy(container, {
        get(target, property, receiver) {
            const value = Reflect.get(target, property, receiver);
            if (typeof value === "function") {
                return function (...args: Array<any>) {
                    const result = value.apply(target, args);
                    if (isPromiseLike(result)) {
                        pendingExpectations.push(Promise.resolve(result));
                    }
                    return result;
                };
            }

            if (value && typeof value === "object") {
                return wrapLegacyMatcherContainer(value, pendingExpectations);
            }

            return value;
        }
    });

    Object.defineProperty(wrappedContainer, LEGACY_WRAPPED_MATCHER_KEY, {
        value: true,
        enumerable: false
    });

    return wrappedContainer;
}

function wrapLegacyJasmineFn(originalFn: Function, callbackIndex: number): Function {
    return function (...args: Array<any>) {
        const callback = args[callbackIndex];
        // Scope this bridge to legacy zero-arg Jasmine callbacks whose matcher
        // promises used to be tolerated implicitly; do not reinterpret done-style hooks/specs.
        if (typeof callback !== "function" || callback.length > 0) {
            return originalFn.apply(this, args);
        }

        args[callbackIndex] = function (...callbackArgs: Array<any>) {
            const globalState = global as any;
            const previousQueue = globalState[LEGACY_EXPECTATION_QUEUE_KEY];
            const pendingExpectations: Array<Promise<any>> = [];
            globalState[LEGACY_EXPECTATION_QUEUE_KEY] = pendingExpectations;

            return Promise.resolve(callback.apply(this, callbackArgs))
                .then(() => waitForLegacyExpectationQueue(pendingExpectations))
                .finally(() => {
                    globalState[LEGACY_EXPECTATION_QUEUE_KEY] = previousQueue;
                });
        };

        return originalFn.apply(this, args);
    };
}

function installLegacyExpectationCompatibilityPatch(): void {
    const globalState = global as any;
    if (globalState[LEGACY_EXPECTATION_PATCH_KEY]) {
        return;
    }

    const originalExpect = global.expect;
    global.expect = function (...args: Array<any>) {
        const expectation = originalExpect.apply(this, args);
        const pendingExpectations = globalState[LEGACY_EXPECTATION_QUEUE_KEY];
        if (!Array.isArray(pendingExpectations)) {
            return expectation;
        }

        return wrapLegacyMatcherContainer(expectation, pendingExpectations);
    };

    global.it = wrapLegacyJasmineFn(global.it, 1) as any;
    global.fit = wrapLegacyJasmineFn(global.fit, 1) as any;
    global.beforeEach = wrapLegacyJasmineFn(global.beforeEach, 0) as any;
    global.afterEach = wrapLegacyJasmineFn(global.afterEach, 0) as any;
    global.beforeAll = wrapLegacyJasmineFn(global.beforeAll, 0) as any;
    global.afterAll = wrapLegacyJasmineFn(global.afterAll, 0) as any;

    globalState[LEGACY_EXPECTATION_PATCH_KEY] = true;
}

async function primeRememberedBrowserSession(): Promise<void> {
    if (normalizeText(REMEMBERED_BROWSER_SESSION_SECRET) === "") {
        throw new Error("SEEDSYNC_E2E_BROWSER_SESSION_SECRET is required for remembered-session bootstrap");
    }

    const wasAngularEnabled = await browser.waitForAngularEnabled();
    await browser.waitForAngularEnabled(false);

    try {
        await browser.get(Urls.APP_BASE_URL);
        await browser.manage().deleteAllCookies();
        await browser.manage().addCookie({
            name: REMEMBERED_BROWSER_SESSION_COOKIE_NAME,
            value: REMEMBERED_BROWSER_SESSION_SECRET,
            path: "/"
        });
        await browser.get(Urls.APP_BASE_URL);
        await waitForRememberedBrowserShell();
    } finally {
        await browser.waitForAngularEnabled(wasAngularEnabled);
    }
}

export let config: Config = {
    framework: 'jasmine',
    SELENIUM_PROMISE_MANAGER: false,
    capabilities: {
        browserName: 'chrome',
        'goog:chromeOptions': { args: [
                '--headless',
                '--disable-gpu',
                '--no-sandbox',
                '--disable-extensions',
                '--disable-dev-shm-usage'
            ] },
    },
    specs: ['tests/**/*.spec.js'],
    seleniumAddress: Urls.SELENIUM_ADDRESS,

    // You could set no globals to true to avoid jQuery '$' and protractor '$'
    // collisions on the global namespace.
    noGlobals: true,

    allScriptsTimeout: 15000,

    // Options to be passed to Jasmine-node.
    jasmineNodeOpts: {
        showColors: true,
        defaultTimeoutInterval: 10000,
        print: function() {}
    },

    onPrepare: function () {
        browser.manage().timeouts().implicitlyWait(1000);
        installLegacyExpectationCompatibilityPatch();
        jasmine.getEnv().addReporter(new SpecReporter({
            spec: {
                displayStacktrace: true
            }
        }));
        return primeRememberedBrowserSession();
    }
};
