# CustomAction 前端集成指南

## 📋 概述

本文档说明前端如何创建、编辑和管理 **CustomAction（自定义组合动作）**。

---

## 🎯 核心概念

### 什么是 CustomAction？

CustomAction 是**用户定义的、可复用的动作组合**，类似于编程中的"函数"。

**特点**：
- ✅ 包含多个步骤（steps）
- ✅ 有明确的输入参数定义（parameters_schema）
- ✅ 可以被 Workflow 调用和复用
- ✅ 支持公开分享和社区 Fork

**与 Workflow 的区别**：
| 特性 | CustomAction | Workflow |
|------|-------------|----------|
| 定位 | 可复用组件（函数） | 完整流程（程序） |
| 复杂度 | 轻量级 | 重量级 |
| 控制流 | ❌ 不支持 | ✅ 支持循环、条件 |
| 用途 | 被多处调用 | 独立执行 |

---

## 🔧 API 接口

### 1️⃣ 创建 CustomAction

**接口**：`POST /api/v1/rpa/browser/control/actions/create`

**请求体**：
```typescript
interface CustomActionCreateRequest {
  name: string;                    // 显示名称（必填，用户可编辑）
  action_type?: "composite";       // 操作类型（默认 composite）
  description?: string;            // 描述
  parameters_schema?: Parameter[]; // 参数定义
  steps?: Step[];                  // 步骤列表
  tags?: string[];                 // 标签
  is_public?: boolean;             // 是否公开
  enabled_plugins?: string[];      // 引用的插件ID列表
}

interface Parameter {
  name: string;        // 参数名
  type: string;        // 参数类型: string, number, boolean
  required?: boolean;  // 是否必填
  default?: any;       // 默认值
  description?: string;// 描述
}

interface Step {
  action_id: string;   // 动作ID（原子动作或 CustomAction）
  params: Record<string, any>; // 动作参数
}
```

**响应**：
```typescript
interface CustomActionDetailResponse {
  id: number;
  action_id: string;     // 系统生成的唯一标识（格式：ca_xxx）
  name: string;
  version: string;
  action_type: string;
  description: string;
  parameters_schema: Parameter[];
  steps: Step[];
  tags: string[];
  is_enabled: boolean;
  is_public: boolean;
  created_at: string;
  updated_at: string;
}
```

**示例**：
```typescript
// 创建一个"网站登录"的 CustomAction
const response = await axios.post('/api/v1/rpa/browser/control/actions/create', {
  name: "网站登录",
  description: "自动登录到指定网站",
  parameters_schema: [
    {
      name: "username",
      type: "string",
      required: true,
      description: "用户名"
    },
    {
      name: "password",
      type: "string",
      required: true,
      description: "密码"
    }
  ],
  steps: [
    {
      action_id: "navigate",
      params: { url: "https://example.com/login" }
    },
    {
      action_id: "input",
      params: { selector: "#username", text: "{{username}}" }
    },
    {
      action_id: "input",
      params: { selector: "#password", text: "{{password}}" }
    },
    {
      action_id: "click",
      params: { selector: "#login-btn" }
    }
  ],
  tags: ["登录", "认证"],
  is_public: false
});

console.log(response.data.data.action_id); // "ca_a1b2c3d4e5f6"
```

---

### 2️⃣ 更新 CustomAction

**接口**：`POST /api/v1/rpa/browser/control/actions/update`

**请求体**：
```typescript
interface CustomActionUpdateRequest {
  id: number;                    // 数据库ID（必填）
  name?: string;                 // 新名称
  description?: string;          // 新描述
  parameters_schema?: Parameter[];
  steps?: Step[];
  tags?: string[];
  is_enabled?: boolean;
  is_public?: boolean;
  timeout?: number;
  enabled_plugins?: string[];
}
```

**示例**：
```typescript
// 更新 CustomAction
await axios.post('/api/v1/rpa/browser/control/actions/update', {
  id: 1,
  name: "网站登录（新版）",
  description: "改进的登录流程",
  steps: [
    // ... 新的步骤
  ]
});
```

---

### 3️⃣ 获取 CustomAction 列表

**接口**：`POST /api/v1/rpa/browser/control/actions/list`

**请求体**（分页）：
```typescript
interface CustomActionListRequest {
  page: number;           // 页码（从 1 开始）
  per_page: number;       // 每页数量
  filter_type?: string;   // all | private | public | community | verified
  sort_by?: string;       // updated_at | likes_count | forks_count | created_at | name
  sort_order?: string;    // desc | asc
}
```

