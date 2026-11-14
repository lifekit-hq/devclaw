// Renders the iOS launch images (`apple-touch-startup-image`) into public/startup: the app icon
// centred on the first-paint background of each colour scheme (the manifest/theme-color values),
// one image per iPhone portrait size. Run `npm run startup-images` and commit the PNGs; the
// matching <link> tags live in src/index.html.
import {readFileSync} from 'node:fs';
import {mkdir} from 'node:fs/promises';
import {chromium} from '@playwright/test';

const ICON = new URL(
  '../node_modules/@lifekit-hq/tokens/brand/dist/dc/icon-512.png',
  import.meta.url
);
const OUT = new URL('../public/startup/', import.meta.url);
const ICON_CSS_PX = 128;

const SCHEMES = {light: '#f3f5f6', dark: '#0c1113'};
// CSS device width x height and pixel ratio of each iPhone portrait launch screen.
const DEVICES = [
  {width: 440, height: 956, ratio: 3},
  {width: 430, height: 932, ratio: 3},
  {width: 428, height: 926, ratio: 3},
  {width: 402, height: 874, ratio: 3},
  {width: 393, height: 852, ratio: 3},
  {width: 390, height: 844, ratio: 3},
  {width: 414, height: 896, ratio: 3},
  {width: 375, height: 812, ratio: 3},
  {width: 414, height: 896, ratio: 2},
  {width: 375, height: 667, ratio: 2},
];

const icon = `data:image/png;base64,${readFileSync(ICON).toString('base64')}`;
await mkdir(OUT, {recursive: true});
const browser = await chromium.launch();
try {
  for (const {width, height, ratio} of DEVICES) {
    const context = await browser.newContext({
      viewport: {width, height},
      deviceScaleFactor: ratio,
    });
    const page = await context.newPage();
    for (const [scheme, background] of Object.entries(SCHEMES)) {
      await page.setContent(
        `<body style="margin:0;height:100vh;display:grid;place-items:center;background:${background}">` +
          `<img src="${icon}" width="${ICON_CSS_PX}" height="${ICON_CSS_PX}"></body>`
      );
      await page.screenshot({
        path: new URL(`${scheme}-${width}x${height}@${ratio}x.png`, OUT).pathname,
      });
    }
    await context.close();
  }
} finally {
  await browser.close();
}
