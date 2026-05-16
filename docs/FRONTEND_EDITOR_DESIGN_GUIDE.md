# Plugin、Workflow、CustomAction 前端界面开发指南

## 📋 概述

本文档详细说明前端如何设计并实现 **Plugin（插件）**、**Workflow（工作流）** 和 **CustomAction（自定义动作）** 的创建和编辑界面。

---

## 🎯 核心概念回顾

### 1. CustomAction（自定义动作）
- **定位**：可复用的"函数"，包含多个步骤序列
- **特点**：线性执行、有明确输入输出参数、可被 Workflow 调用
- **关键字段**：`name`, `parameters_schema`, `steps`

### 2. Workflow（工作流）
- **定位**：完整的"程序"，支持复杂控制流
- **特点**：节点图结构、支持循环/条件分支、可调用 CustomAction
- **关键字段**：`name`, `nodes`, `edges`, `variables`

### 3. Plugin（插件）
- **定位**：生命周期钩子处理器
- **特点**：在特定事件触发时自动执行、按优先级排序
- **关键字段**：`name`, `hook_type`, `priority`, `code`

---

## 🔧 API 接口总览

### CustomAction API

| 接口 | 方法 | 用途 |
|------|------|------|
| `/actions/create` | POST | 创建 CustomAction |
| `/actions/update` | POST | 更新 CustomAction |
| `/actions/list` | POST | 获取列表（分页） |
| `/actions/get` | POST | 获取详情 |
| `/actions/delete` | POST | 删除 |

### Workflow API

| 接口 | 方法 | 用途 |
|------|------|------|
| `/workflows/create` | POST | 创建 Workflow |
| `/workflows/update` | POST | 更新 Workflow |
| `/workflows/list` | POST | 获取列表（分页） |
| `/workflows/get` | POST | 获取详情 |
| `/workflows/delete` | POST | 删除 |

### Plugin API

| 接口 | 方法 | 用途 |
|------|------|------|
| `/plugins/create` | POST | 创建 Plugin |
| `/plugins/update` | POST | 更新 Plugin |
| `/plugins/list` | POST | 获取列表（分页） |
| `/plugins/get` | POST | 获取详情 |
| `/plugins/delete` | POST | 删除 |

---

## 🎨 CustomAction 编辑器设计

### 1️⃣ 界面布局

```
┌─────────────────────────────────────────────────────────┐
│  CustomAction 编辑器                                      │
├─────────────────────────────────────────────────────────┤
│  基本信息                                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 名称: [B站登录________________]                     │  │
│  │ 描述: [用于自动登录B站账号______]                   │  │
│  │ 版本: [1.0.0___________]                           │  │
│  │ 标签: [登录, B站, 自动化____] [+添加]               │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  参数定义 (parameters_schema)                            │
│  ┌───────────────────────────────────────────────────┐  │
│  │ + 添加参数                                          │  │
│  │                                                     │  │
│  │ ┌───────────────────────────────────────────────┐ │  │
│  │ │ 参数名: username    类型: string   [✓] 必填    │ │  │
│  │ │ 默认值: ___________ 描述: B站用户名             │ │  │
│  │ │ [删除]                                         │ │  │
│  │ └───────────────────────────────────────────────┘ │  │
│  │                                                     │  │
│  │ ┌───────────────────────────────────────────────┐ │  │
│  │ │ 参数名: password    类型: string   [✓] 必填    │ │  │
│  │ │ 默认值: ___________ 描述: B站密码               │ │  │
│  │ │ [删除]                                         │ │  │
│  │ └───────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  步骤列表 (steps)                                        │
│  ┌───────────────────────────────────────────────────┐  │
│  │ + 添加步骤                                          │  │
│  │                                                     │  │
│  │ ┌───────────────────────────────────────────────┐ │  │
│  │ │ 1. Navigate                                    │ │  │
│  │ │    URL: https://passport.bilibili.com/login    │ │  │
│  │ │    [编辑] [上移] [下移] [删除]                  │ │  │
│  │ └───────────────────────────────────────────────┘ │  │
│  │                                                     │  │
│  │ ┌───────────────────────────────────────────────┐ │  │
│  │ │ 2. Input                                       │ │  │
│  │ │    选择器: #login-username                     │ │  │
│  │ │    值: {{username}}  ← 模板变量                │ │  │
│  │ │    [编辑] [上移] [下移] [删除]                  │ │  │
│  │ └───────────────────────────────────────────────┘ │  │
│  │                                                     │  │
│  │ ┌───────────────────────────────────────────────┐ │  │
│  │ │ 3. Input                                       │ │  │
│  │ │    选择器: #login-passwd                       │ │  │
│  │ │    值: {{password}}                            │ │  │
│  │ │    [编辑] [上移] [下移] [删除]                  │ │  │
│  │ └───────────────────────────────────────────────┘ │  │
│  │                                                     │  │
│  │ ┌───────────────────────────────────────────────┐ │  │
│  │ │ 4. Click                                       │ │  │
│  │ │    选择器: .btn-login                          │ │  │
│  │ │    [编辑] [上移] [下移] [删除]                  │ │  │
│  │ └───────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  [保存] [取消] [测试执行]                                │
└─────────────────────────────────────────────────────────┘
```