**响应**：
```typescript
interface BasePaginationResp<ActionListItem> {
  page: number;
  per_page: number;
  total: number;
  pages: number;
  has_next: boolean;
  has_prev: boolean;
  next_page: number;
  prev_page: number;
  items: ActionListItem[];
}

interface ActionListItem {
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
```

**示例**：
```typescript
const response = await axios.post('/api/v1/rpa/browser/control/actions/list', {
  page: 1,
  per_page: 10,
  filter_type: "all",
  sort_by: "updated_at",
  sort_order: "desc"
});

console.log(response.data.data.items);    // 列表数据
console.log(response.data.data.total);    // 总数
console.log(response.data.data.pages);    // 总页数
```

---

### 4️⃣ 获取 CustomAction 详情

**接口**：`POST /api/v1/rpa/browser/control/actions/get`

**请求体**：
```typescript
{
  "id": 1  // 数据库ID
}
```

**响应**：完整的 `CustomActionDetailResponse`（包含 steps 和 parameters_schema）

---

### 5️⃣ 删除 CustomAction

**接口**：`POST /api/v1/rpa/browser/control/actions/delete`

**请求体**：
```typescript
{
  "id": 1  // 数据库ID
}
```

---

### 6️⃣ Fork CustomAction

**接口**：`POST /api/v1/rpa/browser/control/actions/fork`

**请求体**：
```typescript
{
  "id": 1,              // 原 CustomAction ID
  "new_name": "我的副本" // 新名称（可选）
}
```

**注意**：只能 Fork 公开的 CustomAction。

---

## 💻 React 组件示例

### 1️⃣ 创建/编辑表单组件

