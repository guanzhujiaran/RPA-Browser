# 前端分页接口迁移指南

## 📋 概述

本文档说明后端列表接口（Action/Workflow/Plugin）从 **skip/limit 模式**迁移到**标准分页模式**的前端适配方法。

---

## 🔧 API 变更对比

### 1️⃣ 请求参数变化

#### ❌ 旧版（已废弃）
```typescript
// 旧的请求格式
POST /api/v1/rpa/browser/control/actions/list
{
  "skip": 0,        // 跳过记录数
  "limit": 100,     // 返回记录数
  "filter_type": "all",
  "sort_by": "updated_at",
  "sort_order": "desc"
}
```

#### ✅ 新版（当前使用）
```typescript
// 新的请求格式
POST /api/v1/rpa/browser/control/actions/list
{
  "page": 1,        // 当前页码（从 1 开始）
  "per_page": 10,   // 每页数量
  "filter_type": "all",
  "sort_by": "updated_at",
  "sort_order": "desc"
}
```

**关键变化**：
- `skip` → `page`：从"跳过多少条"改为"第几页"
- `limit` → `per_page`：语义更清晰，表示每页显示数量
- `page` 从 **1** 开始（不是 0）

---

### 2️⃣ 响应结构变化

#### ❌ 旧版（已废弃）
```json
{
  "code": 200,
  "message": "success",
  "data": [
    { "id": 1, "name": "Action 1", ... },
    { "id": 2, "name": "Action 2", ... }
  ]
}
```

#### ✅ 新版（当前使用）
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "page": 1,           // 当前页码
    "per_page": 10,      // 每页数量
    "total": 45,         // 总记录数
    "pages": 5,          // 总页数（自动计算）
    "has_next": true,    // 是否有下一页
    "has_prev": false,   // 是否有上一页
    "next_page": 2,      // 下一页页码
    "prev_page": 1,      // 上一页页码
    "items": [           // 数据列表
      { "id": 1, "name": "Action 1", ... },
      { "id": 2, "name": "Action 2", ... }
    ]
  }
}
```

**关键变化**：
- `data` 从**数组**变为**对象**
- 数据列表在 `data.items` 中
- 新增分页元数据：`total`, `pages`, `has_next`, `has_prev` 等

---

## 📘 TypeScript 类型定义

### 基础分页类型

```typescript
// base_pagination.ts

/**
 * 分页请求参数
 */
export interface BasePaginationReq {
  /** 当前页码（从 1 开始） */
  page: number;
  /** 每页数量 */
  per_page: number;
}

/**
 * 分页响应结构
 */
export interface BasePaginationResp<T> {
  /** 当前页码 */
  page: number;
  /** 每页数量 */
  per_page: number;
  /** 总记录数 */
  total: number;
  /** 总页数（自动计算） */
  pages: number;
  /** 是否有下一页 */
  has_next: boolean;
  /** 是否有上一页 */
  has_prev: boolean;
  /** 下一页页码 */
  next_page: number;
  /** 上一页页码 */
  prev_page: number;
  /** 数据列表 */
  items: T[];
}

/**
 * 通用列表请求（包含筛选和排序）
 */
export interface ListRequest extends BasePaginationReq {
  /** 筛选类型: all | private | public | community | verified */
  filter_type?: string;
  /** 排序字段: updated_at | likes_count | forks_count | created_at | name */
  sort_by?: string;
  /** 排序方向: desc | asc */
  sort_order?: string;
}
```

### Action 相关类型

```typescript
// action_types.ts

import { BasePaginationResp } from './base_pagination';

/**
 * Action 列表项
 */
export interface ActionListItem {
  id: number;
  action_id: string;
  name: string;
  action_type: string;
  description: string;
  steps_count: number;
  is_enabled: boolean;
  is_public: boolean;
  likes_count: number;
  reports_count: number;
  is_verified: boolean;
  forks_count: number;
  forked_from_id: number | null;
  created_at: string;
  updated_at: string;
}

/**
 * Action 列表响应
 */
export type ActionListResponse = BasePaginationResp<ActionListItem>;
```

### Workflow 相关类型

```typescript
// workflow_types.ts

import { BasePaginationResp } from './base_pagination';

/**
 * Workflow 列表项
 */
export interface WorkflowListItem {
  id: number;
  workflow_id: string;
  name: string;
  description: string;
  tags: string[];
  is_enabled: boolean;
  is_public: boolean;
  likes_count: number;
  reports_count: number;
  is_verified: boolean;
  forks_count: number;
  forked_from_id: number | null;
  created_at: string;
  updated_at: string;
}