---

### 2️⃣ React 组件实现

```tsx
// components/CustomActionEditor.tsx
import React, { useState } from 'react';
import { Form, Input, Button, Select, Card, Space } from 'antd';
import { PlusOutlined, DeleteOutlined, UpOutlined, DownOutlined } from '@ant-design/icons';

interface ParameterField {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'object';
  required: boolean;
  default_value?: any;
  description?: string;
}

interface Step {
  action: string;
  params: Record<string, any>;
}

interface CustomActionData {
  name: string;
  description: string;
  version: string;
  tags: string[];
  parameters_schema: ParameterField[];
  steps: Step[];
}

const ACTION_TYPES = [
  'navigate', 'click', 'input', 'scroll', 'wait', 
  'screenshot', 'evaluate', 'hover', 'select', 'keyboard'
];

const CustomActionEditor: React.FC<{
  initialData?: CustomActionData;
  onSave: (data: CustomActionData) => Promise<void>;
  onCancel: () => void;
}> = ({ initialData, onSave, onCancel }) => {
  const [form] = Form.useForm();
  const [parameters, setParameters] = useState<ParameterField[]>(
    initialData?.parameters_schema || []
  );
  const [steps, setSteps] = useState<Step[]>(
    initialData?.steps || []
  );

  // 添加参数
  const addParameter = () => {
    setParameters([
      ...parameters,
      { name: '', type: 'string', required: true }
    ]);
  };

  // 删除参数
  const removeParameter = (index: number) => {
    setParameters(parameters.filter((_, i) => i !== index));
  };

  // 更新参数
  const updateParameter = (index: number, field: keyof ParameterField, value: any) => {
    const newParams = [...parameters];
    newParams[index] = { ...newParams[index], [field]: value };
    setParameters(newParams);
  };

  // 添加步骤
  const addStep = () => {
    setSteps([
      ...steps,
      { action: 'navigate', params: {} }
    ]);
  };

  // 删除步骤
  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index));
  };

  // 移动步骤
  const moveStep = (index: number, direction: 'up' | 'down') => {
    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= steps.length) return;
    
    const newSteps = [...steps];
    [newSteps[index], newSteps[newIndex]] = [newSteps[newIndex], newSteps[index]];
    setSteps(newSteps);
  };

  // 更新步骤
  const updateStep = (index: number, field: keyof Step, value: any) => {
    const newSteps = [...steps];
    newSteps[index] = { ...newSteps[index], [field]: value };
    setSteps(newSteps);
  };

  // 更新步骤参数
  const updateStepParam = (index: number, paramKey: string, value: any) => {
    const newSteps = [...steps];
    newSteps[index] = {
      ...newSteps[index],
      params: { ...newSteps[index].params, [paramKey]: value }
    };
    setSteps(newSteps);
  };

  // 保存
  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const data: CustomActionData = {
        ...values,
        parameters_schema: parameters,
        steps: steps
      };
      await onSave(data);
    } catch (error) {
      console.error('Validation failed:', error);
    }
  };

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <h2>{initialData ? '编辑 CustomAction' : '创建 CustomAction'}</h2>
      
      <Form form={form} layout="vertical" initialValues={initialData}>
        {/* 基本信息 */}
        <Card title="基本信息" style={{ marginBottom: 16 }}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="例如：B站登录" />
          </Form.Item>
          
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="描述这个动作的功能" />
          </Form.Item>
          
          <Form.Item name="version" label="版本">
            <Input placeholder="1.0.0" />
          </Form.Item>
          
          <Form.Item name="tags" label="标签">
            <Select mode="tags" placeholder="添加标签" />
          </Form.Item>
        </Card>

        {/* 参数定义 */}
        <Card title="参数定义" extra={<Button icon={<PlusOutlined />} onClick={addParameter}>添加参数</Button>}>
          {parameters.map((param, index) => (
            <Card key={index} size="small" style={{ marginBottom: 8 }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space>
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
                    <Select.Option value="string">string</Select.Option>
                    <Select.Option value="number">number</Select.Option>
                    <Select.Option value="boolean">boolean</Select.Option>
                    <Select.Option value="object">object</Select.Option>
                  </Select>
                  <Form.Item
                    name={['parameters_schema', index, 'required']}
                    valuePropName="checked"
                    noStyle
                  >
                    <input
                      type="checkbox"
                      checked={param.required}
                      onChange={(e) => updateParameter(index, 'required', e.target.checked)}
                    />
                  </Form.Item>
                  <span>必填</span>
                  <Button
                    icon={<DeleteOutlined />}
                    danger
                    onClick={() => removeParameter(index)}
                  />
                </Space>
                
                <Input
                  placeholder="默认值（可选）"
                  value={param.default_value}
                  onChange={(e) => updateParameter(index, 'default_value', e.target.value)}
                />
                
                <Input
                  placeholder="参数描述"
                  value={param.description}
                  onChange={(e) => updateParameter(index, 'description', e.target.value)}
                />
              </Space>
            </Card>
          ))}
          
          {parameters.length === 0 && (
            <div style={{ textAlign: 'center', color: '#999', padding: 20 }}>
              暂无参数，点击"添加参数"开始定义
            </div>
          )}
        </Card>

        {/* 步骤列表 */}
        <Card title="步骤列表" extra={<Button icon={<PlusOutlined />} onClick={addStep}>添加步骤</Button>}>
          {steps.map((step, index) => (
            <Card
              key={index}
              size="small"
              style={{ marginBottom: 8 }}
              title={`步骤 ${index + 1}: ${step.action}`}
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<UpOutlined />}
                    disabled={index === 0}
                    onClick={() => moveStep(index, 'up')}
                  />
                  <Button
                    size="small"
                    icon={<DownOutlined />}
                    disabled={index === steps.length - 1}
                    onClick={() => moveStep(index, 'down')}
                  />
                  <Button
                    size="small"
                    icon={<DeleteOutlined />}
                    danger
                    onClick={() => removeStep(index)}
                  />
                </Space>
              }
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select
                  value={step.action}
                  onChange={(value) => updateStep(index, 'action', value)}
                  style={{ width: '100%' }}
                  placeholder="选择动作类型"
                >
                  {ACTION_TYPES.map(type => (
                    <Select.Option key={type} value={type}>{type}</Select.Option>
                  ))}
                </Select>
                
                {/* 根据动作类型显示不同的参数输入框 */}
                {step.action === 'navigate' && (
                  <Input
                    placeholder="URL"
                    value={step.params.url}
                    onChange={(e) => updateStepParam(index, 'url', e.target.value)}
                  />
                )}
                
                {(step.action === 'click' || step.action === 'input' || step.action === 'hover') && (
                  <>
                    <Input
                      placeholder="CSS 选择器"
                      value={step.params.selector}
                      onChange={(e) => updateStepParam(index, 'selector', e.target.value)}
                    />
                    {step.action === 'input' && (
                      <Input
                        placeholder="输入值（支持 {{variable}} 模板）"
                        value={step.params.value}
                        onChange={(e) => updateStepParam(index, 'value', e.target.value)}
                      />
                    )}
                  </>
                )}
                
                {step.action === 'wait' && (
                  <Input
                    type="number"
                    placeholder="等待时间（毫秒）"
                    value={step.params.time}
                    onChange={(e) => updateStepParam(index, 'time', parseInt(e.target.value))}
                  />
                )}
                
                {step.action === 'scroll' && (
                  <Space>
                    <Select
                      value={step.params.direction}
                      onChange={(value) => updateStepParam(index, 'direction', value)}
                      style={{ width: 120 }}
                    >
                      <Select.Option value="up">向上</Select.Option>
                      <Select.Option value="down">向下</Select.Option>
                    </Select>
                    <Input
                      type="number"
                      placeholder="滚动距离"
                      value={step.params.amount}
                      onChange={(e) => updateStepParam(index, 'amount', parseInt(e.target.value))}
                      style={{ width: 150 }}
                    />
                  </Space>
                )}
              </Space>
            </Card>
          ))}
          
          {steps.length === 0 && (
            <div style={{ textAlign: 'center', color: '#999', padding: 20 }}>
              暂无步骤，点击"添加步骤"开始编排
            </div>
          )}
        </Card>

        {/* 操作按钮 */}
        <Space style={{ marginTop: 16 }}>
          <Button type="primary" onClick={handleSave}>保存</Button>
          <Button onClick={onCancel}>取消</Button>
          <Button onClick={() => console.log('Test execution')}>测试执行</Button>
        </Space>
      </Form>
    </div>
  );
};

export default CustomActionEditor;
```

