import {enableProdMode} from '@angular/core';
import {bootstrapApplication} from '@angular/platform-browser';
import {provideHttpClient} from '@angular/common/http';
import {provideRouter} from '@angular/router';

import {AppComponent} from './app/pages/main/app.component';
import {APP_PROVIDERS} from './app/app.module';
import {ROUTES} from './app/routes';
import {environment} from './environments/environment';

if (environment.production) {
    enableProdMode();
}

bootstrapApplication(AppComponent, {
    providers: [provideHttpClient(), provideRouter(ROUTES), ...APP_PROVIDERS]
}).catch(error => console.error(error));
