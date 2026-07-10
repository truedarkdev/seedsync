import { InjectionToken } from "@angular/core";

export interface StorageService {
  get(key: string): any;
  set(key: string, value: any): void;
  remove(key: string): void;
}

export const LOCAL_STORAGE = new InjectionToken<StorageService>("LOCAL_STORAGE");

class BrowserStorageService implements StorageService {
  private getStorage(): Storage | null {
    try {
      return window.localStorage;
    } catch (_error) {
      return null;
    }
  }

  get(key: string): any {
    const storage = this.getStorage();
    if (storage == null) {
      return null;
    }

    const value = storage.getItem(key);
    if (value == null) {
      return null;
    }

    try {
      return JSON.parse(value);
    } catch (_error) {
      return value;
    }
  }

  set(key: string, value: any): void {
    const storage = this.getStorage();
    if (storage == null) {
      return;
    }

    storage.setItem(key, JSON.stringify(value));
  }

  remove(key: string): void {
    const storage = this.getStorage();
    if (storage == null) {
      return;
    }

    storage.removeItem(key);
  }
}

export function localStorageFactory(): StorageService {
  return new BrowserStorageService();
}

export const STORAGE_SERVICE_PROVIDER = {
  provide: LOCAL_STORAGE,
  useFactory: localStorageFactory
};
