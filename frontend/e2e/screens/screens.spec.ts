import {existsSync, readdirSync} from 'node:fs';
import {join} from 'node:path';

import {defineScreenBaselines} from '@lifekit-hq/tokens/screen';
import {expect, test} from '@playwright/test';

import {ready, ROUTES} from '../routes';

// The look held still (lifekit-common's screen baselines): every route at 390 and 1280, light and
// dark, against the Linux images committed beside this file, compared exactly. A change that moves
// a pixel fails until the images are re-rendered on the CI runner (the "Update screen baselines"
// workflow) and reviewed; the pull request that changes one shows the screen before and after.
// The comparison is registered only once at least one image exists; until the workflow adds them
// there is nothing to compare against and nothing is registered.
const FROZEN_NOW = Date.parse('2026-11-01T12:00:00Z');

const SNAPSHOT_DIR = join(__dirname, 'screens.spec.ts-snapshots');
const hasBaselines =
  existsSync(SNAPSHOT_DIR) && readdirSync(SNAPSHOT_DIR).some(file => file.endsWith('-linux.png'));

if (hasBaselines) {
  defineScreenBaselines(
    {test, expect},
    {
      routes: ROUTES.map(({name, url}) => ({
        name,
        url,
        // Relative times ("5m ago") are read from the clock; the fixtures hold fixed dates.
        prepare: page => page.clock.setFixedTime(FROZEN_NOW),
        ready,
        // The release version moves with every release, not with the design.
        mask: page => [page.getByText(/\bv\d+\.\d+\.\d+\b/)],
      })),
    }
  );
}
