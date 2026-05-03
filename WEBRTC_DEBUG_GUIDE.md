# WebRTC "流未激活" 错误调试指南

## 错误信息

```json
{
  "code": 2007,
  "data": null,
  "msg": "WebRTC 流未激活"
}
```

## 错误原因

这个错误表示在调用 WebRTC Answer、ICE Candidate 或 Close 接口时，找不到对应的活跃流。

### 常见原因

1. **调用顺序错误** - 没有先调用 `/webrtc/offer` 创建流
2. **stream_key 格式错误** - 无法正确解析 page_index
3. **流已超时被清理** - offer 和 answer 之间间隔太长（默认 300 秒）
4. **page_index 不匹配** - 请求的页面索引与创建的流不一致

## 正确的调用流程

### 步骤 1: 创建 Offer（启动流）

```javascript
// 客户端首先调用 offer 接口
const offerResponse = await fetch('/api/v1/rpa/browser/control/webrtc/offer', {
    method: 'POST',
    headers: {
        'Authorization': 'Bearer YOUR_TOKEN',
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        browser_id: 123,
        page_index: 0  // 要共享的页面索引
    })
});

const { sdp, type, stream_key } = await offerResponse.json();
// stream_key 格式: "mid:browser_id:page_index"，例如 "1:123:0"
```

**服务器端行为**：
- 自动启用 WebRTC（如果尚未启用）
- 创建 WebRTCStreamSession
- 启动帧捕获
- 返回 SDP Offer

### 步骤 2: 客户端处理 Offer 并生成 Answer

```javascript
// 客户端使用收到的 offer 创建 RTCPeerConnection
const pc = new RTCPeerConnection(configuration);

// 设置远程描述（服务器的 offer）
await pc.setRemoteDescription(new RTCSessionDescription({
    sdp: sdp,
    type: type
}));

// 创建 answer
const answer = await pc.createAnswer();
await pc.setLocalDescription(answer);

// 收集 ICE candidates
pc.onicecandidate = (event) => {
    if (event.candidate) {
        // 发送 ICE candidate 到服务器
        sendIceCandidate(event.candidate);
    }
};
```

### 步骤 3: 发送 Answer 到服务器

```javascript
// 将客户端的 answer 发送给服务器
await fetch('/api/v1/rpa/browser/control/webrtc/answer', {
    method: 'POST',
    headers: {
        'Authorization': 'Bearer YOUR_TOKEN',
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        stream_key: stream_key,  // 从 offer 响应中获取
        sdp: answer.sdp,
        type: answer.type
    })
});
```

**服务器端行为**：
- 根据 stream_key 找到对应的流
- 设置远程描述（客户端的 answer）
- WebRTC 连接建立完成

### 步骤 4: 发送 ICE Candidates（可选，取决于实现）

```javascript
// 如果使用了 trickle ICE，需要逐个发送 candidates
async function sendIceCandidate(candidate) {
    await fetch('/api/v1/rpa/browser/control/webrtc/ice-candidate', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer YOUR_TOKEN',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            stream_key: stream_key,
            candidate: candidate.candidate,
            sdpMid: candidate.sdpMid,
            sdpMLineIndex: candidate.sdpMLineIndex
        })
    });
}
```

## 调试步骤

### 1. 检查服务器日志

改进后的错误信息会提供详细诊断：

```
WARNING: 会话 1_123 未启用 WebRTC。请先调用 /webrtc/offer 接口创建流。
```

或

```
WARNING: 找不到 page_index=0 的 WebRTC 流。当前活跃流: []
```

### 2. 验证调用顺序

确保严格按照以下顺序调用：

```
✅ 正确顺序：
1. POST /webrtc/offer       → 获取 stream_key
2. POST /webrtc/answer      → 使用相同的 stream_key
3. POST /webrtc/ice-candidate → 使用相同的 stream_key（可选）

❌ 错误顺序：
1. POST /webrtc/answer      → 流还不存在！
```

### 3. 检查 stream_key 格式

stream_key 必须是 `mid:browser_id:page_index` 格式：

```javascript
// ✅ 正确
stream_key: "1:123:0"

// ❌ 错误
stream_key: "1-123-0"     // 分隔符错误
stream_key: "1:123"       // 缺少 page_index
stream_key: "abc:123:0"   // mid 不是数字
```

### 4. 检查时间间隔

WebRTC 流有闲置超时（默认 300 秒）。如果 offer 和 answer 之间间隔太长，流会被自动清理。

```javascript
// ✅ 快速发送 answer（几秒内）
const offer = await createOffer();
const answer = await createAnswer();
await sendAnswer(answer);  // 立即发送

// ❌ 延迟太久
const offer = await createOffer();
await sleep(400000);  // 等待 400 秒 > 300 秒超时
await sendAnswer(answer);  // 流已被清理！
```

