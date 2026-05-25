# Video Automation 前端 UI 设计评审报告

> 评审日期：2026-05-16
> 评审范围：`web/` 目录下全部前端代码（HTML / CSS / JS）
> 评审维度：设计系统、可访问性、视觉层次、响应式适配、交互细节

---

## 一、当前设计亮点（保持）

| 亮点 | 说明 |
|------|------|
| 深色主题统一 | `#0a0a14` 底色 + 青色 `#00d4aa` accent，现代感强 |
| CSS 变量系统 | `:root` 中已定义颜色、圆角、过渡、间距等基础 token |
| 字体搭配 | Inter + JetBrains Mono，UI 与代码数据区分明确 |
| 微交互意识 | hover 时 `translateY`、边框色变化、进度条动画等 |
| 状态覆盖全面 | loading / empty / error / skeleton 四种状态都有处理 |
| 响应式基础 | `@media (max-width: 860px)` 已实现移动端底部导航 |

---

## 二、需要修改的问题清单

### 🔴 P0 - 可访问性（Accessibility）问题

这些问题影响所有用户，尤其是使用键盘或辅助技术的用户。

| 问题 | 位置 | 严重程度 | 修复建议 |
|------|------|---------|---------|
| **按钮无 focus-visible 样式** | `.button` 全类 | 高 | 为所有按钮添加 `&:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }` |
| **导航链接无 focus 指示** | `.nav-link` | 高 | 同上，确保键盘 Tab 导航可见 |
| **语言切换按钮无 focus** | `.lang-button` | 中 | 添加 focus-visible outline |
| **卡片作为链接但无焦点指示** | `.job-card` | 高 | 作为 `<a>` 标签已有 hover，需补充 focus 状态 |
| **进度条缺少 ARIA 属性** | `.progress` | 中 | 添加 `role="progressbar" aria-valuenow aria-valuemin aria-valuemax` |
| **时间线 canvas 无替代文本** | `canvas.timeline` | 中 | 添加 `aria-label` 描述时间线内容 |
| **复选框原生样式不可见** | `.check input[type="checkbox"]` | 中 | 深色主题下原生复选框对比度不足，建议自定义样式 |

### 🟠 P1 - 设计系统不一致

| 问题 | 位置 | 修复建议 |
|------|------|---------|
| **Primary 按钮无 hover 样式** | `.button.primary` | 添加 hover 时背景色变暗或亮度提升，如 `background: #00b894` |
| **Danger 按钮无 hover** | `.button.danger` | 添加 hover 状态，提升交互反馈 |
| **按钮 hover 位移不统一** | `.button` vs `.job-card` | 卡片 `-2px`，按钮 `-1px`，建议统一为 `-2px` 或取消按钮位移避免布局抖动 |
| **select option 选中样式突兀** | `.field select option:checked` | 当前黑底绿字，与整体风格不协调，建议改为深色背景 + 青色文字 |
| **表单输入框圆角不一致** | `.search` (8px) vs `.field input` (6px) vs `.field select` (6px) | 统一为 8px（`--radius-md`） |

### 🟡 P2 - 视觉层次与信息架构

| 问题 | 位置 | 修复建议 |
|------|------|---------|
| **页面标题与卡片标题对比不足** | `.page-title` (clamp 26-36px) vs `.job-title` (18px) | 将 `.job-title` 提升至 20px 或加粗，强化层级 |
| **Pipeline 阶段在小屏溢出** | `.pipeline` 固定 13 列 | 小屏下改为可横向滑动，并添加滑动指示器 |
| **时间线区域缺少上下文** | `canvas.timeline` | 在时间线上方添加图例（legend），说明各颜色代表的含义 |
| **Clip 编辑器表格信息密度过高** | `.clip-editor` | 为表格添加斑马纹（striped rows），提升可读性；固定操作列 |
| **空状态过于简陋** | `.empty` | 添加一个 subtle 的图标（如文件夹图标或搜索图标），减少空白感 |
| **下载区域按钮堆叠杂乱** | `.downloads` | 为不同文件类型分组，添加文件类型图标 |

### 🟢 P3 - 响应式适配

| 问题 | 位置 | 修复建议 |
|------|------|---------|
| **Clip 编辑器溢出无滚动** | `.clip-editor { min-width: 980px }` | 父容器添加 `overflow-x: auto` 和滚动条样式 |
| **移动端底部导航遮挡内容** | `.main { padding-bottom: 88px }` | 88px 可能不够，建议增加到 100px 或动态计算 |
| **Job 卡片移动端 thumb 高度** | `.job-card` 1fr 布局 | thumb 在移动端可能过矮，建议设置 `min-height: 120px` |
| **设置页双列在小屏未适配** | `.settings-grid` | 在 860px 断点下已改为 1fr，但 860-1024px 区间可能仍显拥挤，建议在 1024px 也改为单列 |

