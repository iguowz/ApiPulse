<template>
  <div class="page docs-page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('docs.title') }}</div>
        <div class="page-subtitle">{{ $t('docs.subtitle') }}</div>
      </div>
    </div>

    <div class="docs-layout">
      <!-- 左侧目录导航：固定侧边栏，锚点跳转 -->
      <!-- 外层 wrapper 撑满高度，内层 sticky 才能跟随整个内容区滚动 -->
      <div class="docs-toc-wrapper">
      <aside class="docs-toc">
        <div class="toc-title">{{ $t('docs.toc') }}</div>
        <ul class="toc-list">
          <li v-for="(item, i) in tocItems" :key="i"
            :class="{ 'toc-active': activeSection === item.anchor, 'toc-h2': item.level === 2 }"
            @click="scrollTo(item.anchor)">
            {{ item.num }} {{ item.label }}
          </li>
        </ul>
      </aside>
      </div>

      <!-- 右侧内容区：折叠面板 -->
      <div class="docs-content">
        <el-card style="padding:0">
        <!-- 第1节：项目介绍 -->
        <el-collapse v-model="activeSections" class="docs-collapse">
          <el-collapse-item :title="$t('docs.sections.intro')" name="intro">
            <div class="section-body" id="anchor-intro">
              <p>{{ $t('docs.intro.p1') }}</p>
              <ul>
                <li v-for="(cap, idx) in $tm('docs.intro.capabilities')" :key="idx">
                  <strong>{{ cap.title }}</strong>：{{ cap.desc }}
                </li>
              </ul>
              <p>{{ $t('docs.intro.p2') }}</p>
            </div>
          </el-collapse-item>

          <!-- 第2节：系统架构与核心流程 -->
          <el-collapse-item :title="$t('docs.sections.architecture')" name="architecture">
            <div class="section-body">
              <p>{{ $t('docs.architecture.p1') }}</p>
              <div v-for="(d, idx) in $tm('docs.architecture.diagrams')" :key="idx">
                <h3 :id="'anchor-arch-' + d.file">{{ sectionNum('arch-' + d.file) }} {{ d.title }}</h3>
                <p>{{ d.desc }}</p>
                <figure class="diagram-figure">
                  <img :src="'/diagrams/' + d.file + '.png'" :alt="d.title" class="diagram-img" loading="lazy" />
                  <figcaption class="diagram-caption">
                    <a :href="'/diagrams/' + d.file + '.html'" target="_blank" rel="noopener">{{ $t('docs.architecture.open') }}</a>
                  </figcaption>
                </figure>
              </div>
            </div>
          </el-collapse-item>

          <!-- 第3节：功能模块说明 -->
          <el-collapse-item :title="$t('docs.sections.modules')" name="modules">
            <div class="section-body">
              <!-- 仪表盘 -->
              <h3 id="anchor-mod-dashboard">{{ sectionNum('mod-dashboard') }} {{ $t('docs.modules.dashboard.title') }}</h3>
              <p>{{ $t('docs.modules.dashboard.desc') }}</p>
              <ul>
                <li v-for="(t, idx) in $tm('docs.modules.dashboard.tabs')" :key="idx">{{ t }}</li>
              </ul>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.dashboard.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- API 接口 -->
              <h3 id="anchor-mod-apis">{{ sectionNum('mod-apis') }} {{ $t('docs.modules.apis.title') }}</h3>
              <p>{{ $t('docs.modules.apis.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.apis.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- API 详情 -->
              <h3 id="anchor-mod-apidetail">{{ sectionNum('mod-apidetail') }} {{ $t('docs.modules.apidetail.title') }}</h3>
              <p>{{ $t('docs.modules.apidetail.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.apidetail.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- Mock 服务 -->
              <h3 id="anchor-mod-mock">{{ sectionNum('mod-mock') }} {{ $t('docs.modules.mock.title') }}</h3>
              <p>{{ $t('docs.modules.mock.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.mock.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 场景 -->
              <h3 id="anchor-mod-scenarios">{{ sectionNum('mod-scenarios') }} {{ $t('docs.modules.scenarios.title') }}</h3>
              <p>{{ $t('docs.modules.scenarios.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.scenarios.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 数据工厂 -->
              <h3 id="anchor-mod-factory">{{ sectionNum('mod-factory') }} {{ $t('docs.modules.factory.title') }}</h3>
              <p>{{ $t('docs.modules.factory.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.factory.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 执行历史 -->
              <h3 id="anchor-mod-executions">{{ sectionNum('mod-executions') }} {{ $t('docs.modules.executions.title') }}</h3>
              <p>{{ $t('docs.modules.executions.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.executions.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 巡检监控 -->
              <h3 id="anchor-mod-monitor">{{ sectionNum('mod-monitor') }} {{ $t('docs.modules.monitor.title') }}</h3>
              <p>{{ $t('docs.modules.monitor.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.monitor.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 审核中心 -->
              <h3 id="anchor-mod-generations">{{ sectionNum('mod-generations') }} {{ $t('docs.modules.generations.title') }}</h3>
              <p>{{ $t('docs.modules.generations.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.generations.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 导入差异 -->
              <h3 id="anchor-mod-importdiffs">{{ sectionNum('mod-importdiffs') }} {{ $t('docs.modules.importdiffs.title') }}</h3>
              <p>{{ $t('docs.modules.importdiffs.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.importdiffs.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 知识库 -->
              <h3 id="anchor-mod-knowledge">{{ sectionNum('mod-knowledge') }} {{ $t('docs.modules.knowledge.title') }}</h3>
              <p>{{ $t('docs.modules.knowledge.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.knowledge.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 覆盖度 -->
              <h3 id="anchor-mod-coverage">{{ sectionNum('mod-coverage') }} {{ $t('docs.modules.coverage.title') }}</h3>
              <p>{{ $t('docs.modules.coverage.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.coverage.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 设置 -->
              <h3 id="anchor-mod-settings">{{ sectionNum('mod-settings') }} {{ $t('docs.modules.settings.title') }}</h3>
              <p>{{ $t('docs.modules.settings.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.settings.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 机器人接入 -->
              <h3 id="anchor-mod-bot_config">{{ sectionNum('mod-bot_config') }} {{ $t('docs.modules.bot_config.title') }}</h3>
              <p>{{ $t('docs.modules.bot_config.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.bot_config.features')" :key="idx">{{ f }}</li>
              </ul>

              <!-- 用户管理 -->
              <h3 id="anchor-mod-users">{{ sectionNum('mod-users') }} {{ $t('docs.modules.users.title') }}</h3>
              <p>{{ $t('docs.modules.users.desc') }}</p>
              <ul>
                <li v-for="(f, idx) in $tm('docs.modules.users.features')" :key="idx">{{ f }}</li>
              </ul>
            </div>
          </el-collapse-item>

          <!-- 第3节：常用场景与操作流程 -->
          <el-collapse-item :title="$t('docs.sections.scenarios')" name="scenarios">
            <div class="section-body">
              <div v-for="(s, idx) in $tm('docs.scenarios.list')" :key="idx">
                <h3 :id="'anchor-scenario-' + (idx + 1)">{{ sectionNum('scenario-' + (idx + 1)) }} {{ s.title }}</h3>
                <p><strong>{{ $t('docs.scenarios.background') }}</strong>{{ s.background }}</p>
                <p><strong>{{ $t('docs.scenarios.flow') }}</strong>{{ s.flow }}</p>
                <ol v-if="s.steps && s.steps.length" class="steps-list">
                  <li v-for="(step, stepIdx) in s.steps" :key="stepIdx">{{ step }}</li>
                </ol>
                <ul v-if="s.tips">
                  <li v-for="(tip, tIdx) in s.tips" :key="tIdx">{{ tip }}</li>
                </ul>
              </div>
            </div>
          </el-collapse-item>

          <!-- 第4节：其他说明 -->
          <el-collapse-item :title="$t('docs.sections.notes')" name="notes">
            <div class="section-body">
              <h3 id="anchor-note-theme">{{ sectionNum('note-theme') }} {{ $t('docs.notes.theme.title') }}</h3>
              <p>{{ $t('docs.notes.theme.desc') }}</p>

              <h3 id="anchor-note-lang">{{ sectionNum('note-lang') }} {{ $t('docs.notes.lang.title') }}</h3>
              <p>{{ $t('docs.notes.lang.desc') }}</p>

              <h3 id="anchor-note-ai">{{ sectionNum('note-ai') }} {{ $t('docs.notes.ai.title') }}</h3>
              <p>{{ $t('docs.notes.ai.desc') }}</p>

              <h3 id="anchor-note-project">{{ sectionNum('note-project') }} {{ $t('docs.notes.project.title') }}</h3>
              <p>{{ $t('docs.notes.project.desc') }}</p>

              <h3 id="anchor-note-roles">{{ sectionNum('note-roles') }} {{ $t('docs.notes.roles.title') }}</h3>
              <p>{{ $t('docs.notes.roles.desc') }}</p>
            </div>
          </el-collapse-item>

          <!-- 第5节：使用示例 -->
          <el-collapse-item :title="$t('docs.sections.examples')" name="examples">
            <div class="section-body">
              <div v-for="(item, idx) in $tm('docs.examples.items')" :key="idx">
                <h3 :id="'anchor-example-' + (idx + 1)">{{ sectionNum('example-' + (idx + 1)) }} {{ item.title }}</h3>
                <p>{{ item.desc }}</p>
                <div class="code-samples">
                  <div v-for="(s, sIdx) in item.samples" :key="sIdx" class="code-sample-item">
                    <span class="code-label">{{ s.label }}</span>
                    <code class="code-block">{{ s.code }}</code>
                  </div>
                </div>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'

const { t, tm } = useI18n()

// 默认展开所有面板
const activeSections = ref(['intro', 'architecture', 'modules', 'scenarios', 'notes', 'examples'])

// 当前激活的锚点节（用于高亮 TOC）
const activeSection = ref('intro')

// TOC 条目：从 i18n 生成，按节/小节编号
// i18n 标签可能自带 "1. " 前缀（用于折叠面板标题），TOC 中需要去掉避免重复
function stripNum(s) { return s.replace(/^\d+\.\s*/, '') }

const moduleKeys = [
  'dashboard', 'apis', 'apidetail', 'mock', 'scenarios', 'factory', 'executions', 'monitor',
  'generations', 'importdiffs', 'knowledge', 'coverage', 'settings', 'bot_config', 'users',
]
const noteKeys = ['theme', 'lang', 'ai', 'project', 'roles']

function localizedArray(path) {
  const value = tm(path)
  return Array.isArray(value) ? value : []
}

const tocItems = computed(() => {
  const scenarioItems = localizedArray('docs.scenarios.list').map((item, idx) => ({
    anchor: `scenario-${idx + 1}`,
    label: item.title || t(`docs.scenarios.list.${idx}.title`),
    level: 2,
  }))
  const exampleItems = localizedArray('docs.examples.items').map((item, idx) => ({
    anchor: `example-${idx + 1}`,
    label: item.title || t(`docs.examples.items.${idx}.title`),
    level: 2,
  }))
  const sections = [
    { key: 'intro', items: [
      { anchor: 'intro', label: stripNum(t('docs.sections.intro')), level: 1 },
    ]},
    { key: 'architecture', items: [
      { anchor: 'architecture', label: stripNum(t('docs.sections.architecture')), level: 1 },
      ...localizedArray('docs.architecture.diagrams').map(d => ({ anchor: 'arch-' + d.file, label: d.title, level: 2 })),
    ]},
    { key: 'modules', items: [
      // 节标题（level 1）：展开面板、滚动到该节第一个子项
      { anchor: 'modules', label: stripNum(t('docs.sections.modules')), level: 1 },
      ...moduleKeys.map(key => ({ anchor: `mod-${key}`, label: t(`docs.modules.${key}.title`), level: 2 })),
    ]},
    { key: 'scenarios', items: [
      { anchor: 'scenarios', label: stripNum(t('docs.sections.scenarios')), level: 1 },
      ...scenarioItems,
    ]},
    { key: 'notes', items: [
      { anchor: 'notes', label: stripNum(t('docs.sections.notes')), level: 1 },
      ...noteKeys.map(key => ({ anchor: `note-${key}`, label: t(`docs.notes.${key}.title`), level: 2 })),
    ]},
    { key: 'examples', items: [
      { anchor: 'examples', label: stripNum(t('docs.sections.examples')), level: 1 },
      ...exampleItems,
    ]},
  ]
  const result = []
  let secIdx = 0
  for (const sec of sections) {
    secIdx++
    let itemIdx = 0
    for (const item of sec.items) {
      // level 1（节标题）不消耗子项序号，子项从 .1 开始编号
      if (item.level === 1) {
        result.push({ ...item, num: String(secIdx) })
      } else {
        itemIdx++
        result.push({ ...item, num: `${secIdx}.${itemIdx}` })
      }
    }
  }
  return result
})

// 根据锚点名查编号，用于右侧标题前显示节号（与 TOC 一致）
function sectionNum(anchor) {
  const item = tocItems.value.find(i => i.anchor === anchor)
  return item ? item.num : ''
}

// 锚点滚动：平滑滚动到目标元素
function scrollTo(anchor) {
  // 先确保对应的 collapse 面板展开，并确定实际滚动目标
  let scrollAnchor = anchor

  if (anchor === 'intro') {
    if (!activeSections.value.includes('intro')) activeSections.value.push('intro')
  } else if (anchor === 'architecture') {
    if (!activeSections.value.includes('architecture')) activeSections.value.push('architecture')
    scrollAnchor = 'arch-architecture'
  } else if (anchor.startsWith('arch-')) {
    if (!activeSections.value.includes('architecture')) activeSections.value.push('architecture')
  } else if (anchor.startsWith('mod-')) {
    if (!activeSections.value.includes('modules')) activeSections.value.push('modules')
  } else if (anchor.startsWith('scenario-')) {
    if (!activeSections.value.includes('scenarios')) activeSections.value.push('scenarios')
  } else if (anchor.startsWith('note-')) {
    if (!activeSections.value.includes('notes')) activeSections.value.push('notes')
  } else if (anchor === 'modules') {
    // 节标题：展开面板，滚动到第一个子项
    if (!activeSections.value.includes('modules')) activeSections.value.push('modules')
    scrollAnchor = 'mod-dashboard'
  } else if (anchor === 'scenarios') {
    if (!activeSections.value.includes('scenarios')) activeSections.value.push('scenarios')
    scrollAnchor = 'scenario-1'
  } else if (anchor === 'notes') {
    if (!activeSections.value.includes('notes')) activeSections.value.push('notes')
    scrollAnchor = 'note-theme'
  } else if (anchor.startsWith('example-')) {
    if (!activeSections.value.includes('examples')) activeSections.value.push('examples')
  } else if (anchor === 'examples') {
    if (!activeSections.value.includes('examples')) activeSections.value.push('examples')
    scrollAnchor = 'example-1'
  }

  // 等待 DOM 更新后滚动
  setTimeout(() => {
    const el = document.getElementById('anchor-' + scrollAnchor)
    if (el) {
      activeSection.value = anchor
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, 100)
}

// 监听滚动，高亮当前可见的锚点
// 节标题（如 modules/scenarios/notes）在 DOM 中没有对应元素，用其第一个子项位置代替
function onScroll() {
  const sectionMap = { architecture: 'arch-architecture', modules: 'mod-dashboard', scenarios: 'scenario-1', notes: 'note-theme', examples: 'example-1' }
  const anchors = tocItems.value.map(i => 'anchor-' + i.anchor)
  let current = anchors[0]
  for (const id of anchors) {
    // 节标题锚点：用第一个子项的位置判断
    const anchorKey = id.replace('anchor-', '')
    const lookupId = sectionMap[anchorKey] ? 'anchor-' + sectionMap[anchorKey] : id
    const el = document.getElementById(lookupId)
    if (el) {
      const rect = el.getBoundingClientRect()
      if (rect.top <= 120) {
        current = id
      }
    }
  }
  activeSection.value = current.replace('anchor-', '')
}

// 节流滚动监听
let scrollTimer = null
function throttledScroll() {
  if (scrollTimer) return
  scrollTimer = setTimeout(() => {
    onScroll()
    scrollTimer = null
  }, 100)
}

onMounted(() => {
  document.querySelector('.main-area')?.addEventListener('scroll', throttledScroll, { passive: true })
  window.addEventListener('scroll', throttledScroll, { passive: true })
})

onUnmounted(() => {
  document.querySelector('.main-area')?.removeEventListener('scroll', throttledScroll)
  window.removeEventListener('scroll', throttledScroll)
})
</script>

<style scoped>
/* 覆盖全局 .page 的 overflow:hidden，否则 sticky TOC 失效 */
.docs-page {
  max-width: 1100px;
  margin: 0 auto;
  overflow: visible;
}

.docs-layout {
  display: flex;
  gap: 32px;
  margin-top: 20px;
  /* 不设 align-items，默认 stretch 让容器撑满内容高度，TOC 的 sticky 才能覆盖全滚动范围 */
}

/* 左侧目录导航 wrapper：撑满容器高度，为 sticky 提供完整滚动上下文 */
.docs-toc-wrapper {
  width: 220px;
  flex-shrink: 0;
}

/* 左侧目录导航：固定在可视区域 */
.docs-toc {
  position: sticky;
  top: 20px;
  background: var(--bg-2);
  border: 1px solid var(--border-2);
  border-radius: var(--radius);
  padding: 16px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}

.toc-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-2);
}

.toc-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.toc-list li {
  font-size: 12px;
  color: var(--text-2);
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all .15s ease;
  line-height: 1.5;
}

.toc-list li.toc-h2 {
  padding-left: 18px;
  font-size: 11px;
  color: var(--text-3);
}

.toc-list li:hover {
  background: var(--bg-hover);
  color: var(--text-1);
}

.toc-list li.toc-active {
  background: rgba(79,142,247,.12);
  color: var(--accent);
  font-weight: 500;
}

/* 右侧内容区 */
.docs-content {
  flex: 1;
  min-width: 0;
  padding-right: 24px;  /* 右侧留白，避免文字贴边 */
}

.docs-collapse {
  --el-collapse-header-font-size: 15px;
  --el-collapse-content-font-size: 14px;
}

.docs-collapse :deep(.el-collapse-item__header) {
  font-weight: 600;
  padding: 12px 0;
  border-bottom: 0px solid var(--border-2);
}

.docs-collapse :deep(.el-collapse-item__content) {
  padding: 16px 12px;  /* 左右增加内边距，避免文字贴边 */
}

.section-body {
  line-height: 1.75;
  color: var(--text-1);
}

.section-body h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
  margin: 24px 0 8px;
  padding-top: 8px;
  scroll-margin-top: 80px;
}

.section-body h3:first-child {
  margin-top: 0;
}

.section-body p {
  margin: 8px 0;
}

.section-body ul {
  padding-left: 20px;
  margin: 8px 0;
}

.section-body ol {
  padding-left: 22px;
  margin: 8px 0;
}

.section-body li {
  margin: 4px 0;
  color: var(--text-2);
}

.steps-list {
  background: var(--bg-2);
  border: 1px solid var(--border-2);
  border-radius: var(--radius-sm);
  padding: 10px 14px 10px 28px;
}

.section-body strong {
  color: var(--text-1);
}

/* 代码示例块 */
.code-samples {
  margin: 12px 0;
}

.code-sample-item {
  display: flex;
  flex-direction: column;
  margin: 8px 0;
  padding: 10px 14px;
  background: var(--bg-2);
  border: 1px solid var(--border-2);
  border-radius: var(--radius-sm);
}

.code-label {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 4px;
}

.code-block {
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 12px;
  color: var(--accent);
  background: var(--bg-1);
  padding: 6px 10px;
  border-radius: 4px;
  word-break: break-all;
  line-height: 1.6;
}

/* 图示 */
.diagram-figure {
  margin: 16px 0;
}
.diagram-figure img.diagram-img {
  width: 100%;
  max-width: 100%;
  border: 1px solid var(--border-2);
  border-radius: var(--radius);
  background: #fff;
}
.diagram-caption {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-3);
  text-align: center;
}
.diagram-caption a {
  color: var(--accent);
  text-decoration: none;
}
.diagram-caption a:hover {
  text-decoration: underline;
}
</style>