---

## 🎨 Workflow 编辑器设计

### 1️⃣ 界面布局（可视化流程图编辑器）

推荐使用 **React Flow** 或 **X6** 等流程图库。

```
┌─────────────────────────────────────────────────────────┐
│  Workflow 编辑器                                          │
├─────────────────────────────────────────────────────────┤
│  工具栏                                                  │
│  [保存] [运行] [缩放: 100%] [适配视图]                    │
├──────────────┬──────────────────────────────────────────┤
│  组件面板     │  画布区域                                 │
│              │                                          │
│  ┌────────┐  │  ┌──────────┐                           │
│  │ Navigate│  │  │ Start    │────┐                     │
│  └────────┘  │  └──────────┘    │                     │
│  ┌────────┐  │                  ▼                     │
│  │ Click   │  │          ┌──────────────┐             │
│  └────────┘  │          │ B站登录       │             │
│  ┌────────┐  │          │ (CustomAction)│             │
│  │ Input   │  │          └──────────────┘             │
│  └────────┘  │                  │                     │
│  ┌────────┐  │                  ▼                     │
│  │ Loop    │  │          ┌──────────────┐             │
│  └────────┘  │          │ 搜索视频      │             │
│  ┌────────┐  │          └──────────────┘             │
│  │ If-Else │  │                  │                     │
│  └────────┘  │                  ▼                     │
│  ┌────────┐  │          ┌──────────────┐             │
│  │Custom  │  │          │ 保存数据      │             │
│  │ Action │  │          └──────────────┘             │
│  └────────┘  │                  │                     │
│              │                  ▼                     │
│              │          ┌──────────┐                 │
│              │          │ End      │                 │
│              │          └──────────┘                 │
│              │                                          │
└──────────────┴──────────────────────────────────────────┘
```