```tsx
// components/CustomActionForm.tsx

import React, { useState, useEffect } from 'react';
import { Form, Input, Button, Switch, Tag, Select, Card, Space } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import axios from 'axios';

interface Parameter {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

interface Step {
  action_id: string;
  params: Record<string, any>;
}

interface CustomActionFormProps {
  initialValues?: any;
  onSubmit: (values: any) => void;
  loading?: boolean;
}

const CustomActionForm: React.FC<CustomActionFormProps> = ({
  initialValues,
  onSubmit,
  loading = false,
}) => {
  const [form] = Form.useForm();
  const [parameters, setParameters] = useState<Parameter[]>(
    initialValues?.parameters_schema || []
  );
  const [steps, setSteps] = useState<Step[]>(
    initialValues?.steps || []
  );

  // 添加参数
  const addParameter = () => {
    setParameters([
      ...parameters,
      { name: '', type: 'string', required: false, description: '' }
    ]);
  };

  // 删除参数
  const removeParameter = (index: number) => {
    setParameters(parameters.filter((_, i) => i !== index));
  };

  // 更新参数
  const updateParameter = (index: number, field: keyof Parameter, value: any) => {
    const newParams = [...parameters];
    newParams[index] = { ...newParams[index], [field]: value };
    setParameters(newParams);
  };

  // 添加步骤
  const addStep = () => {
    setSteps([
      ...steps,
      { action_id: 'click', params: {} }
    ]);
  };

  // 删除步骤
  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index));
  };

  // 更新步骤
  const updateStep = (index: number, field: keyof Step, value: any) => {
    const newSteps = [...steps];
    newSteps[index] = { ...newSteps[index], [field]: value };
    setSteps(newSteps);
  };

  // 提交表单
  const handleSubmit = async (values: any) => {
    const data = {
      ...values,
      parameters_schema: parameters,
      steps: steps,
    };

    if (initialValues?.id) {
      // 更新
      data.id = initialValues.id;
      await axios.post('/api/v1/rpa/browser/control/actions/update', data);
    } else {
      // 创建
      await axios.post('/api/v1/rpa/browser/control/actions/create', data);
    }

    onSubmit(data);
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleSubmit}
      initialValues={initialValues}
    >
      {/* 基本信息 */}
      <Card title="基本信息" style={{ marginBottom: 16 }}>
        <Form.Item
          label="名称"
          name="name"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="例如：网站登录" />
        </Form.Item>

        <Form.Item label="描述" name="description">
          <Input.TextArea rows={3} placeholder="描述这个动作的功能" />
        </Form.Item>

        <Form.Item label="标签" name="tags">
          <Select
            mode="tags"
            placeholder="添加标签"
            style={{ width: '100%' }}
          />
        </Form.Item>

        <Form.Item label="是否公开" name="is_public" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Card>

      {/* 参数定义 */}
      <Card
        title="参数定义"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={addParameter}>
          添加参数
        </Button>}
        style={{ marginBottom: 16 }}
      >
        {parameters.map((param, index) => (
          <Space key={index} style={{ display: 'flex', marginBottom: 8 }} align="baseline">
            <Input
              placeholder="参数名"
              value={param.name}
              onChange={(e) => updateParameter(index, 'name', e.target.value)}
              style={{ width: 150 }}
            />
            <Select
              value={param.type}
              onChange={(value) => updateParameter(index, 'type', value)}
              style={{ width: 120 }}
            >
              <Select.Option value="string">字符串</Select.Option>
              <Select.Option value="number">数字</Select.Option>
              <Select.Option value="boolean">布尔</Select.Option>
            </Select>
            <Input
              placeholder="描述"
              value={param.description}
              onChange={(e) => updateParameter(index, 'description', e.target.value)}
              style={{ width: 200 }}
            />
            <Switch
              checked={param.required}
              onChange={(checked) => updateParameter(index, 'required', checked)}
              checkedChildren="必填"
              unCheckedChildren="选填"
            />
            <Button
              type="text"
              danger
              icon={<DeleteOutlined />}
              onClick={() => removeParameter(index)}
            />
          </Space>
        ))}

        {parameters.length === 0 && (
          <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>
            暂无参数，点击"添加参数"开始定义
          </div>
        )}
      </Card>

      {/* 步骤列表 */}
      <Card
        title="步骤列表"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={addStep}>
          添加步骤
        </Button>}
        style={{ marginBottom: 16 }}
      >
        {steps.map((step, index) => (
          <Card
            key={index}
            size="small"
            style={{ marginBottom: 8 }}
            title={`步骤 ${index + 1}`}
            extra={
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
                onClick={() => removeStep(index)}
              />
            }
          >
            <Form.Item label="动作类型">
              <Select
                value={step.action_id}
                onChange={(value) => updateStep(index, 'action_id', value)}
              >
                <Select.Option value="click">点击</Select.Option>
                <Select.Option value="input">输入</Select.Option>
                <Select.Option value="navigate">导航</Select.Option>
                <Select.Option value="screenshot">截图</Select.Option>
                <Select.Option value="wait">等待</Select.Option>
              </Select>
            </Form.Item>

            <Form.Item label="参数配置">
              <Input.TextArea
                rows={3}
                value={JSON.stringify(step.params, null, 2)}
                onChange={(e) => {
                  try {
                    const params = JSON.parse(e.target.value);
                    updateStep(index, 'params', params);
                  } catch (err) {
                    // JSON 解析错误，忽略
                  }
                }}
                placeholder='{"selector": "#button", "text": "{{username}}"}'
              />
            </Form.Item>
          </Card>
        ))}

        {steps.length === 0 && (
          <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>
            暂无步骤，点击"添加步骤"开始定义
          </div>
        )}
      </Card>

      {/* 提交按钮 */}
      <Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={loading}>
            {initialValues ? '更新' : '创建'}
          </Button>
          <Button onClick={() => form.resetFields()}>重置</Button>
        </Space>
      </Form.Item>
    </Form>
  );
};

export default CustomActionForm;
```

---

### 2️⃣ 列表页面组件

