import {ChangeDetectionStrategy, Component, CUSTOM_ELEMENTS_SCHEMA, inject} from '@angular/core';
import {RouterOutlet} from '@angular/router';
import {AppUpdateService} from '@lifekit-hq/core/pwa';

/** The router outlet plus the PWA's offline banner, update prompt and install hint. */
@Component({
  selector: 'dc-root',
  imports: [RouterOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
  host: {class: 'block h-full'},
  template: `
    <router-outlet />

    <div
      class="pointer-events-none fixed inset-x-cmn-4 top-[calc(env(safe-area-inset-top)+0.5rem)] z-50 mx-auto flex max-w-md flex-col gap-cmn-2 *:pointer-events-auto *:rounded-cmn-lg *:shadow-lg"
    >
      <lk-offline-banner />
      <lk-update-prompt
        [ready]="update.updateReady()"
        (lk-update-prompt-reload)="update.reload()"
      />
      <lk-install-hint />
    </div>
  `,
})
export class AppComponent {
  protected readonly update = inject(AppUpdateService);
}