---

### 2️⃣ React Flow 实现示例

```tsx
// components/WorkflowEditor.tsx
import React, { useState, useCallback } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';

interface WorkflowNodeData {
  type: 'action' | 'custom_action' | 'loop' | 'if_else';
  action_id?: string;
  params?: Record<string, any>;
  condition?: string;
  loop_count?: number;
  label: string;
}

const nodeTypes = {
  action: ({ data }: { data: WorkflowNodeData }) => (
    <div style={{
      padding: 10,
      background: '#fff',
      border: '1px solid #ddd',
      borderRadius: 5,
      minWidth: 150
    }}>
      <strong>{data.label}</strong>
      <div style={{ fontSize: 12, color: '#666' }}>
        {data.action_id}
      </div>
    </div>
  ),
  custom_action: ({ data }: { data: WorkflowNodeData }) => (
    <div style={{
      padding: 10,
      background: '#e6f7ff',
      border: '1px solid #91d5ff',
      borderRadius: 5,
      minWidth: 150
    }}>
      <strong>📦 {data.label}</strong>
      <div style={{ fontSize: 12, color: '#666' }}>
        CustomAction
      </div>
    </div>
  ),
  loop: ({ data }: { data: WorkflowNodeData }) => (
    <div style={{
      padding: 10,
      background: '#fff7e6',
      border: '1px solid #ffd591',
      borderRadius: 5,
      minWidth: 150
    }}>
      <strong>🔄 {data.label}</strong>
      <div style={{ fontSize: 12, color: '#666' }}>
        循环 {data.loop_count} 次
      </div>
    </div>
  ),
};

const initialNodes: Node<WorkflowNodeData>[] = [
  {
    id: '1',
    type: 'action',
    position: { x: 250, y: 5 },
    data: { label: 'Start', type: 'action' },
  },
];

const WorkflowEditor: React.FC<{
  initialData?: any;
  onSave: (data: any) => Promise<void>;
  onCancel: () => void;
}> = ({ initialData, onSave, onCancel }) => {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [workflowName, setWorkflowName] = useState(initialData?.name || '');

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({
      ...params,
      markerEnd: { type: MarkerType.ArrowClosed },
    }, eds)),
    [setEdges]
  );

  // 添加节点
  const addNode = (type: WorkflowNodeData['type']) => {
    const newNode: Node<WorkflowNodeData> = {
      id: `${Date.now()}`,
      type,
      position: { x: Math.random() * 500, y: Math.random() * 500 },
      data: {
        label: type === 'custom_action' ? 'CustomAction' : type,
        type,
        action_id: type === 'custom_action' ? '' : undefined,
      },
    };
    setNodes((nds) => nds.concat(newNode));
  };

  // 保存
  const handleSave = async () => {
    const workflowData = {
      name: workflowName,
      nodes: nodes.map(node => ({
        id: node.id,
        type: node.data.type,
        action_id: node.data.action_id,
        params: node.data.params,
        position: node.position,
      })),
      edges: edges.map(edge => ({
        source: edge.source,
        target: edge.target,
      })),
    };
    
    await onSave(workflowData);
  };

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* 工具栏 */}
      <div style={{
        padding: '10px 20px',
        background: '#f5f5f5',
        borderBottom: '1px solid #ddd',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center'
      }}>
        <div>
          <input
            type="text"
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
            placeholder="Workflow 名称"
            style={{ marginRight: 10, padding: '5px 10px' }}
          />
          <button onClick={() => addNode('action')}>+ Action</button>
          <button onClick={() => addNode('custom_action')} style={{ marginLeft: 5 }}>
            + CustomAction
          </button>
          <button onClick={() => addNode('loop')} style={{ marginLeft: 5 }}>
            + Loop
          </button>
        </div>
        
        <div>
          <button onClick={handleSave} style={{ marginRight: 5 }}>保存</button>
          <button onClick={onCancel}>取消</button>
        </div>
      </div>

      {/* 画布 */}
      <div style={{ flex: 1 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          fitView
        >
          <Controls />
          <Background />
        </ReactFlow>
      </div>
    </div>
  );
};

export default WorkflowEditor;
```

