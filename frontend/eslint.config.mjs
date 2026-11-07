import {createEslintConfig} from '@lifekit-hq/config/eslint';

// A lint rule sees one file, not the route table, so the preset finds routed pages by their name
// (`*.page.ts`, a `pages/` folder). This app names them `*-page.component.ts`; the second glob is
// their inline template, which the template rules see as a virtual `<file>.ts/<n>.html` file.
const PAGES = ['**/*-page.component.ts', '**/*-page.component.ts/*.html'];

export default createEslintConfig({
  selectorPrefix: 'dc',
  extraConfigs: [
    {
      files: PAGES,
      ignores: ['**/*.spec.ts', '**/*.spec.ts/*.html'],
      // more-page.component.ts hands the screen to the kit's <cmn-more-page>, which is the page.
      rules: {'lifekit/page-root': 'error'},
    },
  ],
});
