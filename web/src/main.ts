import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/base.css'

// 文件拖到拖放区以外（顶栏、侧栏、对话页）松手：浏览器默认会直接打开那个文件，
// 整个应用就被换走了。拖放区自己先拦下（它调过 preventDefault，这里不动），其余地方一律不收
for (const type of ['dragover', 'drop'] as const) {
  window.addEventListener(type, (event) => {
    if (event.defaultPrevented || !event.dataTransfer?.types.includes('Files')) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'none'
  })
}

createApp(App).use(createPinia()).use(router).mount('#app')