---

## 🎨 Plugin 编辑器设计

### 1️⃣ 界面布局

```
┌─────────────────────────────────────────────────────────┐
│  Plugin 编辑器                                            │
├─────────────────────────────────────────────────────────┤
│  基本信息                                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 名称: [日志记录插件_____________]                   │  │
│  │ 描述: [在动作执行前后记录日志____]                  │  │
│  │ 版本: [1.0.0__________________]                    │  │
│  │ 启用: [✓]                                          │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  钩子配置                                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 钩子类型: [before_action▼]                         │  │
│  │   • before_action  (动作执行前)                    │  │
│  │   • after_action   (动作执行后)                    │  │
│  │   • on_success     (成功时)                        │  │
│  │   • on_error       (失败时)                        │  │
│  │   • on_timeout     (超时时)                        │  │
│  │                                                    │  │
│  │ 优先级: [10____] (数值越小越先执行)                 │  │
│  │                                                    │  │
│  │ 适用对象:                                          │  │
│  │   [ ] 所有 Workflow                               │  │
│  │   [ ] 所有 CustomAction                           │  │
│  │   [✓] 指定对象                                     │  │
│  │     - Workflow: [视频采集____] [+添加]             │  │
│  │     - CustomAction: [B站登录__] [+添加]            │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  插件代码 (JavaScript)                                   │
│  ┌───────────────────────────────────────────────────┐  │
│  │ // 在动作执行前记录日志                             │  │
│  │ async function beforeAction(context) {             │  │
│  │   console.log('即将执行:', context.action_id);     │  │
│  │   console.log('参数:', context.params);            │  │
│  │                                                    │  │
│  │   // 可以修改参数                                   │  │
│  │   // context.params.timeout = 5000;                │  │
│  │ }                                                  │  │
│  │                                                    │  │
│  │ [语法检查] [格式化代码]                             │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  [保存] [取消] [测试执行]                                │
└─────────────────────────────────────────────────────────┘
```

