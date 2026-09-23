import js from "@eslint/js";
import tseslint from "typescript-eslint";
import pluginVue from "eslint-plugin-vue";

export default [
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs["flat/recommended"],
  {
    files: ["**/*.{ts,vue}"],
    languageOptions: {
      globals: {
        window: "readonly",
        document: "readonly",
        HTMLElement: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        Blob: "readonly",
        URL: "readonly",
        FileReader: "readonly",
        performance: "readonly",
        console: "readonly",
        navigator: "readonly",
        ClipboardItem: "readonly",
        File: "readonly",
        AbortController: "readonly",
        crypto: "readonly",
      },
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: [".vue"],
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      // 允许 console.warn/error（保留运行时告警），禁止 console.log
      "no-console": ["warn", { allow: ["warn", "error"] }],
      // 允许空 catch（接口容错常见写法）
      "no-empty": ["error", { allowEmptyCatch: true }],
      // 页面级组件使用单词命名（index.vue、403.vue 等）
      "vue/multi-word-component-names": "off",
      // 项目使用 DOMPurify 消毒后渲染 HTML
      "vue/no-v-html": "off",
      // 未使用变量按 TypeScript 规则处理
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
    },
  },
  {
    ignores: ["dist/", "node_modules/", "**/*.d.ts"],
  },
];
