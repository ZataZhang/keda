"""POSIX 上真实 TIME_WAIT 连接与占用监听端口的对照。"""

import socket
import sys

import pytest

from backend.api.cli_typer_console import ConsolePortUnavailableError, resolve_console_port


@pytest.mark.skipif(sys.platform == "win32", reason="本题测 POSIX TIME_WAIT；Windows 另需原生验证")
def test_reuse_port_after_server_active_close():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = listener.accept()
            connection.shutdown(socket.SHUT_WR)
            assert client.recv(1) == b""
            client.shutdown(socket.SHUT_WR)
            connection.close()
    assert resolve_console_port(host="127.0.0.1", explicit_port=port, default_port=port) == port


@pytest.mark.parametrize(
    "listener_host,reuse", [("127.0.0.1", False), ("127.0.0.1", True), ("0.0.0.0", True)]
)
def test_live_listener_is_not_mistaken_for_reusable_port(listener_host, reuse):
    with socket.socket() as listener:
        if reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((listener_host, 0))
        port = listener.getsockname()[1]
        listener.listen()
        with pytest.raises(ConsolePortUnavailableError):
            resolve_console_port(host="127.0.0.1", explicit_port=port, default_port=port)
