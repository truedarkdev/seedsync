const selenium = require('selenium-webdriver');

const promise = selenium.promise || (selenium.promise = {});

class ControlFlow {
  execute(fn) {
    return Promise.resolve().then(fn);
  }

  once(_eventName, callback) {
    callback();
  }

  isIdle() {
    return true;
  }

  getSchedule() {
    return [];
  }
}

ControlFlow.EventType = { IDLE: 'idle' };

const scheduler = new ControlFlow();

function isPromise(value) {
  return !!value
      && (typeof value === 'object' || typeof value === 'function')
      && typeof value.then === 'function';
}

function fullyResolved(value) {
  if (Array.isArray(value)) {
    return Promise.all(value.map(fullyResolved));
  }

  if (value && typeof value === 'object' && !isPromise(value)) {
    const entries = Object.entries(value);
    return Promise.all(
        entries.map(async ([key, inner]) => [key, await fullyResolved(inner)]))
        .then((resolved) => Object.fromEntries(resolved));
  }

  return Promise.resolve(value);
}

promise.when = promise.when || ((value, cb, eb) => Promise.resolve(value).then(cb, eb));
promise.all = promise.all || ((values) => Promise.all(values));
promise.defer = promise.defer || (() => {
  let fulfill;
  let reject;
  const pending = new Promise((resolve, rejectPromise) => {
    fulfill = resolve;
    reject = rejectPromise;
  });
  return { promise: pending, fulfill, reject, resolve: fulfill };
});
promise.fulfilled = promise.fulfilled || ((value) => Promise.resolve(value));
promise.rejected = promise.rejected || ((err) => Promise.reject(err));
promise.isPromise = promise.isPromise || isPromise;
promise.fullyResolved = promise.fullyResolved || fullyResolved;
promise.USE_PROMISE_MANAGER = false;
promise.controlFlow = promise.controlFlow || (() => scheduler);
promise.ControlFlow = promise.ControlFlow || ControlFlow;

function wrapDriver(proto) {
  if (!proto) {
    return;
  }

  if (!proto.controlFlow) {
    proto.controlFlow = function controlFlow() {
      return scheduler;
    };
  }

  if (!proto.schedule) {
    proto.schedule = function schedule(command) {
      return this.execute(command);
    };
  }

  if (proto.__protractorManageShimApplied) {
    return;
  }

  const originalManage = proto.manage;
  if (typeof originalManage !== 'function') {
    return;
  }

  proto.manage = function manage() {
    const options = originalManage.call(this);
    if (options && typeof options.timeouts !== 'function' && typeof options.setTimeouts === 'function') {
      options.timeouts = () => ({
        implicitlyWait: (ms) => options.setTimeouts({ implicit: ms }),
        setScriptTimeout: (ms) => options.setTimeouts({ script: ms }),
        pageLoadTimeout: (ms) => options.setTimeouts({ pageLoad: ms }),
      });
    }
    return options;
  };

  proto.__protractorManageShimApplied = true;
}

wrapDriver(selenium.ThenableWebDriver && selenium.ThenableWebDriver.prototype);
wrapDriver(selenium.WebDriver && selenium.WebDriver.prototype);

module.exports = selenium;
