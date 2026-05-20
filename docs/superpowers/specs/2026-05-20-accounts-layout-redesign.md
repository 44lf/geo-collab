# 账号授权页面布局优化设计

**日期**: 2026-05-20  
**功能**: 账号列表展示优化 + 平台选择下拉框  
**影响范围**: 前端 (`web/src/features/accounts/AccountsWorkspace.tsx`)

## 概述

优化"平台账号授权"页面，以支持未来账号增加和平台拓展：
- 将账号列表从单列改为两列网格布局
- 移除"复用状态"按钮
- 添加平台选择下拉框，替代现有的 readonly input
- 实现列表自动筛选和持久化平台选择

## 需求背景

随着账号和平台数量增加，单列布局会造成页面冗长、空间浪费。两列布局能显著提升空间利用率，同时保持卡片信息的清晰性和完整度。平台选择下拉框提供了更灵活的过滤能力。

## 设计细节

### 1. 平台选择下拉框

**现状**：
```jsx
<label>
  平台
  <input value={selectedPlatformName} readOnly />
</label>
```

**改动**：
- 将 readonly input 改为 `<select>` 下拉框
- 选项包括：`-- 全部 --` 以及所有支持的平台（从 `listPlatforms()` 动态获取）
- 下拉框选择值存储到 localStorage，页面刷新后自动恢复用户的上次选择

**逻辑**：
- 选择"全部"时，"添加授权"按钮 disabled
- 选择具体平台后，"添加授权"按钮启用
- 只有选了具体平台，才允许添加账号

### 2. 移除"复用状态"按钮

**移除操作**：
- 删除 `login(false)` 相关的代码路径
- 检查后端 API `loginPlatformAccount` 的 `use_browser` 参数依赖，确保移除不会导致后端逻辑崩溃
- 如果不确定是否安全删除，先从前端隐藏该按钮（`display: none`），稍后再清理

**账号添加流程**：
- 保留"添加授权"按钮，对应 `startNewRemoteLogin()`，通过浏览器远程登录

### 3. 账号列表两列布局

**CSS 改动**：
```css
.accountList {
  display: grid;
  grid-template-columns: repeat(2, 1fr);  /* 从 1fr 改为 repeat(2, 1fr) */
  gap: 12px;  /* 调整间距 */
  max-height: 500px;  /* 添加高度限制 */
  overflow-y: auto;  /* 启用滚动条 */
}
```

**卡片样式微调**：
- padding 从 `20px 22px` 减小到 `16px 18px`
- 字号保持不变，保证信息完整性和可读性
- 按钮大小、间距无需大幅调整

### 4. 列表筛选和持久化

**平台筛选**：
- 下拉框变化时，实时筛选账号列表
- 选择"全部"显示所有账号，选择具体平台只显示该平台的账号

**刷新按钮**：
- 新增"刷新"按钮在列表标题处
- 刷新时只更新列表数据（调用后端 API），不重新加载整个页面
- 刷新后保持用户选择的平台（通过 localStorage 恢复）

**持久化方案**：
```javascript
// 保存用户选择
localStorage.setItem('selectedPlatform', platformCode);

// 页面加载时恢复
const savedPlatform = localStorage.getItem('selectedPlatform');
if (savedPlatform) selectElement.value = savedPlatform;
```

## 实现要点

### React 状态管理

```jsx
// 已有的 state
const [accounts, setAccounts] = useState<Account[]>([]);
const [platforms, setPlatforms] = useState<PlatformOption[]>([]);

// 新增 state
const [selectedPlatform, setSelectedPlatform] = useState<string>('');

// 初始化：从 localStorage 恢复选择
useEffect(() => {
  const saved = localStorage.getItem('selectedPlatform');
  if (saved) setSelectedPlatform(saved);
}, []);

// 平台变化时保存和筛选
const handlePlatformChange = (value: string) => {
  setSelectedPlatform(value);
  localStorage.setItem('selectedPlatform', value);
};

// 本地筛选逻辑
const filteredAccounts = selectedPlatform 
  ? accounts.filter(a => a.platform_code === selectedPlatform)
  : accounts;
```

### 后端依赖检查

在删除"复用状态"之前，需要检查：
1. `loginPlatformAccount` 的 `use_browser` 参数是否必需
2. 后端是否有逻辑依赖 `use_browser === false` 的路径
3. 如果后端有依赖，需要与后端开发确认移除影响

**建议**：先隐藏按钮，等后端确认后再删除代码。

## 样式变更

### 主要 CSS 修改

| 元素 | 原值 | 新值 | 原因 |
|------|------|------|------|
| `.accountList` grid | `1fr` | `repeat(2, 1fr)` | 两列布局 |
| `.accountList` gap | `1px` | `12px` | 增加列间距 |
| `.accountList` max-height | 无 | `500px` | 添加高度限制，启用滚动 |
| `.accountCard` padding | `20px 22px` | `16px 18px` | 微调间距，保持可读性 |

### 新增样式

```css
/* 列表标题和刷新按钮 */
.listHeader {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 0;
}

.listHeader h3 {
  font-size: 13px;
  text-transform: uppercase;
  color: var(--fg-3);
  letter-spacing: 1px;
  margin: 0;
}

.listHeader button {
  padding: 6px 12px;
  font-size: 12px;
  border: 1px solid var(--hair);
  border-radius: var(--r-sm);
  cursor: pointer;
  transition: all .12s;
}
```

## 交互流程

```
用户选择平台
    ↓
下拉框 onChange 事件
    ├→ 保存到 localStorage
    ├→ 更新 selectedPlatform state
    └→ 更新"添加授权"按钮状态（选"全部"时 disabled）

用户点击刷新
    ↓
调用 refreshAccounts() API
    ├→ 更新 accounts list
    └→ 自动应用当前平台筛选（无需用户重新选择）

页面加载
    ↓
从 localStorage 恢复 selectedPlatform
    ├→ 恢复下拉框值
    └→ 自动应用筛选
```

## 向后兼容性

- 代码改动仅涉及前端 UI，不改动 API 签名
- 现有账号数据结构无需变化
- 可平稳迁移，旧版本用户选择时会默认为空（"全部"）

## 测试计划

1. **平台选择**：验证下拉框选项加载正确，选择后列表正确筛选
2. **按钮状态**：验证选"全部"时按钮 disabled，选具体平台时启用
3. **持久化**：刷新页面后，下拉框保持用户的选择
4. **刷新功能**：点刷新后账号列表更新，但平台选择不变
5. **两列布局**：验证卡片在两列中正常显示，信息完整可读
6. **滚动条**：验证账号超过屏幕高度时出现滚动条

## 后续考虑

- 如果平台数量继续增加（>20 个），可考虑将下拉框改为搜索框
- 可添加"批量操作"功能，如批量导出、批量更新状态
- 考虑账号按平台分组显示，而非全部混合

## 受影响文件

- `web/src/features/accounts/AccountsWorkspace.tsx` — React 组件修改
- `web/src/styles.css` — 样式调整（`.accountList`, `.accountCard`, `.listHeader` 等）
