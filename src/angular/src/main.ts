import {enableProdMode} from '@angular/core';
import {bootstrapApplication} from '@angular/platform-browser';
import {provideHttpClient} from '@angular/common/http';
import {environment} from './environments/environment';

if (environment.production) {
    enableProdMode();
}

const path = window.location.pathname.replace(/\/+$/, "") || "/";
const isMigrationPath = path === "/migration" || path.startsWith("/migration/");

const bootstrap = path === "/migration/recovery"
    ? import("./app/pages/migration-recovery/migration-recovery.component").then(({MigrationRecoveryComponent}) =>
        bootstrapApplication(MigrationRecoveryComponent, {
            providers: [provideHttpClient()]
        })
    )
    : isMigrationPath
    ? import("./app/pages/migration/migration-app.component").then(({MigrationAppComponent}) =>
        bootstrapApplication(MigrationAppComponent, {
            providers: [provideHttpClient()]
        })
    )
    : Promise.all([
        import("@angular/router"),
        import("./app/pages/main/app.component"),
        import("./app/app.module"),
        import("./app/routes")
    ]).then(([{provideRouter}, {AppComponent}, {APP_PROVIDERS}, {ROUTES}]) =>
        bootstrapApplication(AppComponent, {
            providers: [provideHttpClient(), provideRouter(ROUTES), ...APP_PROVIDERS]
        })
    );

bootstrap.catch(error => console.error(error));
