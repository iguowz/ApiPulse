import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'fs';

const BASE = 'http://localhost:3000';
const SCREENSHOT_DIR = '/tmp/apipulse_audit/interactions';

const issues = [];

function addIssue(page, action, detail) {
  issues.push({ page, action, detail });
  console.log(`  ❌ ${action}: ${detail}`);
}

async function safeClick(page, selector, label) {
  try {
    const el = page.locator(selector);
    if (await el.isVisible({ timeout: 3000 })) {
      await el.click();
      await page.waitForTimeout(500);
      return true;
    }
    return false;
  } catch { return false; }
}

// 关闭所有打开的 dialog/drawer/overlay，防止拦截后续点击
async function dismissDialogs(page) {
  // 尝试多种关闭方式
  // 1. el-dialog 的关闭按钮
  const closeBtn = page.locator('.el-dialog__close, .el-drawer__close, .el-message-box__close').first();
  if (await closeBtn.isVisible({ timeout: 500 }).catch(() => false)) {
    await closeBtn.click().catch(() => {});
    await page.waitForTimeout(400);
  }
  // 2. 取消按钮
  const cancelBtn = page.locator('button:has-text("取消"), button:has-text("Cancel")').first();
  if (await cancelBtn.isVisible({ timeout: 500 }).catch(() => false)) {
    await cancelBtn.click().catch(() => {});
    await page.waitForTimeout(400);
  }
  // 3. Escape 键
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  // 4. 点击 overlay 遮罩层（最后手段）
  const overlay = page.locator('.el-overlay').first();
  if (await overlay.isVisible({ timeout: 300 }).catch(() => false)) {
    await overlay.click({ position: { x: 10, y: 10 } }).catch(() => {});
    await page.waitForTimeout(300);
  }
}

// 检查是否有打开的 dialog（包括 el-dialog, el-drawer, el-overlay）
async function hasOpenDialog(page) {
  const sel = '.el-dialog, .el-drawer, .el-overlay, [role="dialog"]';
  return page.locator(sel).first().isVisible({ timeout: 1000 }).catch(() => false);
}

