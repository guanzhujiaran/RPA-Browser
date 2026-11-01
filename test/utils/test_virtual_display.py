"""虚拟显示（Xvfb）探测逻辑测试

背景：宿主重启 / 桌面会话退出后，`/tmp/.X11-unix/X0` 这类 socket 文件常常残留，
但后面已经没有 X server。旧实现只看 `DISPLAY` 环境变量有没有值就直接复用，
浏览器会在死掉的 display 上启动：进程起来即退出，`browser_context.pages` 为空，
上层判定 `is_closed`，报「浏览器在创建过程中被关闭，请重试」。

`_display_alive` 必须真连一次 unix socket，只有 server 活着才算可用。
测试用 monkeypatch 把 socket 路径改到 tmp_path（`/tmp/.X11-unix` 在部分环境只读）。
"""

import socket

import pytest

from app.utils import virtual_display
from app.utils.virtual_display import _display_alive, _display_socket_path


@pytest.fixture
def unix_dir(tmp_path, monkeypatch):
    """把 display socket 路径重定向到临时目录"""
    monkeypatch.setattr(
        virtual_display,
        "_display_socket_path",
        lambda display: str(tmp_path / f"X{display.lstrip(':').split('.')[0]}"),
    )
    return tmp_path


class TestDisplaySocketPath:
    def test_display_to_socket_path(self):
        assert _display_socket_path(":0") == "/tmp/.X11-unix/X0"
        assert _display_socket_path(":99") == "/tmp/.X11-unix/X99"
        # `:99.0` 这种带 screen 的写法只取 display 号
        assert _display_socket_path(":99.0") == "/tmp/.X11-unix/X99"


class TestDisplayAlive:
    def test_missing_socket_is_dead(self, unix_dir):
        """socket 文件不存在 → 不可用"""
        assert _display_alive(":96") is False

    def test_stale_socket_file_is_dead(self, unix_dir):
        """socket 文件残留但没有 server 监听 → 必须判为不可用

        这正是宿主重启后的现场：文件还在，connect 会被拒绝。
        """
        (unix_dir / "X95").write_text("", encoding="utf-8")
        assert _display_alive(":95") is False

    def test_listening_socket_is_alive(self, unix_dir):
        """真正在 listen 的 unix socket → 可用"""
        path = str(unix_dir / "X94")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(path)
            server.listen(1)
            assert _display_alive(":94") is True
        finally:
            server.close()

    def test_bound_but_not_listening_is_dead(self, unix_dir):
        """socket 已 bind 但未 listen（没有 server 提供服务）→ 不可用"""
        path = str(unix_dir / "X93")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(path)
            assert _display_alive(":93") is False
        finally:
            sock.close()
