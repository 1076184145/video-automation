# Video Automation 前端代码优化报告

> 评审日期：2026-05-17
> 评审角色：Frontend Developer
> 评审范围：`web/` 全部前端代码
> 重点维度：性能、可访问性、代码质量、用户体验、安全性

---

## 一、严重问题（影响核心体验，建议立即修复）

### 1.1 Job 详情页自动刷新导致编辑/观看中断 [job-detail.js]

**问题**：`setInterval(load, 1500)` 每 1.5 秒完全替换 `app.innerHTML`。用户正在：
- 观看 `video-preview` → 视频中断、播放进度丢失
- 编辑 `clip-editor` 中的时间/原因 → 输入全部丢失
- 展开 `download-advanced` → 折叠状态重置

**影响**：★★★★★ 生产环境不可用

**修复方案**：
```js
// 方案 A：智能 diff 更新（推荐）
// 只更新变化的部分（状态 badge、进度条、时间戳），保留 DOM 结构

// 方案 B：用户交互时暂停自动刷新
let userInteracted = false;
app.addEventListener('input', () => userInteracted = true);
app.addEventListener('play', () => userInteracted = true, true); // video play

async function load(forceRender = false) {
  if (userInteracted && !forceRender) {
    updateLiveStatusOnly(await API.getJob(name)); // 轻量更新
    return;
  }
  // ... 完整渲染
}

// 方案 C：visibilitychange 控制刷新
function shouldAutoRefresh() {
  return document.visibilityState === 'visible' && !userInteracted;
}
```

### 1.2 Dashboard 搜索无防抖 [dashboard.js]

**问题**：`input` 事件每次按键都触发 `updateJobs()` → 遍历全部 jobs → 拼接 HTML → 替换 DOM。

```js
// 当前代码
input.addEventListener("input", () => {
  search = input.value;
  update(); // 立即执行，无防抖
});
```

**修复**：
```js
let searchTimer;
input.addEventListener("input", () => {
  search = input.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(update, 200); // 200ms 防抖
});
```

### 1.3 后台标签页仍在浪费资源 [dashboard.js / job-detail.js]

**问题**：`setInterval` 在页面不可见时继续执行，每秒浪费 CPU/网络/电池。

**修复**：
```js
// dashboard.js
let timer = null;
function startPolling() {
  if (timer) return;
  timer = setInterval(load, 3000);
}
function stopPolling() {
  if (timer) { clearInterval(timer); timer = null; }
}
document.addEventListener('visibilitychange', () => {
  document.visibilityState === 'visible' ? startPolling() : stopPolling();
});
```

---

## 二、性能问题（影响流畅度，建议本周修复）

### 2.1 timeline.js 渲染性能瓶颈

| 问题 | 位置 | 影响 |
|------|------|------|
| `getComputedStyle` 每次渲染调用 | `drawSegments` / `drawScenes` | 强制重排（Reflow），阻塞主线程 |
| `marks.find()` 线性查找 | `canvas.onmousemove` | O(n) 复杂度，marks 超过 500 时卡顿 |
| `devicePixelRatio` 未处理变化 | `renderTimeline` | 用户缩放浏览器后画面模糊 |

**修复**：
```js
// 缓存 CSS 变量
const cssCache = new Map();
function getCss(name) {
  if (!cssCache.has(name)) {
    cssCache.set(name, getComputedStyle(document.documentElement).getPropertyValue(name).trim());
  }
  return cssCache.get(name);
}
// 监听属性变化刷新缓存
window.addEventListener('languagechange', () => cssCache.clear());

// marks 使用空间索引或排序 + 二分查找
// 简单方案：按 x 排序后用二分
marks.sort((a, b) => a.x - b.x);
function findMark(x, y) {
  // 二分查找附近候选，再精确匹配
}

// 监听 DPR 变化
window.matchMedia(`(resolution: ${devicePixelRatio}dppx)`)
  .addEventListener('change', () => renderTimeline(canvas, data));
```

### 2.2 CSS backdrop-filter 导致滚动卡顿

**问题**：`.card, .panel` 使用 `backdrop-filter: blur(12px)`，在滚动时强制 GPU 合成层，低端设备卡顿。

**修复**：
```css
@media (prefers-reduced-motion: no-preference) and (hover: hover) {
  .card, .panel { backdrop-filter: blur(12px); }
}
/* 移动端/触控设备禁用 */
@media (hover: none) {
  .card, .panel { backdrop-filter: none; }
}
```

### 2.3 Dashboard jobs 过滤在渲染线程执行

**问题**：jobs 数量大时（>100），过滤+HTML 拼接阻塞主线程。

**修复**：虚拟列表或分页。当前 2 列网格，超过 20 个卡片时考虑分页或无限滚动。

---

## 三、可访问性问题（合规与包容性）

### 3.1 已修复（UI 设计师阶段已完成）

- ✅ `focus-visible` outline 已添加
- ✅ 进度条 ARIA 属性已添加
- ✅ 自定义复选框样式已添加
- ✅ 时间线 canvas `role="img"` 已添加

### 3.2 仍需修复

