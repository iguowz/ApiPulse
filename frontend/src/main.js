import { createApp, watch } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import './main.css'

const app = createApp(App)

// 全局错误边界 —— 捕获所有未处理的应用内异常，避免一个组件崩溃导致整个界面白屏
app.config.errorHandler = (err, instance, info) => {
  console.error('[GlobalError]', err, { component: instance?.$?.type?.name, info })
}

// 抑制 vue-i18n v10 在根组件产生的 "Not found parent scope" 无害警告
// $t() 回退到 global scope 属预期行为；vue-i18n 未提供公开配置项关闭此警告
if (import.meta.env.DEV) {
  const { warn: orig } = console
  console.warn = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('Not found parent scope')) return
    orig.apply(console, args)
  }
}

app.use(createPinia())
app.use(router)
app.use(i18n)

// Element Plus 国际化跟随应用语言切换
const elLocales = { 'zh-CN': zhCn, en }
const savedLang = localStorage.getItem('apipulse-lang') || 'zh-CN'
app.use(ElementPlus, { locale: elLocales[savedLang] || zhCn })

// 监听 i18n 语言变更，同步更新 Element Plus 组件语言
// 使用顶层 ESM import 的 watch，避免动态 require 在 type:module 模式下报错
watch(() => i18n.global.locale.value, (lang) => {
  ElementPlus.locale(elLocales[lang] || zhCn)
})

app.mount('#app')
