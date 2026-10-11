// ESLint for the browser code in src/, check only: never run it with --fix. src/ is published as committed and tests
// compare parts of its exact text, so findings are fixed by hand in a reviewed change. The files are classic scripts
// (not ES modules), loaded by archive.html and index.html.
import css from "@eslint/css";
import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["src/vendor/**"] },
  {
    files: ["src/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: { ...globals.browser, DEEMO_I18N: "readonly" },
    },
    rules: js.configs.recommended.rules,
  },
  {
    files: ["src/**/*.css"],
    language: "css/css",
    plugins: { css },
    // Defects only: browser-support and !important style rules are not defects here.
    rules: { ...css.configs.recommended.rules, "css/no-important": "off", "css/use-baseline": "off" },
  },
  {
    // The forced-colors rule sets `-webkit-appearance: auto`, which Safari supports but the CSSTree data behind
    // css/no-invalid-properties does not list.
    files: ["src/archive.css"],
    rules: { "css/no-invalid-properties": "off" },
  },
];
