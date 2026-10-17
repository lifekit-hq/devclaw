import {type ActivatedRouteSnapshot, type Route, type Routes} from '@angular/router';
import {type PageChromeData} from '@lifekit-hq/ui';

import {AppShellComponent} from './shell/app-shell.component';

// What each page declares for the shell's top bar (title, back target); the pages render no
// heading or back link of their own.
const chrome = (data: PageChromeData): PageChromeData => data;

const SHORT_ID = 8;

/**
 * A detail page names itself from its route param, once: the top bar's title and the tab title
 * come from this one declaration, so the page body renders no heading of its own.
 */
const named = (name: (id: string) => string): Pick<Route, 'title' | 'resolve'> => {
  const barTitle = (route: ActivatedRouteSnapshot): string => name(route.paramMap.get('id') ?? '');
  return {
    title: route => `${barTitle(route)} · devclaw`,
    resolve: {title: barTitle},
  };
};

export const APP_ROUTES: Routes = [
  {
    path: '',
    component: AppShellComponent,
    children: [
      {path: '', pathMatch: 'full', redirectTo: 'needs-you'},
      {
        path: 'needs-you',
        title: 'Needs you · devclaw',
        data: chrome({title: 'Needs you'}),
        loadComponent: () =>
          import('./needs-you/needs-you-page.component').then(m => m.NeedsYouPageComponent),
      },
      {
        path: 'goals',
        title: 'Goals · devclaw',
        data: chrome({title: 'Goals'}),
        loadComponent: () => import('./goals/goals-page.component').then(m => m.GoalsPageComponent),
      },
      {
        path: 'goals/:id',
        ...named(id => `Goal ${id}`),
        data: chrome({parent: '/goals'}),
        loadComponent: () => import('./goals/goal-page.component').then(m => m.GoalPageComponent),
      },
      {
        path: 'projects',
        title: 'Projects · devclaw',
        data: chrome({title: 'Projects'}),
        loadComponent: () =>
          import('./projects/projects-page.component').then(m => m.ProjectsPageComponent),
      },
      {
        path: 'projects/:id',
        ...named(id => `Project ${id}`),
        data: chrome({parent: '/projects'}),
        loadComponent: () =>
          import('./projects/project-page.component').then(m => m.ProjectPageComponent),
      },
      // A session is reached from its goal or its project: no declared parent, so back follows
      // the history the app itself made.
      {
        path: 'sessions/:id',
        ...named(id => `Session ${id.slice(0, SHORT_ID)}`),
        data: chrome({}),
        loadComponent: () =>
          import('./sessions/session-page.component').then(m => m.SessionPageComponent),
      },
      {
        path: 'verdicts',
        title: 'Verdicts · devclaw',
        data: chrome({title: 'Verdicts'}),
        loadComponent: () =>
          import('./verdicts/verdicts-page.component').then(m => m.VerdictsPageComponent),
      },
      {
        path: 'more',
        title: 'More · devclaw',
        data: chrome({title: 'More'}),
        loadComponent: () => import('./shell/more-page.component').then(m => m.MorePageComponent),
      },
      {
        path: 'settings',
        title: 'Settings · devclaw',
        data: chrome({title: 'Settings', parent: '/needs-you'}),
        loadComponent: () =>
          import('./settings/settings-page.component').then(m => m.SettingsPageComponent),
      },
      {path: '**', redirectTo: 'needs-you'},
    ],
  },
];
