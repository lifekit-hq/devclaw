import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {
    // Concurrent workers make Vite re-optimize deps mid-run, which can deadlock the runner or get
    // it OOM-killed in CI; serial files finish quickly.
    fileParallelism: false,
  },
});