---

### 2️⃣ React 组件实现

```tsx
// components/PluginEditor.tsx
import React, { useState } from 'react';
import { Form, Input, Button, Select, Checkbox, Card, Space } from 'antd';
import Editor from '@monaco-editor/react';

const HOOK_TYPES = [
  { value: 'before_action', label: 'before_action (动作执行前)' },
  { value: 'after_action', label: 'after_action (动作执行后)' },
  { value: 'on_success', label: 'on_success (成功时)' },
  { value: 'on_error', label: 'on_error (失败时)' },
  { value: 'on_timeout', label: 'on_timeout (超时时)' },
];

interface PluginData {
  name: string;
  description: string;
  version: string;
  is_enabled: boolean;
  hook_type: string;
  priority: number;
  apply_to_all_workflows: boolean;
  apply_to_all_actions: boolean;
  target_workflow_ids: string[];
  target_action_ids: string[];
  code: string;
}

const PluginEditor: React.FC<{
  initialData?: PluginData;
  onSave: (data: PluginData) => Promise<void>;
  onCancel: () => void;
}> = ({ initialData, onSave, onCancel }) => {
  const [form] = Form.useForm();
  const [code, setCode] = useState(initialData?.code || getDefaultCode(initialData?.hook_type));
  const [hookType, setHookType] = useState(initialData?.hook_type || 'before_action');

  // 根据钩子类型生成默认代码
  function getDefaultCode(hookType?: string): string {
    const templates: Record<string, string> = {
      before_action: `// 在动作执行前执行
async function beforeAction(context) {
  console.log('即将执行动作:', context.action_id);
  console.log('参数:', context.params);
  
  // 可以在这里修改参数
  // context.params.timeout = 5000;
}`,
      after_action: `// 在动作执行后执行
async function afterAction(context, result) {
  console.log('动作执行完成:', context.action_id);
  console.log('结果:', result);
}`,
      on_success: `// 动作成功时执行
async function onSuccess(context, result) {
  console.log('动作执行成功:', context.action_id);
}`,
      on_error: `// 动作失败时执行
async function onError(context, error) {
  console.error('动作执行失败:', context.action_id);
  console.error('错误信息:', error.message);
  
  // 可以进行重试或其他处理
}`,
      on_timeout: `// 动作超时时执行