/**
 * Workflow 列表响应
 */
export type WorkflowListResponse = BasePaginationResp<WorkflowListItem>;
```

### Plugin 相关类型

```typescript
// plugin_types.ts

import { BasePaginationResp } from './base_pagination';

/**
 * Plugin 列表项
 */
export interface PluginListItem {
  id: number;
  plugin_id: string;
  name: string;
  hook_type: string;
  custom_action_id: string;
  is_enabled: boolean;
  priority: number;
  is_public: boolean;
  likes_count: number;
  reports_count: number;
  is_verified: boolean;
  forks_count: number;
  forked_from_id: number | null;
  created_at: string;
  updated_at: string;
}

/**
 * Plugin 列表响应
 */
export type PluginListResponse = BasePaginationResp<PluginListItem>;
```

---

## 💻 前端调用示例

### 1️⃣ Axios 封装

```typescript
// api/action_api.ts

import axios from 'axios';
import { ActionListResponse, ListRequest } from '@/types/action_types';

const apiClient = axios.create({
  baseURL: '/api/v1/rpa/browser/control',
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * 获取 Action 列表
 */
export async function getActionList(params: ListRequest): Promise<ActionListResponse> {
  const response = await apiClient.post('/actions/list', {
    page: params.page || 1,
    per_page: params.per_page || 10,
    filter_type: params.filter_type || 'all',
    sort_by: params.sort_by || 'updated_at',
    sort_order: params.sort_order || 'desc',
  });

  // 注意：响应数据在 response.data.data 中
  return response.data.data;
}

/**
 * 获取 Workflow 列表
 */
export async function getWorkflowList(params: ListRequest): Promise<WorkflowListResponse> {
  const response = await apiClient.post('/workflows/list', params);
  return response.data.data;
}

/**
 * 获取 Plugin 列表
 */
export async function getPluginList(params: ListRequest): Promise<PluginListResponse> {
  const response = await apiClient.post('/plugins/list', params);
  return response.data.data;
}
```

### 2️⃣ React Hook 示例

```typescript
// hooks/useActionList.ts

import { useState, useEffect, useCallback } from 'react';
import { getActionList } from '@/api/action_api';
import { ActionListItem, ActionListResponse } from '@/types/action_types';

interface UseActionListOptions {
  initialPage?: number;
  initialPageSize?: number;
  filterType?: string;
  sortBy?: string;
  sortOrder?: string;
}

export function useActionList(options: UseActionListOptions = {}) {
  const {
    initialPage = 1,
    initialPageSize = 10,
    filterType = 'all',
    sortBy = 'updated_at',
    sortOrder = 'desc',
  } = options;

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ActionListResponse | null>(null);
  
  const [pagination, setPagination] = useState({
    page: initialPage,
    per_page: initialPageSize,
    filter_type: filterType,
    sort_by: sortBy,
    sort_order: sortOrder,
  });

  // 加载数据
  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    
    try {
      const result = await getActionList(pagination);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [pagination]);

  // 初始加载
  useEffect(() => {
    loadData();
  }, [loadData]);

  // 切换页码
  const changePage = (page: number) => {
    setPagination(prev => ({ ...prev, page }));
  };

  // 改变每页数量
  const changePageSize = (per_page: number) => {
    setPagination(prev => ({ ...prev, per_page, page: 1 })); // 重置到第一页
  };

  // 改变筛选条件
  const changeFilter = (filter_type: string) => {
    setPagination(prev => ({ ...prev, filter_type, page: 1 })); // 重置到第一页
  };

  return {
    loading,
    error,
    data,
    items: data?.items || [],
    total: data?.total || 0,
    pagination: {
      current: data?.page || 1,
      pageSize: data?.per_page || 10,
      total: data?.total || 0,
      pages: data?.pages || 0,
      hasNext: data?.has_next || false,
      hasPrev: data?.has_prev || false,
    },
    changePage,
    changePageSize,
    changeFilter,
    refresh: loadData,
  };
}
```

### 3️⃣ Vue Composition API 示例

```typescript
// composables/useWorkflowList.ts

import { ref, computed, watch } from 'vue';
import { getWorkflowList } from '@/api/workflow_api';
import { WorkflowListResponse } from '@/types/workflow_types';

export function useWorkflowList() {
  const loading = ref(false);
  const error = ref<string | null>(null);
  const data = ref<WorkflowListResponse | null>(null);
  
  const pagination = ref({
    page: 1,
    per_page: 10,
    filter_type: 'all',
    sort_by: 'updated_at',
    sort_order: 'desc',
  });

  // 计算属性
  const items = computed(() => data.value?.items || []);
  const total = computed(() => data.value?.total || 0);
  const pages = computed(() => data.value?.pages || 0);

  // 加载数据
  const loadData = async () => {
    loading.value = true;
    error.value = null;
    
    try {
      data.value = await getWorkflowList(pagination.value);
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载失败';
    } finally {
      loading.value = false;
    }
  };

  // 监听分页变化
  watch(pagination, () => {
    loadData();
  }, { deep: true });

  // 切换页码
  const changePage = (page: number) => {
    pagination.value.page = page;
  };

  // 改变每页数量
  const changePageSize = (per_page: number) => {
    pagination.value.per_page = per_page;
    pagination.value.page = 1; // 重置到第一页
  };

  return {
    loading,
    error,
    items,
    total,
    pages,
    pagination,
    changePage,
    changePageSize,
    refresh: loadData,
  };
}
```

---

## 🎨 UI 组件适配建议

### 1️⃣ Ant Design Pagination

```tsx
// components/ActionList.tsx

import React from 'react';
import { Table, Pagination } from 'antd';
import { useActionList } from '@/hooks/useActionList';
import { ActionListItem } from '@/types/action_types';

const ActionList: React.FC = () => {
  const {
    loading,
    items,
    pagination,
    changePage,
    changePageSize,
  } = useActionList();

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '描述', dataIndex: 'description', key: 'description' },
    { title: '步骤数', dataIndex: 'steps_count', key: 'steps_count' },
    { title: '点赞数', dataIndex: 'likes_count', key: 'likes_count' },
    { title: 'Fork数', dataIndex: 'forks_count', key: 'forks_count' },
  ];

  return (
    <div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={false} // 禁用 Table 内置分页
      />
      
      <Pagination
        current={pagination.current}
        pageSize={pagination.pageSize}
        total={pagination.total}
        showSizeChanger
        showQuickJumper
        showTotal={(total) => `共 ${total} 条`}
        onChange={(page) => changePage(page)}
        onShowSizeChange={(_, size) => changePageSize(size)}
        style={{ marginTop: 16, textAlign: 'right' }}
      />
    </div>
  );
};

