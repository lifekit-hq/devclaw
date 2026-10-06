/** @type {import('tailwindcss').Config} */
module.exports = {
  presets: [require('@lifekit-hq/tokens/tailwind')],
  content: ['./src/**/*.{ts,html}', './node_modules/@lifekit-hq/ui/fesm2022/*.mjs'],
};