async function onTimeout(context) {
  console.warn('动作执行超时:', context.action_id);
}`,
    };
    
    return templates[hookType || 'before_action'] || templates.before_action;
  }

  // 钩子类型变化时更新代码模板
  const handleHookTypeChange = (value: string) => {
    setHookType(value);
    if (!initialData?.code) {
      setCode(getDefaultCode(value));
    }
  };

  // 保存
  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const data: PluginData = {
        ...values,
        code,
      };
      await onSave(data);
    } catch (error) {
      console.error('Validation failed:', error);
    }
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <h2>{initialData ? '编辑 Plugin' : '创建 Plugin'}</h2>
      
      <Form form={form} layout="vertical" initialValues={initialData}>
        {/* 基本信息 */}
        <Card title="基本信息" style={{ marginBottom: 16 }}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="例如：日志记录插件" />
          </Form.Item>
          
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="描述这个插件的功能" />
          </Form.Item>
          
          <Form.Item name="version" label="版本">
            <Input placeholder="1.0.0" />
          </Form.Item>
          
          <Form.Item
            name="is_enabled"
            valuePropName="checked"
          >
            <Checkbox>启用此插件</Checkbox>
          </Form.Item>
        </Card>

        {/* 钩子配置 */}
        <Card title="钩子配置" style={{ marginBottom: 16 }}>
          <Form.Item
            name="hook_type"
            label="钩子类型"
            rules={[{ required: true, message: '请选择钩子类型' }]}
          >
            <Select
              options={HOOK_TYPES}
              onChange={handleHookTypeChange}
            />
          </Form.Item>
          
          <Form.Item
            name="priority"
            label="优先级"
            initialValue={10}
            tooltip="数值越小越先执行"
          >
            <Input type="number" min={1} max={100} />
          </Form.Item>
          
          <Form.Item label="适用对象">
            <Space direction="vertical">
              <Form.Item
                name="apply_to_all_workflows"
                valuePropName="checked"
                noStyle
              >
                <Checkbox>应用到所有 Workflow</Checkbox>
              </Form.Item>
              
              <Form.Item
                name="apply_to_all_actions"
                valuePropName="checked"
                noStyle
              >
                <Checkbox>应用到所有 CustomAction</Checkbox>
              </Form.Item>
              
              {/* TODO: 添加指定对象选择器 */}
              <div>指定对象选择器（待实现）</div>
            </Space>
          </Form.Item>
        </Card>

        {/* 插件代码 */}
        <Card title="插件代码 (JavaScript)" style={{ marginBottom: 16 }}>
          <div style={{ border: '1px solid #d9d9d9', borderRadius: 4 }}>
            <Editor
              height="400px"
              defaultLanguage="javascript"
              value={code}
              onChange={(value) => setCode(value || '')}
              theme="vs-dark"
              options={{
                minimap: { enabled: false },
                fontSize: 14,
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
              }}
            />
          </div>
          
          <Space style={{ marginTop: 8 }}>
            <Button onClick={() => console.log('Syntax check')}>语法检查</Button>
            <Button onClick={() => console.log('Format code')}>格式化代码</Button>
          </Space>
        </Card>

        {/* 操作按钮 */}
        <Space>
          <Button type="primary" onClick={handleSave}>保存</Button>
          <Button onClick={onCancel}>取消</Button>
          <Button onClick={() => console.log('Test execution')}>测试执行</Button>
        </Space>
      </Form>
    </div>
  );
};

export default PluginEditor;
```

---

## 📊 数据结构对比

### CustomAction 数据结构

```typescript
interface CustomAction {
  id: number;
  action_id: string;           // 系统生成，格式：ca_xxx
  name: string;                // 用户定义的显示名称
  version: string;             // 版本号
  action_type: 'composite';    // 固定为 composite
  description: string;
  parameters_schema: ParameterField[];  // 参数定义
  steps: Step[];               // 步骤序列
  tags: string[];
  user_data?: Record<string, any>;
  is_public: boolean;
  mid: string;                 // 用户ID
  created_at: Date;
  updated_at: Date;
}

interface ParameterField {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'object';
  required: boolean;
  default_value?: any;
  description?: string;
}

interface Step {
  action: string;              // 原子动作类型
  params: Record<string, any>; // 动作参数
}
```

### Workflow 数据结构

```typescript
interface Workflow {
  id: number;
  workflow_id: string;         // 系统生成，格式：wf_xxx
  name: string;
  version: string;
  description: string;
  nodes: WorkflowNode[];       // 节点列表
  edges: WorkflowEdge[];       // 边列表
  variables?: Record<string, any>;  // 全局变量
  enabled_plugins?: string[];  // 引用的插件ID列表
  tags: string[];
  is_public: boolean;
  mid: string;
  created_at: Date;
  updated_at: Date;
}

interface WorkflowNode {
  id: string;
  type: 'action' | 'custom_action' | 'loop' | 'if_else';
  action_id?: string;          // 如果是 custom_action
  params?: Record<string, any>;
  condition?: string;          // 条件表达式
  loop_count?: number;         // 循环次数
  loop_while?: string;         // while 条件
  children?: WorkflowNode[];   // 子节点（嵌套）
  position?: { x: number; y: number };  // 画布位置
}

interface WorkflowEdge {
  source: string;
  target: string;
}
```

