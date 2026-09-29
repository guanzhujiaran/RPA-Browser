"""临时测量：暂停后 WebRTC 链路实际还有多少字节在流动。

连上流 → 测一次速率 → 调 /webrtc/pause 暂停 → 再测两次速率（含 framesReceived）。
数据来源与前端网速显示一致（candidate-pair / inbound-rtp 的累计字节）。
"""
import asyncio
import contextlib

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription

MID = "141630480"
BID = "98798656"
BASE = "http://localhost:28000/api/v1/rpa/browser/control"
HEADERS = {"x-bili-mid": MID, "x-bili-level": "level6"}


async def counters(pc: RTCPeerConnection) -> tuple[int, int, int]:
    """返回 (候选对收到的字节, inbound-rtp 收到字节, 收到的帧数)"""
    pair_bytes = 0
    rtp_bytes = 0
    frames = 0
    for report in await pc.getStats():
        rtype = getattr(report, "type", None)
        if rtype == "candidate-pair" and getattr(report, "state", None) == "succeeded":
            pair_bytes = max(pair_bytes, int(getattr(report, "bytesReceived", 0) or 0))
        elif rtype == "inbound-rtp":
            rtp_bytes += int(getattr(report, "bytesReceived", 0) or 0)
            frames += int(getattr(report, "framesReceived", 0) or 0)
    return pair_bytes, rtp_bytes, frames


async def rate(pc: RTCPeerConnection, seconds: float) -> tuple[float, float, int]:
    a = await counters(pc)
    await asyncio.sleep(seconds)
    b = await counters(pc)
    return (
        (b[0] - a[0]) / seconds,
        (b[1] - a[1]) / seconds,
        b[2] - a[2],
    )


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, headers=HEADERS, timeout=30) as client:
        body = (
            await client.post("/webrtc/offer", params={"browser_id": BID}, json={"page_index": 0})
        ).json()
        if body.get("code") != 0:
            print("OFFER_FAILED:", body.get("code"), body.get("msg"))
            return
        data = body["data"]
        stream_key = data["stream_key"]

        pc = RTCPeerConnection()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        for _ in range(50):
            if pc.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.1)
        await client.post(
            "/webrtc/answer",
            params={"browser_id": BID},
            json={"stream_key": stream_key, "sdp": pc.localDescription.sdp, "type": "answer"},
        )
        for line in pc.localDescription.sdp.splitlines():
            if line.startswith("a=candidate"):
                await client.post(
                    "/webrtc/ice-candidate",
                    params={"browser_id": BID},
                    json={
                        "stream_key": stream_key,
                        "candidate": "candidate:" + line[len("a=candidate:"):],
                        "sdpMid": "0",
                        "sdpMLineIndex": 0,
                    },
                )
        for _ in range(100):
            if pc.connectionState == "connected":
                break
            await asyncio.sleep(0.1)
        print("connectionState =", pc.connectionState)

        # 暂停是会话级状态（切换页面/重建流都会保留），先显式恢复，否则下面测不到画面
        r0 = await client.post(
            "/webrtc/pause", params={"browser_id": BID}, json={"paused": False}
        )
        print("resume ->", r0.json().get("code"), (r0.json().get("data") or {}).get("paused"))

        pair_bps, rtp_bps, frames = await rate(pc, 5)
        print(f"[未暂停] 链路 {pair_bps:8.0f} B/s | 视频RTP {rtp_bps:8.0f} B/s | 帧 {frames}")

        r = await client.post(
            "/webrtc/pause", params={"browser_id": BID}, json={"paused": True}
        )
        print("pause ->", r.json().get("code"), (r.json().get("data") or {}).get("paused"))

        await asyncio.sleep(2)  # 等切换稳定
        pair_bps, rtp_bps, frames = await rate(pc, 6)
        print(f"[暂停后] 链路 {pair_bps:8.0f} B/s | 视频RTP {rtp_bps:8.0f} B/s | 帧 {frames}")

        pair_bps, rtp_bps, frames = await rate(pc, 6)
        print(f"[暂停后+6s] 链路 {pair_bps:8.0f} B/s | 视频RTP {rtp_bps:8.0f} B/s | 帧 {frames}")

        with contextlib.suppress(Exception):
            await client.post(
                "/webrtc/close", params={"browser_id": BID}, json={"stream_key": stream_key}
            )
        await pc.close()
        print("已关闭流（前端会自动重连）")


asyncio.run(main())
