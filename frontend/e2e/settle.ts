export interface SettleClock<T> {
  scan: () => Promise<T>;
  sleep: (ms: number) => Promise<void>;
  now: () => number;
}

// Scans again while the scan still shows an unsettled route, until the deadline passes. Returns
// the settled scan, or fails with 'never settled' if the deadline passes first.
export const scanUntilSettled = async <T>(
  {scan, sleep, now}: SettleClock<T>,
  isUnsettled: (result: T) => boolean,
  timeoutMs: number,
  retryMs: number,
  what: string
): Promise<T> => {
  const deadline = now() + timeoutMs;
  let result = await scan();
  while (isUnsettled(result) && now() < deadline) {
    await sleep(retryMs);
    result = await scan();
  }
  if (isUnsettled(result)) {
    throw new Error(`${what} never settled: the scan still saw a skeleton`);
  }
  return result;
};