### 🔵 P4 - 交互与动效优化

| 问题 | 位置 | 修复建议 |
|------|------|---------|
| **按钮点击无 active 状态** | `.button` | 添加 `:active { transform: translateY(0); transition-duration: 80ms; }` |
| **卡片点击区域不明确** | `.job-card` | 考虑在 hover 时添加微妙的光晕（box-shadow 扩散） |
| **搜索框缺少清除按钮** | `.search` | 添加 `type="search"` 或右侧清除图标 |
| **语言切换无过渡动画** | `.lang-button.active` | 添加背景色和文字色的过渡效果 |
| **页面切换无过渡** | `app.innerHTML` 替换 | 考虑添加淡入淡出（opacity transition）提升流畅感 |

---

## 三、具体代码修改建议

### 3.1 CSS 修复示例

```css
/* === 可访问性：全局 focus-visible === */
a:focus-visible,
button:focus-visible,
.nav-link:focus-visible,
.lang-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* === 按钮状态完善 === */
.button.primary:hover:not(:disabled) {
  background: #00b894;
  border-color: #00b894;
}
.button.danger:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.22);
}
.button:active:not(:disabled) {
  transform: translateY(0);
  transition-duration: 80ms;
}

/* === 复选框自定义样式 === */
.check input[type="checkbox"] {
  appearance: none;
  width: 18px;
  height: 18px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg-surface);
  cursor: pointer;
  position: relative;
}
.check input[type="checkbox"]:checked {
  background: var(--accent);
  border-color: var(--accent);
}
.check input[type="checkbox"]:checked::after {
  content: "";
  position: absolute;
  left: 5px;
  top: 2px;
  width: 5px;
  height: 10px;
  border: solid #06110f;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
.check input[type="checkbox"]:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* === 进度条 ARIA 支持 === */
.progress {
  role: progressbar;
  /* 通过 JS 动态设置 aria-valuenow */
}

/* === 表格斑马纹 === */
.table tbody tr:nth-child(even) {
  background: rgba(255, 255, 255, 0.02);
}

/* === 搜索框清除按钮 === */
.search::-webkit-search-cancel-button {
  -webkit-appearance: none;
  appearance: none;
  height: 16px;
  width: 16px;
  background: var(--text-muted);
  mask: url("data:image/svg+xml,...") no-repeat center;
  cursor: pointer;
}

/* === 移动端优化 === */
@media (max-width: 860px) {
  .clip-editor-wrapper {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .job-card .thumb {
    min-height: 120px;
  }
}
```

### 3.2 JS 修复示例

```js
// 进度条添加 ARIA 属性（在 renderLiveProgress 中）
function renderLiveProgress(job) {
  const percent = typeof job.stage_progress === "number" ? Math.round(job.stage_progress) : null;
  // ...
  return `
    <div class="progress stage-progress"
         role="progressbar"
         aria-valuemin="0"
         aria-valuemax="100"
         aria-valuenow="${percent ?? 0}"
         aria-label="${t("dashboard.progress")}">
      <span id="stage-progress-fill" style="width: ${percent === null ? 0 : percent}%"></span>
    </div>
  `;
}

// 更新时同步更新 ARIA（在 updateLiveStatus 中）
const progressBar = document.querySelector('.stage-progress');
if (progressBar && percent !== null) {
  progressBar.setAttribute('aria-valuenow', percent);
}
```

---

## 四、优先级建议

建议按以下顺序实施：

1. **第一阶段（立即可做）**：修复 P0 可访问性问题 + P1 按钮 hover 不一致（约 30 行 CSS）
2. **第二阶段（本周）**：优化 P2 视觉层次（空状态图标、表格斑马纹、时间线图例）
3. **第三阶段（后续迭代）**：完善 P3 响应式细节 + P4 动效优化

---

## 五、整体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉风格 | ⭐⭐⭐⭐☆ | 深色主题成熟，accent 色选择有辨识度 |
| 设计一致性 | ⭐⭐⭐☆☆ | 基础组件有，但交互状态覆盖不全 |
| 可访问性 | ⭐⭐☆☆☆ | focus 管理缺失，ARIA 属性不足 |
| 响应式 | ⭐⭐⭐☆☆ | 基础断点有，细节打磨不够 |
| 信息架构 | ⭐⭐⭐⭐☆ | 页面结构清晰，功能分区合理 |

**总体评价**：这是一个有设计潜力的前端实现，核心视觉语言已经确立。当前最大的短板是**可访问性**和**交互状态的完整性**。建议优先补齐 focus 管理和 ARIA 属性，这不仅能通过 WCAG 基础要求，也能显著提升键盘用户的体验。