```tsx
// pages/CustomActionList.tsx

import React, { useState, useEffect } from 'react';
import { Table, Button, Pagination, Modal, message, Tag, Space } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ForkOutlined } from '@ant-design/icons';
import axios from 'axios';
import CustomActionForm from '@/components/CustomActionForm';

const CustomActionList: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<any[]>([]);
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    total: 0,
  });
  const [modalVisible, setModalVisible] = useState(false);
  const [editingItem, setEditingItem] = useState<any>(null);

  // 加载列表
  const loadList = async (page = 1, pageSize = 10) => {
    setLoading(true);
    try {
      const response = await axios.post('/api/v1/rpa/browser/control/actions/list', {
        page,
        per_page: pageSize,
        filter_type: 'all',
        sort_by: 'updated_at',
        sort_order: 'desc',
      });

      setData(response.data.data.items);
      setPagination({
        current: response.data.data.page,
        pageSize: response.data.data.per_page,
        total: response.data.data.total,
      });
    } catch (error) {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadList();
  }, []);

  // 删除
  const handleDelete = async (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个动作吗？',
      onOk: async () => {
        try {
          await axios.post('/api/v1/rpa/browser/control/actions/delete', { id });
          message.success('删除成功');
          loadList(pagination.current, pagination.pageSize);
        } catch (error) {
          message.error('删除失败');
        }
      },
    });
  };

  // Fork
  const handleFork = async (id: number, name: string) => {
    try {
      await axios.post('/api/v1/rpa/browser/control/actions/fork', {
        id,
        new_name: `${name} (副本)`,
      });
      message.success('Fork 成功');
      loadList(pagination.current, pagination.pageSize);
    } catch (error) {
      message.error('Fork 失败');
    }
  };

  // 表格列定义
  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '步骤数',
      dataIndex: 'steps_count',
      key: 'steps_count',
      width: 100,
    },
    {
      title: '状态',
      key: 'status',
      width: 120,
      render: (_, record: any) => (
        <Space>
          {record.is_enabled ? (
            <Tag color="green">启用</Tag>
          ) : (
            <Tag color="red">禁用</Tag>
          )}
          {record.is_public && <Tag color="blue">公开</Tag>}
          {record.is_verified && <Tag color="gold">已认证</Tag>}
        </Space>
      ),
    },
    {
      title: '点赞/Fork',
      key: 'stats',
      width: 120,
      render: (_, record: any) => (
        <span>
          👍 {record.likes_count} / 🍴 {record.forks_count}
        </span>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, record: any) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => {
              setEditingItem(record);
              setModalVisible(true);
            }}
          >
            编辑
          </Button>
          <Button
            type="link"
            icon={<ForkOutlined />}
            onClick={() => handleFork(record.id, record.name)}
          >
            Fork
          </Button>
          <Button
            type="link"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.id)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditingItem(null);
            setModalVisible(true);
          }}
        >
          创建动作
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        pagination={false}
      />

      <Pagination
        current={pagination.current}
        pageSize={pagination.pageSize}
        total={pagination.total}
        showSizeChanger
        showQuickJumper
        showTotal={(total) => `共 ${total} 条`}
        onChange={(page, pageSize) => loadList(page, pageSize)}
        style={{ marginTop: 16, textAlign: 'right' }}
      />

      {/* 创建/编辑弹窗 */}
      <Modal
        title={editingItem ? '编辑动作' : '创建动作'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          setEditingItem(null);
        }}
        footer={null}
        width={800}
      >
        <CustomActionForm
          initialValues={editingItem}
          onSubmit={() => {
            setModalVisible(false);
            setEditingItem(null);
            loadList(pagination.current, pagination.pageSize);
            message.success(editingItem ? '更新成功' : '创建成功');
          }}
        />
      </Modal>
    </div>
  );
};

export default CustomActionList;
```

---

## ⚠️ 注意事项

### 1️⃣ 参数模板语法

在步骤参数中使用 `{{parameter_name}}` 引用 CustomAction 的输入参数：

```json
{
  "action_id": "input",
  "params": {
    "selector": "#username",
    "text": "{{username}}"  // ← 引用名为 username 的参数
  }
}
```

### 2️⃣ action_id 由系统生成

**不要**在创建时提供 `action_id`，它由系统自动生成：

```typescript
// ❌ 错误
{
  action_id: "my_custom_action",  // 不允许
  name: "我的动作"
}

// ✅ 正确
{
  name: "我的动作"
  // action_id 会自动生成为 "ca_xxx"
}
```

### 3️⃣ 步骤中的动作类型

步骤可以引用：
- **原子动作**：`click`, `input`, `navigate`, `screenshot`, `wait`, `scroll` 等
- **其他 CustomAction**：通过 `ca_xxx` ID 引用

```json
{
  "steps": [
    {"action_id": "navigate", "params": {...}},  // 原子动作
    {"action_id": "ca_a1b2c3d4e5f6", "params": {...}}  // CustomAction
  ]
}
```

### 4️⃣ 公开与私有

- `is_public: false` - 仅自己可见
- `is_public: true` - 所有用户可见，可以被 Fork

### 5️⃣ 错误处理

```typescript
try {
  await axios.post('/api/v1/rpa/browser/control/actions/create', data);
  message.success('创建成功');
} catch (error) {
  if (error.response?.status === 400) {
    message.error('请求参数错误：' + error.response.data.message);
  } else if (error.response?.status === 409) {
    message.error('名称已存在，请使用其他名称');
  } else {
    message.error('创建失败，请稍后重试');
  }
}
```

---

## 📚 相关文档

- [后端 API 文档](./API_DOCUMENTATION.md)
- [分页接口迁移指南](./FRONTEND_PAGINATION_MIGRATION_GUIDE.md)
- [CustomAction 架构说明](./CUSTOM_ACTION_ARCHITECTURE.md)

---

**最后更新**: 2026-05-13  
**维护者**: RPA-Browser 团队
