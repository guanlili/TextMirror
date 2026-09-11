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
      // 与项目风格保持一致：允许显式 any（存量代码较多，逐步收紧）
      "@typescript-eslint/no-explicit-any": "off",
      // 允许 console.warn/error（保留运行时告警），禁止 console.log
      "no-console": ["warn", { allow: ["warn", "error"] }],
      // 允许空 catch（接口容错常见写法）
      "no-empty": ["error", { allowEmptyCatch: true }],
      // Vue 单文件组件命名风格放宽
      "vue/multi-word-component-names": "off",
      // 模板/格式类规则关闭：PR-C 先保证 toolchain 可用，后续再逐步统一风格
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/multiline-html-element-content-newline": "off",
      "vue/html-indent": "off",
      "vue/html-self-closing": "off",
      "vue/html-closing-bracket-spacing": "off",
      "vue/html-closing-bracket-newline": "off",
      "vue/first-attribute-linebreak": "off",
      "vue/attributes-order": "off",
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