### 5. 验证 page_index

确保 answer 中的 page_index 与 offer 中的一致：

```javascript
// Offer 请求
{
    browser_id: 123,
    page_index: 0  // ← 注意这个值
}

// Answer 请求
{
    stream_key: "1:123:0",  // ← 必须匹配 page_index=0
    sdp: "...",
    type: "answer"
}
```

## 常见问题排查

### 问题 1: "WebRTC 未启用，请先调用 /webrtc/offer 创建流"

**原因**：直接调用了 answer 接口，没有先调用 offer。

**解决**：
```javascript
// 先调用 offer
const offer = await fetch('/webrtc/offer', {...});
const { stream_key } = await offer.json();

// 再调用 answer
await fetch('/webrtc/answer', {
    body: JSON.stringify({ stream_key, ... })
});
```

### 问题 2: "页面 X 的 WebRTC 流不存在"

**可能原因**：
1. page_index 不匹配
2. 流已被关闭或超时
3. offer 调用失败但客户端没有检查

**解决**：
```javascript
// 检查 offer 是否成功
const offerResponse = await fetch('/webrtc/offer', {...});
if (!offerResponse.ok) {
    console.error('Offer 创建失败:', await offerResponse.text());
    return;
}

const { stream_key } = await offerResponse.json();
console.log('Stream key:', stream_key);  // 确认 stream_key 正确

// 然后再发送 answer
```

### 问题 3: 流创建后立即消失

**原因**：可能有其他代码关闭了流，或者会话被关闭。

**检查**：
```python
# 查看服务器日志
# 应该有类似这样的日志：
# INFO: WebRTC 流已创建: 1:123:0 (page_index=0)
# WARNING: WebRTC 流因闲置超时而关闭: page_index=0, idle_time=301s, timeout=300s
```

## 完整的客户端示例

```javascript
class WebRTCClient {
    constructor(baseUrl, token) {
        this.baseUrl = baseUrl;
        this.token = token;
        this.pc = null;
        this.streamKey = null;
    }
    
    async startStreaming(browserId, pageIndex = 0) {
        try {
            // 步骤 1: 创建 Offer
            console.log('Creating offer...');
            const offerResponse = await fetch(`${this.baseUrl}/webrtc/offer`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.token}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    browser_id: browserId,
                    page_index: pageIndex
                })
            });
            
            if (!offerResponse.ok) {
                throw new Error(`Offer failed: ${await offerResponse.text()}`);
            }
            
            const { sdp, type, stream_key } = await offerResponse.json();
            this.streamKey = stream_key;
            console.log('Offer created, stream_key:', stream_key);
            
            // 步骤 2: 创建 PeerConnection
            this.pc = new RTCPeerConnection({
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' }
                ]
            });
            
            // 接收视频流
            this.pc.ontrack = (event) => {
                console.log('Received video track');
                const videoElement = document.getElementById('remoteVideo');
                if (videoElement) {
                    videoElement.srcObject = event.streams[0];
                }
            };
            
            // 步骤 3: 设置远程描述
            await this.pc.setRemoteDescription(new RTCSessionDescription({
                sdp: sdp,
                type: type
            }));
            
            // 步骤 4: 创建 Answer
            const answer = await this.pc.createAnswer();
            await this.pc.setLocalDescription(answer);
            
            // 步骤 5: 发送 Answer
            console.log('Sending answer...');
            const answerResponse = await fetch(`${this.baseUrl}/webrtc/answer`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.token}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    stream_key: this.streamKey,
                    sdp: answer.sdp,
                    type: answer.type
                })
            });
            
            if (!answerResponse.ok) {
                throw new Error(`Answer failed: ${await answerResponse.text()}`);
            }
            
            console.log('WebRTC connection established!');
            
        } catch (error) {
            console.error('WebRTC error:', error);
            this.stopStreaming();
        }
    }
    
    stopStreaming() {
        if (this.pc) {
            this.pc.close();
            this.pc = null;
        }
        this.streamKey = null;
    }
}

// 使用示例
const client = new WebRTCClient('http://localhost:8000/api/v1/rpa/browser/control', 'YOUR_TOKEN');
await client.startStreaming(123, 0);
```

## 总结

遇到 "WebRTC 流未激活" 错误时：

1. ✅ 检查是否先调用了 `/webrtc/offer`
2. ✅ 验证 stream_key 格式正确
3. ✅ 确认 page_index 匹配
4. ✅ 检查调用间隔是否超过 300 秒
5. ✅ 查看服务器日志获取详细诊断信息

改进后的错误信息会明确指出问题所在，帮助快速定位和解决！
