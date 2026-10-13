import {type Routes} from '@angular/router';
import {type PageChromeData} from '@lifekit-hq/ui';

import {AppShellComponent} from './shell/app-shell.component';

// What each page declares for the shell's top bar (title, back target); the pages render no
// heading or back link of their own.
const chrome = (data: PageChromeData): PageChromeData => data;

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
        title: 'Goal · devclaw',
        data: chrome({title: 'Goal', parent: '/goals'}),
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
        title: 'Project · devclaw',
        data: chrome({title: 'Project', parent: '/projects'}),
        loadComponent: () =>
          import('./projects/project-page.component').then(m => m.ProjectPageComponent),
      },
      // A session is reached from its goal or its project: no declared parent, so back follows
      // the history the app itself made.
      {
        path: 'sessions/:id',
        title: 'Session · devclaw',
        data: chrome({title: 'Session'}),
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
