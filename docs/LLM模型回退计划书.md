# LLM 模型回退计划书（LangChain with_fallbacks）

## 背景

`参与B站抽奖` 工作流 2026-10-23 那次运行：154 次 LLM 调用中 91 次失败（59%），
全部是 OpenRouter 免费额度用尽：

```
Error code: 429 - Rate limit exceeded: free-models-per-day
  metadata: X-RateLimit-Limit=50, Remaining=0,
            limit_source=openrouter_free_tier_daily
```

失败后被 `on_error=continue` 容忍，只能降级为「抄评论 / 固定短评」（77 条评论降级），
质量明显下降。单个模型当天额度耗尽时，**换一个模型就能继续**，因此需要
「主模型 + 备用模型」的回退能力。

## 设计

### 1. 参数（`LLMParams` 新增 `fallbacks`）

```python
class LLMModelConfig(SQLModel):
    model: str
    server_url: str
    api_key: str

class LLMParams(BaseActionParams):
    # 原有：model / server_url / api_key（主模型，保持不变）
    fallbacks: list[LLMModelConfig] = []   # 备用模型，按顺序回退
```

- 动作声明一个输入变量 `llm_fallbacks`（`type: array`，默认 `[]`），步骤参数写成
  `"fallbacks": "{{llm_fallbacks}}"`：这样**工作流编辑页的「输入变量」会渲染成 JSON
  文本域**，用户可以直接粘贴多个模型配置（前端按 `array` 类型走 textarea + JSON 校验）；
- 模板替换后 `fallbacks` 是**字符串**（列表会被 `str()` 成 Python repr），因此参数模型
  增加了 `mode="before"` 校验器：先 `json.loads`，失败再 `ast.literal_eval`（仅字面量），
  两种来源都能解析；
- 主模型三件套**保持原样**，存量动作零改动；
- 备用模型只复用主模型的 `temperature / max_tokens / timeout` 等通用参数，
  各自带自己的 `model / server_url / api_key`（不同供应商 = 不同额度池）。

### 2. 执行（`LLMAction`）

用 LangChain 自带的 `Runnable.with_fallbacks`（不改造成自研池）：

```python
runnables = [主模型] + [备用模型...]          # 结构化输出时先各自 with_structured_output
runnable = runnables[0].with_fallbacks(
    runnables[1:], exceptions_to_handle=(Exception,)
)
result = await runnable.ainvoke(messages)
```

- 主模型抛异常（429 / 401 / 超时 / 5xx / 连接失败）→ 自动按顺序尝试备用模型；
- 全部失败 → 动作返回失败。注意 LangChain `with_fallbacks` 只抛出**第一个**异常、
  会丢掉备用模型的失败原因，因此在回退链里给每个模型包了一层记录
  （`_remember_failure`），上层把「每个模型 + 各自的错误」汇总成一条说明，
  例如 `所有模型均失败（2 个）[gpt-4o-mini: 429 ...; deepseek-chat: 401 ...]`；
- `LLMResult.model` 记录实际应答的模型；`fallback_used` 标记是否用到了备用模型
  （判定口径：应答 `model_name` ≠ 主模型名，属于尽力而为的启发式）。

### 3. 失败后的推送（复用已有 notify）

LLM 步骤仍是 `on_error: continue`，失败后进入 `on_error_branch`，
已有的三条 `notify` 告警会按错误类型推送（401 / 额度耗尽 / 限流），
本次再补一条「非限流类失败」的兜底通知，保证**任何**全池失败都会被推送，
而不是只有特定错误码才通知。

### 4. 与限速器的关系

`InMemoryRateLimiter` **本次不接**：它只平滑 QPS，无法识别 `X-RateLimit-*` 与
`*_daily` 额度（本次 429 就是日额度耗尽），接上也不会减少失败；
需要 QPS 治理时再按「每模型一个单例桶」补参数，属独立需求。

## 兼容性与边界

- `fallbacks` 缺省为空 → 行为与现在完全一致（单模型）；
- API key 仍来自工作流变量（`llm_api_key`）或动作参数，**不写入文档**；
  多 provider 时 key 明文问题会放大，后续可迁移到用户级模型池配置表（另立计划）；
- 回退只在**单次调用内**生效，不做跨调用健康度/冷却（LangChain 的
  `with_fallbacks` 不提供），因此主模型若整天不可用，每次仍会先撞一次再回退；
  如后续确需「跳过已知坏模型」，再补进程内冷却表。

## 自检清单

- [ ] 主模型 + 备用模型都可用时，主模型失败能拿到备用模型结果；
- [ ] 全部失败时动作返回失败，错误为最后一个模型的错误；
- [ ] 失败后 `on_error_branch` 的通知照常触发（含新增的兜底类通知）；
- [ ] `fallbacks` 为空时行为与改造前一致；
- [ ] 单测覆盖：主模型成功、主模型失败→备用成功、全失败；静态校验通过；
- [ ] 动作 JSON 与 DB（源动作 + Fork）一致。
