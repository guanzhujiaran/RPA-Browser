# RPA 全局反馈入口（前端需求）

> 适用组件：`src/views/rpa-browser/RpaBrowserLayout.vue`（RPA 顶层路由布局）、
> `src/views/rpa-browser/RpaFeedbackView.vue`（反馈页，路由 `rpa-browser/feedback`）
> 关联后端：`POST /api/v1/rpa/browser/feedback/submit`（落库 + 推送服主）
> 关联能力：同一套反馈也通过 MCP 工具 `rpa_feedback_submit` 暴露，后端 LLM 可自行上报

---

## 1. 背景

RPA 前端出问题的场景往往是「页面跑不通」：接口 500、画布渲染异常、动作拖不进去。
此时用户需要一个**不用先找菜单**的入口。现状是反馈页只挂在侧边导航「协作 → 报错反馈」，
而 `meta.hideSideNav` 的沉浸式页面（`BrowserStream` 调试台）**根本不渲染侧边导航**，
恰恰是最容易出问题、也最需要反馈的地方。

同时明确：**不做 playwright e2e 兜底**。前端跑不通由用户点按钮反馈，
后端 / MCP 跑不通由调用方 LLM 通过 `rpa_feedback_submit` 上报，不额外维护一套自动化回归。

---

## 2. 交互规格

| 项 | 规格 |
|---|---|
| 位置 | `RpaBrowserLayout` 根元素内部，`fixed bottom-6 right-6 z-50` 的圆角按钮 |
| 文案 | `反馈问题`（纯文字，不使用图标） |
| 显示条件 | 已登录（`isHydrated && uid`）且当前不在反馈页 |
| 点击 | `router.push({ name: RPA_BROWSER_FEEDBACK, query })`，站内 SPA 跳转 |
| 跳转参数 | `page_url` = 当前页面完整 URL；`browser_id` = 当前路由 `params.browserId`（存在时） |

为什么不用图标：`src/assets/svgs/` 里现成的 `audit/icon_tasklist_feedback_.svg` 属于
「固定 128×128 + 硬编码 `fill="#666"`」的旧资源，不满足项目图标规范（外部不可控色/尺寸）；
按 `icons` 规则不得改用 Element Plus 图标凑数，因此本期用纯文字按钮，**待用户补充合规 SVG 后再加图标**。

## 3. 上下文预填

`RpaFeedbackView` 挂载时读取 query 预填「错误上下文」：

- `page_url` → 表单 `page_url`（覆盖默认的「反馈页自身 URL」）；
- `browser_id` → 表单 `browser_id`。

提交时空值字段仍会被剔除（沿用原逻辑），不影响只填标题+描述的最简用法。

## 4. 约束

1. `RpaBrowserLayout` 是**顶层路由组件**，根层必须单根、根层不得有注释节点
   （新增的按钮写在根 `<div class="rpa-browser-layout">` 内部），提交前跑
   `npm run check:route-root -- src/views/rpa-browser/RpaBrowserLayout.vue`；
2. 站内 `router.push` 不产生文档请求，**无需** referrer 处理；
3. 样式全部用 Tailwind 原子类，不新增 `<style>` 块、不用内联样式；
4. 不改反馈后端接口与 MCP 工具契约。

## 5. 验收

1. 登录后进入任一 RPA 页面（含 Stream 调试台），右下角可见「反馈问题」按钮；
2. 点击后进入 `/rpa-browser/feedback`，「页面 URL」「相关浏览器 id」已按来源页预填；
3. 反馈页自身不显示该按钮（避免自我跳转）；
4. 未登录（游客浏览社区广场）不显示该按钮；
5. 提交成功仍弹出「已提交并通知服主」提示，行为与改造前一致。
