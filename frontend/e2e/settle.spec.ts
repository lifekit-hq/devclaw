import {expect, test} from '@playwright/test';

import {scanUntilSettled} from './settle';

// The gate's settle loop with a fake clock: no real waits, no browser.
const fakeClock = () => {
  let t = 0;
  return {
    now: () => t,
    sleep: async (ms: number) => {
      t += ms;
    },
  };
};

test('a scan that sees a skeleton is retried and the settled scan is returned', async () => {
  const results = ['skeleton', 'settled'];
  let scans = 0;
  const result = await scanUntilSettled(
    {...fakeClock(), scan: async () => results[scans++]},
    r => r === 'skeleton',
    60_000,
    1_000,
    'route'
  );
  expect(result).toBe('settled');
  expect(scans).toBe(2);
});

test('a route that never settles fails with never settled once the deadline has passed', async () => {
  const clock = fakeClock();
  let scans = 0;
  const run = scanUntilSettled(
    {
      ...clock,
      scan: async () => {
        scans++;
        return 'skeleton';
      },
    },
    r => r === 'skeleton',
    60_000,
    1_000,
    'route'
  );
  await expect(run).rejects.toThrow('never settled');
  expect(clock.now()).toBe(60_000);
  expect(scans).toBe(61);
});
