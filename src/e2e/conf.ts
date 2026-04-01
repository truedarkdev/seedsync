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
    }
};
