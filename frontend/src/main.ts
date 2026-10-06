// Registers the shared lk-* elements (offline banner, update prompt, install hint) before bootstrap.
import '@lifekit-hq/elements';

import {bootstrapApplication} from '@angular/platform-browser';

import {AppComponent} from './app/app.component';
import {appConfig} from './app/app.config';

bootstrapApplication(AppComponent, appConfig).catch(err => console.error(err));
