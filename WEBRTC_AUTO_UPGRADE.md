# WebRTC 会话自动升级功能说明

## 问题背景

之前的设计中，如果用户先通过普通接口创建了浏览器会话（`PluginedSessionInfo`），然后尝试使用 WebRTC 功能，会收到错误：

```
ValueError: Existing session is not WebRTC-enabled. 
Please close it first before creating a WebRTC session.
```

这要求用户必须先手动关闭旧会话，再创建 WebRTC-enabled 会话，体验不佳。

## 解决方案

实现了**自动升级**功能：当调用 `LiveService.create_webrtc_enabled_session()` 时，系统会自动处理以下三种情况：

### 场景 1：会话不存在
```python
# 直接创建新的 WebRTC-enabled 会话
webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
```
**结果**：✅ 创建新的 `WebRTCEnabledSession`

### 场景 2：会话已存在且是 WebRTC-enabled
```python
# 复用现有会话
webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
```
**结果**：✅ 直接返回现有的 `WebRTCEnabledSession`

### 场景 3：会话已存在但不是 WebRTC-enabled（自动升级）
```python
# 假设之前已经创建了普通会话
await LiveService.create_browser_session(mid, browser_id, ...)

# 现在尝试使用 WebRTC 功能
webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
```
**结果**：✅ 自动执行以下步骤：
1. 记录警告日志
2. 获取会话锁（防止并发）
3. 双重检查会话状态
4. 关闭旧的普通会话
5. 从字典中删除旧会话
6. 清理会话锁
7. 创建新的 `WebRTCEnabledSession`

## 技术实现

### 核心代码逻辑

```python
@staticmethod
async def create_webrtc_enabled_session(mid: int, browser_id: int, headless: bool = False):
    session_key = LiveService._get_session_key(mid, browser_id)
    
    if session_key in LiveService.browser_sessions:
        entry = LiveService.browser_sessions[session_key]
        
        # 场景 2：已经是 WebRTC-enabled
        if isinstance(entry.plugined_session, WebRTCEnabledSession):
            return entry.plugined_session
        
        # 场景 3：需要升级
        logger.warning(f"会话 {session_key} 需要升级为 WebRTC-enabled")
        
        # 获取锁防止并发
        lock = await LiveService._get_session_lock(session_key)
        async with lock:
            # 双重检查
            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                
                # 再次检查是否已被其他请求升级
                if isinstance(entry.plugined_session, WebRTCEnabledSession):
                    return entry.plugined_session
                
                # 关闭旧会话
                await entry.plugined_session.close()
                
                # 删除旧会话
                del LiveService.browser_sessions[session_key]
                await LiveService._cleanup_session_lock(session_key)
    
    # 场景 1 & 3：创建新的 WebRTC-enabled 会话
    webrtc_session = await WebRTCEnabledSession.new(mid, browser_id, headless)
    
    entry = BrowserSessionEntry(
        mid=mid,
        browser_id=browser_id,
        plugined_session=webrtc_session
    )
    LiveService.browser_sessions[session_key] = entry
    
    return webrtc_session
```

### 关键特性

1. **线程安全**
   - 使用会话级别的锁防止并发操作
   - 双重检查模式确保一致性

2. **资源清理**
   - 自动关闭旧会话释放资源
   - 清理会话锁避免内存泄漏

3. **用户透明**
   - 无需手动关闭旧会话
   - API 调用方式不变

4. **日志记录**
   - 警告日志提示会话升级
   - 便于调试和监控

## 使用示例

### WebRTC Router 中的使用

```python
@router.post(BrowserControlRouterPath.webrtc_offer)
async def create_webrtc_offer(req: WebRTCOfferRequest, ...):
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    # 无论之前是否有会话，这里都能正常工作
    webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
    
    offer_data = await webrtc_session.get_webrtc_offer(req.page_index)
    return success_response(data=offer_data)
```

### 客户端调用流程

```javascript
// 客户端无需关心会话类型，直接调用 WebRTC 接口
const response = await fetch('/api/v1/rpa/browser/control/webrtc/offer', {
    method: 'POST',
    headers: {
        'Authorization': 'Bearer token',
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        browser_id: 123,
        page_index: 0
    })
});

// 服务器端会自动处理会话升级
const { sdp, type, stream_key } = await response.json();
```

## 注意事项

### ⚠️ 会话状态丢失

当自动升级发生时，旧会话的状态会丢失：
- 页面导航历史
- Cookie 和 LocalStorage
- JavaScript 变量状态

**建议**：如果需要保持会话状态，应该在创建会话时就指定为 WebRTC-enabled。

### 🔒 并发安全

虽然使用了锁机制，但在高并发场景下仍需要注意：
- 多个请求同时尝试升级同一会话
- 锁等待时间可能影响响应速度

**当前实现**：已通过双重检查和会话锁保证安全性。

### 📝 日志监控

建议监控以下日志：
```
WARNING: 会话 {session_key} 已存在但不是 WebRTC-enabled，将关闭旧会话并创建新的 WebRTC-enabled 会话
INFO: 已关闭旧的非 WebRTC 会话: {session_key}
INFO: WebRTC-enabled 会话已创建: {session_key}
```

频繁的升级日志可能表明客户端调用顺序有问题。

## 最佳实践

### ✅ 推荐做法

1. **明确会话用途**
   ```python
   # 如果确定需要 WebRTC 功能，直接创建 WebRTC-enabled 会话
   webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
   ```

2. **避免混合使用**
   ```python
   # ❌ 不推荐：先创建普通会话，再使用 WebRTC
   await LiveService.create_browser_session(mid, browser_id, ...)
   webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
   
   # ✅ 推荐：直接创建 WebRTC-enabled 会话
   webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)
   ```

3. **合理使用自动升级**
   - 自动升级是容错机制，不是正常流程
   - 应该在开发和测试阶段发现并修复调用顺序问题

### ❌ 避免的做法

1. **频繁升级**
   - 如果在生产环境中看到大量升级日志，说明架构设计有问题

2. **依赖自动升级**
   - 不应该将自动升级作为正常业务流程的一部分

## 总结

自动升级功能提供了更好的用户体验和容错能力：

- ✅ **向后兼容** - 不影响现有代码
- ✅ **用户友好** - 无需手动管理会话类型
- ✅ **线程安全** - 使用锁和双重检查
- ✅ **资源安全** - 自动清理旧会话
- ⚠️ **性能考虑** - 升级过程有少量开销（关闭+创建）

这是一个**防御性编程**的实践，让系统更加健壮和用户友好。
