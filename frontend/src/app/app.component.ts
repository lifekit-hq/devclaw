import {ChangeDetectionStrategy, Component} from '@angular/core';
import {RouterOutlet} from '@angular/router';

/** The router outlet: the PWA prompts live in the shell's notice slot. */
@Component({
  selector: 'dc-root',
  imports: [RouterOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {class: 'block h-full'},
  template: '<router-outlet />',
})
export class AppComponent {}