async function main() {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
    executablePath: '/Users/guo/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  // 注入错误收集
  await page.addInitScript(() => {
    window.__auditErrors = [];
    const origErr = console.error;
    console.error = function (...args) {
      window.__auditErrors.push(args.map(a => String(a)).join(' '));
      origErr.apply(console, args);
    };
    window.addEventListener('error', (e) => {
      window.__auditErrors.push('[GLOBAL] ' + e.message);
    });
    window.addEventListener('unhandledrejection', (e) => {
      window.__auditErrors.push('[PROMISE] ' + (e.reason?.message || String(e.reason)));
    });
  });

  console.log('Loading app...');
  await page.goto(BASE + '/dashboard', { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(1000);

  // ═══════════════════════════════════════════════════════════
  // 1. Dashboard 交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 1. Dashboard Interactions ======');

  // 1a. 刷新按钮
  const refreshBtn = page.locator('button:has-text("刷新"), button:has-text("Refresh"), [class*="refresh"]');
  if (await refreshBtn.first().isVisible({ timeout: 2000 }).catch(() => false)) {
    await refreshBtn.first().click();
    await page.waitForTimeout(1000);
    console.log('  ✅ Refresh button clicked');
  } else {
    addIssue('Dashboard', 'Refresh', 'Refresh button not found');
  }

  // 1b. 语言切换
  const langBtn = page.locator('text=EN, text=中, [class*="lang"]');
  const langVisible = await langBtn.first().isVisible({ timeout: 2000 }).catch(() => false);
  if (langVisible) {
    await langBtn.first().click();
    await page.waitForTimeout(500);
    console.log('  ✅ Language toggle clicked');
  }

  // 1c. 主题切换
  const themeBtn = page.locator('button:has-text("主题"), button:has-text("Theme")');
  if (await themeBtn.first().isVisible({ timeout: 2000 }).catch(() => false)) {
    await themeBtn.first().click();
    await page.waitForTimeout(500);
    console.log('  ✅ Theme toggle clicked');
  }

  // 1d. 侧边栏导航到各页面
  const navItems = [
    { text: /API|接口/, route: '/apis' },
    { text: /场景|Scenario/, route: '/scenarios' },
    { text: /数据工厂|Factory/, route: '/factory' },
    { text: /巡检|Monitor/, route: '/monitor' },
    { text: /执行|Execution/, route: '/executions' },
    { text: /设置|Setting/, route: '/settings' },
  ];
  for (const { text, route } of navItems) {
    const link = page.locator(`a[href="${route}"], [class*="nav"]:has-text("${text.source}")`).first();
    if (await link.isVisible({ timeout: 2000 }).catch(() => false)) {
      await link.click();
      await page.waitForTimeout(800);
      const errors = await page.evaluate(() => window.__auditErrors || []);
      if (errors.length > 0) {
        addIssue(route, 'Navigation', errors.join('; '));
        await page.evaluate(() => { window.__auditErrors = []; });
      }
      console.log(`  ✅ Navigate to ${route}`);
    }
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/01_dashboard_interacted.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 2. APIs 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 2. APIs Interactions ======');
  await page.goto(BASE + '/apis', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 2a. 状态筛选下拉
  const statusSelects = page.locator('.el-select, [class*="select"]');
  const selectCount = await statusSelects.count();
  if (selectCount > 0) {
    for (let i = 0; i < Math.min(selectCount, 3); i++) {
      await statusSelects.nth(i).click().catch(() => {});
      await page.waitForTimeout(400);
      const option = page.locator('.el-select-dropdown__item').first();
      if (await option.isVisible({ timeout: 2000 }).catch(() => false)) {
        await option.click();
        await page.waitForTimeout(600);
      }
      // 关闭下拉框（点击空白处）
      await page.locator('.page-title').click().catch(() => {});
      await page.waitForTimeout(300);
    }
    console.log('  ✅ Filter selects toggled');
  }

  // 2b. 抓包按钮 — 打开后关闭
  await safeClick(page, 'button:has-text("抓包"), button:has-text("Capture")', 'Capture');
  await page.waitForTimeout(600);
  if (await hasOpenDialog(page)) {
    console.log('  ✅ Capture dialog opened');
    await dismissDialogs(page);
  } else {
    console.log('  ⚠️  Capture dialog did not open (may be expected)');
  }

  // 2c. 上传 HAR — 这是文件 input label，点击后打开系统文件选择器，不会弹出 dialog
  await safeClick(page, 'label:has-text("上传"), label:has-text("Upload")', 'Upload HAR');
  // 文件选择器不会产生 Element dialog，跳过 dialog 检测

  // 确保没有遗留的 overlay 拦截
  await dismissDialogs(page);

  // 2d. 点击 API 行进入详情
  const apiRow = page.locator('.el-table__body tr, [class*="table-row"]').first();
  if (await apiRow.isVisible({ timeout: 3000 }).catch(() => false)) {
    await apiRow.click();
    await page.waitForTimeout(800);
    const currentUrl = page.url();
    if (currentUrl.includes('/apis/')) {
      console.log('  ✅ API detail page navigated');
      await page.goBack();
      await page.waitForTimeout(500);
    }
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/02_apis_interacted.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 3. API Detail 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 3. API Detail Interactions ======');
  await page.goto(BASE + '/apis', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(500);
  const apiRow2 = page.locator('.el-table__body tr').first();
  if (await apiRow2.isVisible({ timeout: 3000 }).catch(() => false)) {
    await apiRow2.click();
    await page.waitForTimeout(1000);

    // 3a. Tab 切换（文档/断言/场景/执行历史等）
    const tabs = page.locator('.el-tabs__item, [role="tab"]');
    const tabCount = await tabs.count();
    for (let i = 0; i < Math.min(tabCount, 4); i++) {
      await tabs.nth(i).click().catch(() => {});
      await page.waitForTimeout(400);
    }
    console.log(`  ✅ ${Math.min(tabCount, 4)} tabs toggled`);

    // 3b. 运行 API 按钮 — 打开执行对话框
    await safeClick(page, 'button:has-text("执行"), button:has-text("Run"), button:has-text("发送")', 'Run API');
    await page.waitForTimeout(600);
    if (await hasOpenDialog(page)) {
      console.log('  ✅ API run dialog opened');
      await dismissDialogs(page);
    }

    // 3c. 文档编辑
    await safeClick(page, 'button:has-text("编辑"), button:has-text("Edit"), [class*="edit"]', 'Edit');
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/03_api_detail.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 4. Scenarios 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 4. Scenarios Interactions ======');
  await page.goto(BASE + '/scenarios', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 4a. AI 生成按钮
  await safeClick(page, 'button:has-text("AI"), button:has-text("生成")', 'AI Generate');
  await page.waitForTimeout(600);
  if (await hasOpenDialog(page)) {
    console.log('  ✅ AI generate dialog opened');
    await dismissDialogs(page);
  }

  // 4b. 新建场景按钮
  await safeClick(page, 'button:has-text("新建"), button:has-text("Create"), button:has-text("New"), a[href="/scenarios/new"]', 'New Scenario');

  await page.screenshot({ path: `${SCREENSHOT_DIR}/04_scenarios.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 5. Executions 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 5. Executions Interactions ======');
  await page.goto(BASE + '/executions', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 5a. 类型/触发/结果 筛选下拉
  const execSelects = page.locator('.el-select');
  const execSelectCount = await execSelects.count();
  for (let i = 0; i < Math.min(execSelectCount, 3); i++) {
    await execSelects.nth(i).click().catch(() => {});
    await page.waitForTimeout(400);
    const opt = page.locator('.el-select-dropdown__item').first();
    if (await opt.isVisible({ timeout: 2000 }).catch(() => false)) {
      await opt.click();
      await page.waitForTimeout(600);
    }
    await page.locator('.page-title').click().catch(() => {});
    await page.waitForTimeout(300);
  }
  console.log('  ✅ Execution filters toggled');

  // 5b. 清除筛选
  await safeClick(page, 'button:has-text("清除"), button:has-text("Clear")', 'Clear filters');

  // 5c. 点击执行行进入详情
  const execRow = page.locator('.el-table__body tr').first();
  if (await execRow.isVisible({ timeout: 3000 }).catch(() => false)) {
    await execRow.click();
    await page.waitForTimeout(800);
    if (page.url().includes('/executions/')) {
      console.log('  ✅ Execution detail navigated');
    }
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/05_executions.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 6. Factory 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 6. Factory Interactions ======');
  await page.goto(BASE + '/factory', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 6a. 新建模板
  await safeClick(page, 'button:has-text("新建"), button:has-text("Create"), button:has-text("模板")', 'New Template');
  await page.waitForTimeout(600);
  if (await hasOpenDialog(page)) {
    console.log('  ✅ Template form opened');
    // 尝试填写表单
    const nameInput = page.locator('input[placeholder*="名称"], input[placeholder*="Name"], .el-input__inner').first();
    if (await nameInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      await nameInput.fill('test_template_audit');
      await page.waitForTimeout(300);
    }
    await dismissDialogs(page);
  } else {
    console.log('  ⚠️  No templates exist, create button may navigate instead');
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/06_factory.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 7. Monitor 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 7. Monitor Interactions ======');
  await page.goto(BASE + '/monitor', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 7a. Tab 切换（巡检任务 / 告警历史）
  const monTabs = page.locator('.el-tabs__item, [role="tab"]');
  const monTabCount = await monTabs.count();
  for (let i = 0; i < Math.min(monTabCount, 2); i++) {
    await monTabs.nth(i).click().catch(() => {});
    await page.waitForTimeout(400);
  }
  console.log(`  ✅ ${Math.min(monTabCount, 2)} monitor tabs toggled`);

  // 7b. 新建巡检
  await safeClick(page, 'button:has-text("新建"), button:has-text("Create")', 'New Monitor');
  await page.waitForTimeout(600);
  await dismissDialogs(page);

  await page.screenshot({ path: `${SCREENSHOT_DIR}/07_monitor.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 8. Settings 页面交互
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 8. Settings Interactions ======');
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 8a. 4个Tab 切换
  const setTabs = page.locator('.el-tabs__item, [role="tab"]');
  const setTabCount = await setTabs.count();
  for (let i = 0; i < Math.min(setTabCount, 4); i++) {
    await setTabs.nth(i).click().catch(() => {});
    await page.waitForTimeout(400);
    const errs = await page.evaluate(() => window.__auditErrors || []);
    if (errs.length > 0) {
      addIssue('Settings', `Tab ${i}`, errs.join('; '));
      await page.evaluate(() => { window.__auditErrors = []; });
    }
  }
  console.log(`  ✅ ${Math.min(setTabCount, 4)} settings tabs toggled`);

  // 8b. LLM 配置 Tab — 测试连接按钮
  await setTabs.first().click().catch(() => {});
  await page.waitForTimeout(500);
  await safeClick(page, 'button:has-text("测试"), button:has-text("Test"), button:has-text("检测")', 'Test Connection');
  await page.waitForTimeout(1500);
  // 测试结果可能是 toast 或 dialog，确保清理
  await dismissDialogs(page);

  // 8c. 执行环境 Tab — 检查 JSON 编辑器
  if (setTabCount >= 3) {
    await setTabs.nth(2).click().catch(() => {});
    await page.waitForTimeout(500);
    // 尝试展开环境项
    await safeClick(page, '.el-table__body tr, [class*="env-row"]', 'Environment row');
  }

  await page.screenshot({ path: `${SCREENSHOT_DIR}/08_settings.png`, fullPage: true });

  // ═══════════════════════════════════════════════════════════
  // 9. 边界情况测试
  // ═══════════════════════════════════════════════════════════
  console.log('\n====== 9. Edge Cases ======');

  // 9a. 404 页面
  await page.goto(BASE + '/nonexistent', { waitUntil: 'networkidle', timeout: 10000 });
  await page.waitForTimeout(500);
  const notFoundText = await page.locator('body').innerText().catch(() => '');
  if (notFoundText.includes('404') || notFoundText.includes('不存在') || notFoundText.includes('Not Found')) {
    console.log('  ✅ 404 page renders correctly');
  } else {
    addIssue('404', 'Page', '404 page may not render correctly');
    console.log(`  ⚠️  404 page text: ${notFoundText.substring(0, 100)}`);
  }

  // 9b. 空项目状态（如果没有项目选择器可以切换）
  await page.goto(BASE + '/apis', { waitUntil: 'networkidle', timeout: 10000 });
  await page.waitForTimeout(500);
  const emptyText = await page.locator('.el-empty, [class*="empty"]').count();
  console.log(`  ℹ️  Empty state components on page: ${emptyText}`);

  // ═══════════════════════════════════════════════════════════
  // 汇总
  // ═══════════════════════════════════════════════════════════
  console.log('\n\n====== INTERACTION AUDIT SUMMARY ======');
  if (issues.length === 0) {
    console.log('✅ All interactions passed! No issues found.');
  } else {
    console.log(`❌ Found ${issues.length} issue(s):\n`);
    for (const issue of issues) {
      console.log(`  [${issue.page}] ${issue.action}: ${issue.detail}`);
    }
  }

  writeFileSync('/tmp/apipulse_audit/interaction_issues.json', JSON.stringify(issues, null, 2));
  console.log('\nIssues saved to /tmp/apipulse_audit/interaction_issues.json');

  await browser.close();
  return issues;
}

main().then((issues) => {
  process.exit(issues.length > 0 ? 1 : 0);
}).catch((e) => {
  console.error('Fatal:', e.message);
  process.exit(2);
});
