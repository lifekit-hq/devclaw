import {type Routes} from '@angular/router';

import {AppShellComponent} from './shell/app-shell.component';

export const APP_ROUTES: Routes = [
  {
    path: '',
    component: AppShellComponent,
    children: [
      {path: '', pathMatch: 'full', redirectTo: 'needs-you'},
      {
        path: 'needs-you',
        title: 'Needs you · devclaw',
        loadComponent: () =>
          import('./needs-you/needs-you-page.component').then(m => m.NeedsYouPageComponent),
      },
      {
        path: 'goals',
        title: 'Goals · devclaw',
        loadComponent: () => import('./goals/goals-page.component').then(m => m.GoalsPageComponent),
      },
      {
        path: 'goals/:id',
        title: 'Goal · devclaw',
        loadComponent: () => import('./goals/goal-page.component').then(m => m.GoalPageComponent),
      },
      {
        path: 'projects',
        title: 'Projects · devclaw',
        loadComponent: () =>
          import('./projects/projects-page.component').then(m => m.ProjectsPageComponent),
      },
      {
        path: 'projects/:id',
        title: 'Project · devclaw',
        loadComponent: () =>
          import('./projects/project-page.component').then(m => m.ProjectPageComponent),
      },
      {
        path: 'sessions/:id',
        title: 'Session · devclaw',
        loadComponent: () =>
          import('./sessions/session-page.component').then(m => m.SessionPageComponent),
      },
      {
        path: 'verdicts',
        title: 'Verdicts · devclaw',
        loadComponent: () =>
          import('./verdicts/verdicts-page.component').then(m => m.VerdictsPageComponent),
      },
      {
        path: 'settings',
        title: 'Settings · devclaw',
        loadComponent: () =>
          import('./settings/settings-page.component').then(m => m.SettingsPageComponent),
      },
      {path: '**', redirectTo: 'needs-you'},
    ],
  },
];
