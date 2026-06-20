import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN.json'
import en from './locales/en.json'

// 从 localStorage 读取用户语言偏好，默认中文
const saved = localStorage.getItem('apipulse-lang')
const defaultLocale = saved || 'zh-CN'

const i18n = createI18n({
  legacy: false,           // 使用 Composition API 模式
  locale: defaultLocale,
  fallbackLocale: 'zh-CN',
  missingWarn: false,      // 禁止缺失翻译 key 的控制台警告（如 Element Plus 内部 key）
  fallbackWarn: false,     // 禁止回退翻译的控制台警告
  messages: { 'zh-CN': zhCN, en },
})

export default i18n

/** 切换语言并持久化 */
export function setLocale(lang) {
  i18n.global.locale.value = lang
  localStorage.setItem('apipulse-lang', lang)
}
