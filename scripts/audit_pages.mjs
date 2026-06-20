import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const BASE = 'http://localhost:3000';
const SCREENSHOT_DIR = '/tmp/apipulse_audit';

const pagesToAudit = [
  { path: '/dashboard', name: 'Dashboard' },
  { path: '/apis', name: 'APIs List' },
  { path: '/scenarios', name: 'Scenarios List' },
  { path: '/executions', name: 'Executions List' },
  { path: '/factory', name: 'Factory' },
  { path: '/monitor', name: 'Monitor' },
  { path: '/settings', name: 'Settings' },
];

// 收集所有问题
const issues = [];

// 检查详情页（需要从列表页获取ID）
async function auditDetailPages(browser, page) {
  // 获取 API 详情
  try {
    const apiResp = await page.evaluate(() => fetch('/api/apis?project_id=default&skip=0&limit=5').then(r => r.json()));
    const apis = apiResp?.apis || apiResp?.items || apiResp?.data || apiResp || [];
    const apiList = Array.isArray(apis) ? apis : [];
    if (apiList.length > 0) {
      const apiId = apiList[0]._id || apiList[0].id;
      console.log(`  → Testing API Detail: /apis/${apiId}`);
      await page.goto(`${BASE}/apis/${apiId}`, { waitUntil: 'networkidle', timeout: 15000 });
      await page.waitForTimeout(800);
      const apiErrors = await page.evaluate(() => {
        return window.__auditErrors || [];
      });
      if (apiErrors.length > 0) {
        issues.push({ page: `API Detail /apis/${apiId}`, errors: apiErrors });
      }
      const apiHtml = await page.locator('body').innerHTML();
      if (apiHtml.includes('Error') || apiHtml.includes('error') || apiHtml.includes('错误')) {
        // 可能是正常的错误状态，但记录一下
        issues.push({ page: `API Detail /apis/${apiId}`, errors: ['Page contains "Error" or "错误" text'] });
      }
    }
  } catch (e) {
    issues.push({ page: 'API Detail', errors: [e.message] });
  }

  // 获取场景详情
  try {
    const scenResp = await page.evaluate(() => fetch('/api/scenarios?project_id=default&skip=0&limit=5').then(r => r.json()));
    const scenarios = scenResp?.scenarios || scenResp?.items || scenResp?.data || scenResp || [];
    const scenList = Array.isArray(scenarios) ? scenarios : [];
    if (scenList.length > 0) {
      const scenId = scenList[0]._id || scenList[0].id;
      console.log(`  → Testing Scenario Detail: /scenarios/${scenId}`);
      await page.goto(`${BASE}/scenarios/${scenId}`, { waitUntil: 'networkidle', timeout: 15000 });
      await page.waitForTimeout(800);
      const scenErrors = await page.evaluate(() => window.__auditErrors || []);
      if (scenErrors.length > 0) {
        issues.push({ page: `Scenario Detail /scenarios/${scenId}`, errors: scenErrors });
      }
    }
  } catch (e) {
    issues.push({ page: 'Scenario Detail', errors: [e.message] });
  }

  // 获取执行详情
  try {
    const execResp = await page.evaluate(() => fetch('/api/executions?project_id=default&skip=0&limit=5').then(r => r.json()));
    const executions = execResp?.executions || execResp?.items || execResp?.data || execResp || [];
    const execList = Array.isArray(executions) ? executions : [];
    if (execList.length > 0) {
      const execId = execList[0]._id || execList[0].id;
      console.log(`  → Testing Execution Detail: /executions/${execId}`);
      await page.goto(`${BASE}/executions/${execId}`, { waitUntil: 'networkidle', timeout: 15000 });
      await page.waitForTimeout(800);
      const execErrors = await page.evaluate(() => window.__auditErrors || []);
      if (execErrors.length > 0) {
        issues.push({ page: `Execution Detail /executions/${execId}`, errors: execErrors });
      }
    }
  } catch (e) {
    issues.push({ page: 'Execution Detail', errors: [e.message] });
  }
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/Users/guo/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  // 在页面中注入错误收集器
  page.on('pageerror', (err) => {
    console.log(`  ⚠  Page Error: ${err.message}`);
    if (!page.__errors) page.__errors = [];
    page.__errors.push(err.message);
  });

  // 收集 console 错误和警告
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      console.log(`  🔴 Console ${msg.type()}: ${msg.text()}`);
      if (!page.__consoleIssues) page.__consoleIssues = [];
      page.__consoleIssues.push(`[${msg.type()}] ${msg.text()}`);
    }
  });

  // 注入前端错误收集脚本（在每个页面加载前执行）
  await page.addInitScript(() => {
    window.__auditErrors = [];
    const origError = console.error;
    console.error = function (...args) {
      window.__auditErrors.push(args.map(a => String(a)).join(' '));
      origError.apply(console, args);
    };
    const origWarn = console.warn;
    console.warn = function (...args) {
      window.__auditErrors.push('[WARN] ' + args.map(a => String(a)).join(' '));
      origWarn.apply(console, args);
    };
    window.addEventListener('error', (e) => {
      window.__auditErrors.push('[GLOBAL] ' + e.message);
    });
    window.addEventListener('unhandledrejection', (e) => {
      window.__auditErrors.push('[PROMISE] ' + (e.reason?.message || e.reason || 'Unknown'));
    });
  });

  // 先访问首页触发 Vue 应用加载
  console.log('Loading app...');
  await page.goto(BASE + '/dashboard', { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(1000);

  for (const { path, name } of pagesToAudit) {
    console.log(`\n====== ${name} (${path}) ======`);
    try {
      // 清空已收集的错误（每次页面刷新后重新注入）
      await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 20000 });
      await page.waitForTimeout(1000);

      // 截图
      const filename = `${SCREENSHOT_DIR}/${name.replace(/\s+/g, '_').toLowerCase()}.png`;
      await page.screenshot({ path: filename, fullPage: true });
      console.log(`  Screenshot: ${filename}`);

      // 获取页面错误
      const pageErrors = await page.evaluate(() => window.__auditErrors || []);
      const consoleIssues = page.__consoleIssues || [];
      page.__consoleIssues = [];

      if (pageErrors.length > 0) {
        console.log(`  ERRORS: ${JSON.stringify(pageErrors.slice(0, 5))}`);
        issues.push({ page: name, path, errors: pageErrors.slice(0, 10) });
      }

      if (consoleIssues.length > 0) {
        console.log(`  CONSOLE: ${JSON.stringify(consoleIssues.slice(0, 5))}`);
        issues.push({ page: name, path, consoleIssues: consoleIssues.slice(0, 10) });
      }

      // 检查页面基本结构
      const bodyText = await page.locator('body').innerText().catch(() => '');
      if (bodyText.length < 10) {
        issues.push({ page: name, path, errors: ['Empty or near-empty page!'] });
        console.log(`  ⚠  EMPTY PAGE`);
      }

      // 检查网络请求失败
      const failedRequests = await page.evaluate(() => {
        return window.performance
          .getEntriesByType('resource')
          .filter(r => r.responseStatus >= 400 || r.responseStatus === 0)
          .map(r => ({ url: r.name, status: r.responseStatus }));
      });
      if (failedRequests.length > 0) {
        console.log(`  FAILED REQUESTS: ${JSON.stringify(failedRequests.filter(f => f.status === 0 || f.status >= 500).slice(0, 5))}`);
        issues.push({ page: name, path, failedRequests: failedRequests.filter(f => f.status === 0 || f.status >= 500) });
      }

      // 检查 loading 状态是否消失
      const loadingEls = await page.locator('.el-loading-mask, [class*="loading"]').count();
      if (loadingEls > 2) {
        console.log(`  ⚠  ${loadingEls} loading spinners still visible (may indicate stuck loading)`);
        issues.push({ page: name, path, errors: [`${loadingEls} loading spinners still visible`] });
      }

      console.log(`  ${bodyText.length > 10 ? 'OK' : 'EMPTY'} - ${bodyText.substring(0, 100).replace(/\n/g, ' ')}`);
    } catch (e) {
      console.log(`  ❌ ERROR: ${e.message}`);
      issues.push({ page: name, path, errors: [e.message] });
    }
  }

  // 审查详情页
  console.log('\n====== Detail Pages ======');
  await auditDetailPages(browser, page);

  // 输出汇总
  console.log('\n\n====== ISSUES SUMMARY ======');
  if (issues.length === 0) {
    console.log('No issues found!');
  } else {
    console.log(`Found ${issues.length} page(s) with issues:\n`);
    for (const issue of issues) {
      console.log(`--- ${issue.page} (${issue.path || ''}) ---`);
      if (issue.errors) console.log(`  Errors: ${issue.errors.join('; ')}`);
      if (issue.consoleIssues) console.log(`  Console: ${issue.consoleIssues.join('; ')}`);
      if (issue.failedRequests) console.log(`  Failed: ${JSON.stringify(issue.failedRequests)}`);
    }
  }

  // 保存结果到文件
  writeFileSync('/tmp/apipulse_audit/issues.json', JSON.stringify(issues, null, 2));
  console.log('\nIssues saved to /tmp/apipulse_audit/issues.json');

  await browser.close();
  return issues;
}

// 创建截图目录
import { mkdirSync } from 'fs';
mkdirSync(SCREENSHOT_DIR, { recursive: true });

main().then((issues) => {
  process.exit(issues.length > 0 ? 1 : 0);
}).catch((e) => {
  console.error('Fatal:', e.message);
  process.exit(2);
});
