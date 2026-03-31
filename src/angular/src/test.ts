import 'zone.js/testing';
import { getTestBed, TestBed } from '@angular/core/testing';
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting
} from '@angular/platform-browser-dynamic/testing';

declare module '@angular/core/testing' {
  interface TestBedStatic {
    get(token: any, notFoundValue?: any): any;
  }
}

const testBedCompat = TestBed as typeof TestBed & {
  get?: (token: any, notFoundValue?: any) => any;
};

if (typeof testBedCompat.get !== 'function') {
  testBedCompat.get = testBedCompat.inject.bind(TestBed);
}

getTestBed().initTestEnvironment(
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting()
);
