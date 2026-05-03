# WebRTC 视频流服务 - 面向对象重构完成报告

## 概述

已成功将 WebRTC 视频流服务从函数式实现重构为完整的面向对象架构。新架构通过继承 `PluginedSessionInfo` 创建 `WebRTCEnabledSession`，在浏览器会话层面集成 WebRTC 功能。

## 架构设计

### 核心类层次结构

```
PluginedSessionInfo (现有基类)
    └── WebRTCEnabledSession (新增，扩展 WebRTC 能力)
            ├── WebRTCStreamManager (管理该会话的所有视频流)
            │       └── WebRTCStreamSession (单个页面视频流)
            │               ├── VideoFrameProducer (帧捕获和编码)
            │               └── WebRTCMediaTrack (aiortc 视频轨道)
```

## 已完成的文件

### 新建文件

1. **模型层** (`app/models/runtime/webrtc_models.py`)
   - `WebRTCStreamState`: 视频流状态枚举
   - `WebRTCStreamInfo`: 视频流信息数据类
   - `WebRTCSessionConfig`: WebRTC 会话配置

2. **WebRTC 服务层** (`app/services/RPA_browser/webrtc/`)
   - `__init__.py`: 模块导出
   - `video_frame_producer.py`: VideoFrameProducer 类
     - 使用 Playwright screencast API 捕获帧
     - 通过 `asyncio.to_thread()` 在线程池中解码 JPEG
     - 丢旧保新的帧队列策略 (maxsize=10)
   - `media_track.py`: WebRTCMediaTrack 类
     - 继承 `aiortc.MediaStreamTrack`
     - 提供视频帧给 WebRTC PeerConnection
   - `stream_session.py`: WebRTCStreamSession 类
     - 管理单个页面的完整 WebRTC 生命周期
     - 处理 SDP Offer/Answer、ICE Candidate
     - 监控闲置超时并自动关闭
   - `stream_manager.py`: WebRTCStreamManager 类
     - 管理单个浏览器会话下的所有视频流
     - 按 page_index 索引管理流
     - 定期清理闲置流（每分钟检查一次）

3. **浏览器会话扩展** (`app/services/RPA_browser/browser_session_pool/webrtc_session.py`)
   - `WebRTCEnabledSession`: 继承自 PluginedSessionInfo
   - 组合 WebRTCStreamManager
   - 重写 close() 方法确保关闭所有 WebRTC 流
   - 提供便捷的 WebRTC API

4. **测试文件** (`tests/test_webrtc_basic.py`)
   - 基础单元测试框架
   - 测试模型类和基本功能

### 修改文件

1. **LiveService** (`app/services/RPA_browser/live_service.py`)
   - 添加 `create_webrtc_enabled_session()` 静态方法
   - 修复心跳检测中的活跃客户端计数逻辑
   - 移除对旧服务的引用

2. **WebRTC Router** (`app/controller/v1/browser_control/webrtc/router.py`)
   - 更新所有路由处理器使用新的 OOP 架构
   - 移除对 `WebRTCVideoStreamService` 的依赖
   - 改进错误处理和响应

### 删除文件

1. `app/services/RPA_browser/webrtc_video_service.py` (旧的函数式实现)

## 关键特性

### 1. 服务器到客户端单向视频流
- 服务器作为生产者发送视频帧
- 客户端作为消费者接收和显示
- 不需要客户端发送任何媒体流回服务器

### 2. 严格的流隔离
- 每个 `page_index` 对应独立的 WebRTCStreamSession
- 流之间互不干扰
- 通过 stream_key (`{mid}:{browser_id}:{page_index}`) 唯一标识

### 3. 高性能非阻塞处理
- 所有 CPU 密集型操作（JPEG 解码、格式转换）在 `asyncio.to_thread()` 中执行
- 使用 PyAV 库进行视频帧处理
- 帧队列采用丢旧保新策略防止内存溢出

### 4. 智能会话管理
- 可配置的闲置超时（从 `settings.browser_webrtc_idle_timeout` 读取，默认 300 秒）
- 自动清理机制：每分钟检查一次，关闭超时的流
- ICE 连接状态监控，失败时自动清理

### 5. 资源管理
- Page 关闭时自动停止对应的 WebRTCStreamSession
- BrowserContext 关闭时关闭所有 WebRTC 流
- 使用 try-finally 确保资源释放

## 技术实现细节

### 视频捕获流程

1. **帧捕获**: 使用 Playwright `page.screencast.start(on_frame=callback)` 
2. **帧队列**: 异步队列 (maxsize=10)，丢旧保新
3. **帧解码**: 在线程池中执行 JPEG → PIL Image → av.VideoFrame → YUV420P
4. **帧传输**: WebRTCMediaTrack 从生产者获取帧并设置时间戳
5. **WebRTC 发送**: aiortc PeerConnection 将帧发送给客户端

### 性能优化

- **线程池解码**: 避免阻塞 asyncio 事件循环
- **YUV420P 格式**: WebRTC 标准格式，减少客户端转换开销
- **90kHz 时间戳**: 符合 WebRTC 标准的时间戳计算
- **队列大小限制**: 防止内存泄漏，保持低延迟

### 错误处理

- ICE 连接失败时自动关闭流
- 页面关闭时检测并清理对应流
- 解码失败时记录日志但不中断整个流
- 完善的异常捕获和日志记录

## API 使用示例

### 创建 WebRTC Offer

```python
from app.services.RPA_browser.live_service import LiveService

# 获取或创建 WebRTC-enabled 会话
webrtc_session = await LiveService.create_webrtc_enabled_session(mid, browser_id)

# 创建 Offer
offer_data = await webrtc_session.get_webrtc_offer(page_index=0)
# 返回: {"sdp": "...", "type": "offer", "stream_key": "123:456:0"}
```

### 处理 Answer 和 ICE Candidate

```python
# 处理 Answer
await webrtc_session.handle_webrtc_answer(page_index, sdp, type)

# 添加 ICE Candidate
await webrtc_session.add_webrtc_ice_candidate(page_index, candidate, sdpMid, sdpMLineIndex)
```

### 关闭流

```python
# 关闭单个流
await webrtc_session.close_webrtc_stream(page_index)

# 关闭所有流
await webrtc_session.close_all_webrtc_streams()
```

## 配置

所有配置从 `app/config.py` 的 `settings` 对象读取：

```python
# WebRTC 视频流配置
browser_webrtc_idle_timeout: int = 300  # WebRTC 流最大闲置时间（秒），默认5分钟
```

## 验证

所有模块已成功导入并验证：

```bash
✓ Models imported successfully
✓ WebRTC services imported successfully  
✓ WebRTCEnabledSession imported successfully
```

## 下一步建议

1. **端到端测试**: 在实际环境中测试完整的 WebRTC 连接流程
2. **性能测试**: 测试多并发流的性能和资源使用情况
3. **客户端实现**: 开发配套的 Web 客户端来接收和显示视频流
4. **监控和指标**: 添加更详细的性能指标和监控
5. **文档完善**: 补充 API 文档和使用示例

## 总结

本次重构成功实现了：
- ✅ 完整的面向对象设计
- ✅ 与现有 PluginedSessionInfo 架构无缝集成
- ✅ 高性能非阻塞视频处理
- ✅ 严格的流隔离和资源管理
- ✅ 可配置的闲置超时和自动清理
- ✅ 完善的错误处理和日志记录

新架构更加模块化、可维护，并且遵循了单一职责原则和开闭原则。
