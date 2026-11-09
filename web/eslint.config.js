import js from '@eslint/js'
import vueTsEslintConfig from '@vue/eslint-config-typescript'
// 排版归 Prettier，ESLint 只管正确性。不这么切，eslint-plugin-vue 的
// max-attributes-per-line 之类会和 Prettier 的换行逐行对打，永远调不完。
// 与后端同一个思路：ruff format 管排版，lint 侧关掉 E501。
import skipFormatting from 'eslint-config-prettier'
import pluginVue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  ...vueTsEslintConfig(),
  skipFormatting,
  {
    rules: {
      // 关掉纯排版类规则，交给 Prettier
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/attributes-order': 'off',
      // 禁 any。必要处用 unknown 再收窄（frontend.md「代码格式化」）
      '@typescript-eslint/no-explicit-any': 'error'
    }
  }
]