### Plugin 数据结构

```typescript
interface Plugin {
  id: number;
  plugin_id: string;           // 系统生成，格式：pl_xxx
  name: string;
  version: string;
  description: string;
  hook_type: 'before_action' | 'after_action' | 'on_success' | 'on_error' | 'on_timeout';
  priority: number;            // 执行优先级
  code: string;                // JavaScript 代码
  is_enabled: boolean;
  apply_to_all_workflows: boolean;
  apply_to_all_actions: boolean;
  target_workflow_ids: string[];  // 指定的 Workflow ID
  target_action_ids: string[];    // 指定的 CustomAction ID
  mid: string;
  created_at: Date;
  updated_at: Date;
}
```

---

## 💡 最佳实践建议

### 1. CustomAction 编辑器

✅ **推荐功能**：
- 参数定义时提供实时预览（显示如何在步骤中使用 `{{variable}}`）
- 步骤拖拽排序（比上下移动按钮更直观）
- 步骤模板库（常用操作序列一键添加）
- 测试执行功能（实时看到每一步的执行结果）

❌ **避免**：
- 不要允许在 CustomAction 中使用控制流（Loop、IfElse）
- 不要让步骤过于复杂（超过 10 步考虑拆分成多个 CustomAction）

---

### 2. Workflow 编辑器

✅ **推荐功能**：
- 可视化流程图（React Flow / X6）
- 节点属性面板（点击节点编辑参数）
- 撤销/重做功能
- 导入/导出 JSON
- 执行模拟（高亮当前执行的节点）
- 变量监视器（实时显示 state.variables）

❌ **避免**：
- 不要允许过深的嵌套（建议最多 3 层）
- 不要在画布上直接编辑复杂代码（使用弹窗或侧边栏）

---

### 3. Plugin 编辑器

✅ **推荐功能**：
- Monaco Editor（代码高亮、智能提示）
- 代码模板（根据钩子类型自动生成）
- 语法检查（eslint）
- 沙箱执行环境（防止恶意代码）
- 上下文对象文档（悬浮提示可用的 API）

❌ **避免**：
- 不要允许访问敏感 API（文件系统、网络请求等）
- 不要允许同步阻塞操作（必须异步）

---

## 🚀 快速开始建议

### 第一阶段：基础版

1. **CustomAction**：表单式编辑器（非可视化）
2. **Workflow**：JSON 编辑器（手动编写）
3. **Plugin**：文本编辑器 + 基础语法高亮

### 第二阶段：增强版

1. **CustomAction**：添加步骤拖拽排序
2. **Workflow**：集成 React Flow 可视化编辑器
3. **Plugin**：集成 Monaco Editor

### 第三阶段：专业版

1. **CustomAction**：步骤模板库、测试执行
2. **Workflow**：执行模拟、变量监视、调试工具
3. **Plugin**：沙箱执行、代码片段管理、插件市场

---

## 📚 相关资源

- **React Flow**: https://reactflow.dev/
- **X6 (AntV)**: https://x6.antv.vision/
- **Monaco Editor**: https://microsoft.github.io/monaco-editor/
- **Ant Design**: https://ant.design/

---

## ❓ 常见问题

### Q1: CustomAction 和 Workflow 的编辑器可以合并吗？

**A**: 不建议。它们的抽象层次不同：
- CustomAction 是**线性步骤列表**（简单）
- Workflow 是**节点图结构**（复杂）

合并会导致界面过于复杂，用户体验差。

---

### Q2: 如何实现步骤的参数验证？

**A**: 根据 `action` 类型动态渲染不同的参数输入框，并使用 JSON Schema 验证。

---

### Q3: Workflow 的节点如何支持嵌套？

**A**: 使用树形结构存储 `children` 字段，在画布上用子图或折叠面板展示。

---

### Q4: Plugin 的代码如何安全执行？

**A**: 使用 Web Worker 或 iframe 沙箱，限制可用 API，设置执行超时。

---

希望这份指南能帮助你快速实现这三个核心功能的编辑器！如有问题，欢迎随时提问。🎉