export default ActionList;
```

### 2️⃣ Element UI Pagination

```vue
<!-- components/WorkflowList.vue -->

<template>
  <div>
    <el-table
      :data="items"
      v-loading="loading"
      row-key="id"
    >
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="description" label="描述" />
      <el-table-column prop="tags" label="标签">
        <template #default="{ row }">
          <el-tag v-for="tag in row.tags" :key="tag">{{ tag }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="likes_count" label="点赞数" />
      <el-table-column prop="forks_count" label="Fork数" />
    </el-table>

    <el-pagination
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      :page-sizes="[10, 20, 50, 100]"
      :total="total"
      layout="total, sizes, prev, pager, next, jumper"
      @current-change="handlePageChange"
      @size-change="handleSizeChange"
      style="margin-top: 16px; text-align: right"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { useWorkflowList } from '@/composables/useWorkflowList';

const {
  loading,
  items,
  total,
  pagination,
  changePage,
  changePageSize,
} = useWorkflowList();

const currentPage = ref(pagination.current);
const pageSize = ref(pagination.pageSize);

// 页码变化
const handlePageChange = (page: number) => {
  changePage(page);
};

// 每页数量变化
const handleSizeChange = (size: number) => {
  changePageSize(size);
};

// 同步外部状态
watch(() => pagination.current, (val) => {
  currentPage.value = val;
});

watch(() => pagination.pageSize, (val) => {
  pageSize.value = val;
});
</script>
```

### 3️⃣ 自定义分页组件

```tsx
// components/SimplePagination.tsx

import React from 'react';

interface SimplePaginationProps {
  current: number;
  total: number;
  pageSize: number;
  pages: number;
  hasNext: boolean;
  hasPrev: boolean;
  onPageChange: (page: number) => void;
}

const SimplePagination: React.FC<SimplePaginationProps> = ({
  current,
  total,
  pageSize,
  pages,
  hasNext,
  hasPrev,
  onPageChange,
}) => {
  if (total === 0) {
    return <div className="text-gray-500">暂无数据</div>;
  }

  return (
    <div className="flex items-center justify-between">
      <div className="text-sm text-gray-600">
        第 {current} / {pages} 页，共 {total} 条
      </div>
      
      <div className="flex gap-2">
        <button
          disabled={!hasPrev}
          onClick={() => onPageChange(current - 1)}
          className="px-3 py-1 border rounded disabled:opacity-50"
        >
          上一页
        </button>
        
        <span className="px-3 py-1">
          {current} / {pages}
        </span>
        
        <button
          disabled={!hasNext}
          onClick={() => onPageChange(current + 1)}
          className="px-3 py-1 border rounded disabled:opacity-50"
        >
          下一页
        </button>
      </div>
    </div>
  );
};

export default SimplePagination;
```

---

## ⚠️ 注意事项

### 1️⃣ 页码从 1 开始

```typescript
// ❌ 错误：页码从 0 开始
const request = {
  page: 0,  // 后端期望从 1 开始
  per_page: 10,
};

// ✅ 正确：页码从 1 开始
const request = {
  page: 1,  // 第一页
  per_page: 10,
};
```

### 2️⃣ 空数据处理

```typescript
// 当 total 为 0 时，items 为空数组
if (data.total === 0) {
  console.log('没有数据');
  // 显示空状态提示
  return <Empty description="暂无数据" />;
}

// 安全访问 items
const items = data?.items || [];
```

### 3️⃣ 切换筛选条件时重置页码

```typescript
// ❌ 错误：切换筛选条件后保持当前页码
const changeFilter = (filter_type: string) => {
  setPagination(prev => ({ ...prev, filter_type }));
  // 如果当前在第 5 页，但新筛选条件只有 2 页，会导致空白
};

// ✅ 正确：切换筛选条件时重置到第一页
const changeFilter = (filter_type: string) => {
  setPagination(prev => ({ ...prev, filter_type, page: 1 }));
};
```

### 4️⃣ 改变每页数量时重置页码

```typescript
// ✅ 正确：改变每页数量时重置到第一页
const changePageSize = (per_page: number) => {
  setPagination(prev => ({ ...prev, per_page, page: 1 }));
};
```

### 5️⃣ 利用后端提供的分页信息

```typescript
// ✅ 推荐：使用后端计算的 pages、has_next、has_prev
const { pages, has_next, has_prev } = data;

// 可以直接用于 UI 展示，无需前端重新计算
<button disabled={!has_prev}>上一页</button>
<span>{page} / {pages}</span>
<button disabled={!has_next}>下一页</button>
```

### 6️⃣ 错误处理

```typescript
try {
  const data = await getActionList(params);
  // 处理成功
} catch (error) {
  if (axios.isAxiosError(error)) {
    // 处理 HTTP 错误
    console.error('请求失败:', error.response?.status, error.response?.data);
  } else {
    // 处理其他错误
    console.error('未知错误:', error);
  }
}
```

---

## 🔄 迁移检查清单

- [ ] 将所有列表接口的请求参数从 `skip/limit` 改为 `page/per_page`
- [ ] 确保 `page` 从 **1** 开始（不是 0）
- [ ] 修改响应数据解析：从 `response.data` 改为 `response.data.items`
- [ ] 更新分页组件配置，使用 `total` 和 `per_page`
- [ ] 添加空数据处理逻辑（`total === 0`）
- [ ] 切换筛选条件时重置页码到 1
- [ ] 改变每页数量时重置页码到 1
- [ ] 测试所有列表页面（Action、Workflow、Plugin）
- [ ] 验证分页功能（上一页、下一页、跳转、改变每页数量）

---

## 📞 常见问题

### Q1: 为什么 `page` 要从 1 开始？

A: 这是业界标准做法（如 Google、GitHub），更符合用户直觉。后端使用 `(page - 1) * per_page` 计算 skip 值。

### Q2: 如何处理大数据量的性能问题？

A: 
- 合理设置 `per_page`（建议 10-50）
- 使用虚拟滚动（Virtual Scrolling）渲染长列表
- 考虑服务端搜索和过滤，减少数据传输量

### Q3: 能否同时支持旧版和新版接口？

A: 不建议。建议一次性完成迁移，避免维护两套代码。可以使用 Feature Flag 控制灰度发布。

### Q4: 如何调试分页问题？

A: 
- 检查浏览器 Network 面板的请求参数
- 验证响应结构是否符合预期
- 使用 Postman 或 curl 直接测试 API

---

## 📚 相关文档

- [后端 API 文档](../docs/API_DOCUMENTATION.md)
- [TypeScript 类型定义](./types/)
- [前端组件库文档](https://ant.design/components/pagination/)

---

**最后更新**: 2026-05-13  
**维护者**: RPA-Browser 团队