| 问题 | 位置 | 修复 |
|------|------|------|
| 页面标题不随路由变化 | `index.html` | 路由切换时 `document.title = t('page.title')` |
| 无 `prefers-reduced-motion` | CSS 动画 | 为 `pulse`、`shimmer` 添加媒体查询 |
| 表单错误未关联 `aria-describedby` | `new-job.js` | 错误 div 添加 `id`，input 添加 `aria-describedby` |
| 删除确认仅依赖 `window.confirm` | `job-detail.js` | 提供无 JS 的降级方案或更好的模态框 |
| 语言切换无通知 | `i18n.js` | 切换后通过 `aria-live` 区域通知屏幕阅读器 |

```css
/* prefers-reduced-motion */
@media (prefers-reduced-motion: reduce) {
  .button, .job-card, .nav-link, .lang-button {
    transition: none !important;
  }
  .skeleton { animation: none; background: var(--bg-hover); }
  .stage.current .stage-dot { animation: none; }
}
```

---

## 四、代码质量问题

### 4.1 api.js 缺乏健壮的错误处理

```js
// 当前：如果服务器返回 502 HTML 页面，JSON 解析失败，错误消息是 "502 Bad Gateway"
// 但用户看到的是 "Connection failed"，没有重试机制

// 优化：增加超时、重试、更友好的错误
async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 10000);
  let retries = options.retries ?? 1;

  while (retries >= 0) {
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(timeout);
      if (!response.ok) {
        const text = await response.text();
        let message = `${response.status} ${response.statusText}`;
        try { const payload = JSON.parse(text); if (payload.error) message = payload.error; } catch {}
        throw new Error(message);
      }
      return response.json();
    } catch (error) {
      if (retries-- <= 0) throw error;
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}
```

### 4.2 job-detail.js 的副作用函数

```js
// deriveLiveProgress 直接修改传入对象 — 副作用
job.stage_progress = Math.min(95, ...); // ❌

// 应返回新值，让调用方决定
function deriveLiveProgress(job) {
  if (...) return null;
  return {
    stage_progress: Math.min(95, ...),
    stage_message: `Whisper transcribing...`
  };
}
```

### 4.3 router.js 缺少错误边界

```js
// 当前：如果 route.render 抛出异常，页面卡在空白
export async function renderRoute() {
  // ...
  for (const route of routes) {
    const match = path.match(route.pattern);
    if (!match) continue;
    try {
      cleanup = await route.render(match);
    } catch (error) {
      app.innerHTML = `<div class="error">...</div>`;
      console.error(error);
    }
    return;
  }
}
```

---

## 五、用户体验优化（细节打磨）

### 5.1 页面切换过渡动画

**当前**：`app.innerHTML = ...` 瞬间切换，视觉跳跃。

**修复**：
```css
.main { transition: opacity 120ms ease; }
.main.page-enter { opacity: 0; }
.main.page-enter-active { opacity: 1; }
```

### 5.2 加载状态优化

| 当前 | 优化后 |
|------|--------|
| 全局 skeleton 占位 | skeleton + 加载文本 + 预计时间 |
| 按钮 disabled 无视觉变化 | 已修复（opacity 0.55 + cursor: not-allowed）|
| 表单提交后无进度反馈 | 添加提交中 spinner |

### 5.3 网络断开检测

```js
// api.js 中增加网络状态监听
window.addEventListener('online', () => /* 刷新数据 */);
window.addEventListener('offline', () => /* 显示离线横幅 */);
```

---

## 六、安全性检查

### 6.1 XSS 防护

| 检查项 | 状态 | 说明 |
|--------|------|------|
| `escapeHtml` 使用 | ✅ | 所有用户输出已转义 |
| `innerHTML` 拼接 | ⚠️ | 虽然有转义，但推荐使用 `textContent` 或 DOM API 更安全 |
| URL 参数注入 | ✅ | `encodeURIComponent` 已使用 |
| `download` 链接 | ✅ | 文件名通过 URL 传递，不直接操作 DOM |

### 6.2 潜在风险

```js
// job-detail.js
app.innerHTML = render(job, files, ...); // 虽然 render 内部用了 escapeHtml
// 但如果某个字段绕过 escapeHtml（如 job.error），仍有风险

// 建议：增加 Content Security Policy (CSP)
// <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com;">
```

---

## 七、实施优先级建议

### 第一阶段：今天完成（体验杀手级问题）

1. **job-detail.js**：用户交互时暂停自动刷新 / 使用 visibilitychange
2. **dashboard.js**：搜索添加 200ms 防抖
3. **dashboard.js / job-detail.js**：后台标签页停止轮询

### 第二阶段：本周完成（性能优化）

4. **timeline.js**：缓存 `getComputedStyle`、优化 marks 查找
5. **CSS**：`backdrop-filter` 添加条件渲染、动画添加 `prefers-reduced-motion`
6. **api.js**：添加超时和基础重试

### 第三阶段：后续迭代（体验打磨）

7. **router.js**：添加路由切换动画、错误边界
8. **job-detail.js**：DOM diff 更新替代完全替换
9. **全局**：CSP 策略、网络状态检测、页面标题同步

---

## 八、修改预估

| 文件 | 预估改动行数 | 风险 |
|------|-------------|------|
| `dashboard.js` | ~15 行 | 低 |
| `job-detail.js` | ~25 行 | 中（需测试视频/编辑状态）|
| `timeline.js` | ~20 行 | 低 |
| `style.css` | ~15 行 | 低 |
| `api.js` | ~15 行 | 低 |
| `router.js` | ~8 行 | 低 |
| `index.html` | ~3 行 | 低 |

**总计**：约 101 行改动，可分 3 个 PR 提交。

---

**Frontend Developer** | 2026-05-17
