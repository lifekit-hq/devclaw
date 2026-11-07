import {existsSync, readdirSync} from 'node:fs';
import {join} from 'node:path';

import {defineScreenBaselines} from '@lifekit-hq/tokens/screen';
import {expect, test} from '@playwright/test';

import {ready, ROUTES} from '../routes';

// The look held still (lifekit-common's screen baselines): every route at 390 and 1280, light and
// dark, against the Linux images committed beside this file, compared exactly. A change that moves
// a pixel fails until the images are re-rendered on the CI runner (the "Update screen baselines"
// workflow) and reviewed; the pull request that changes one shows the screen before and after.
// The comparison is registered once at least one Linux image exists, or on an update run
// (SCREEN_BASELINES=update, set by the "Update screen baselines" workflow); otherwise nothing is
// registered and one notice says why. Without the update clause the workflow's
// `-g "screen baseline"` would match no test and could never render the first images: with an empty
// snapshot folder and SCREEN_BASELINES=update the grep lists the 44 tests (11 routes x 2 sizes x 2
// schemes), and without it lists none. The follow-up pull request that commits the first images
// removes this condition and makes the gate unconditional: an empty or missing baseline directory
// then fails CI.
const FROZEN_NOW = Date.parse('2026-11-01T12:00:00Z');

const SNAPSHOT_DIR = join(__dirname, 'screens.spec.ts-snapshots');
const hasImages =
  existsSync(SNAPSHOT_DIR) && readdirSync(SNAPSHOT_DIR).some(file => file.endsWith('-linux.png'));
const updating = process.env['SCREEN_BASELINES'] === 'update';

if (!hasImages && !updating) {
  console.warn(
    'Screen baselines: comparison deferred until the first CI-rendered Linux baselines are committed.'
  );
}

if (hasImages || updating) {
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
