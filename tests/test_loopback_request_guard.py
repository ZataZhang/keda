"""本机 Console 请求来源守卫的测试。

守卫挡的是「经浏览器打到回环 API」的两条路径：DNS rebinding（``Host`` 不是回环）与
跨站写请求（``Origin`` / ``Referer`` 不是回环）。用例走真实的 ``backend.api.app``
装配，证明中间件确实挂在全部路由之前；``/api/auth/*`` 没有外部依赖，适合做探针。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app

#: ``base_url`` 决定 ASGI ``scope["server"]``：回环地址才进入守卫的管辖范围。
loopback_client = TestClient(app, base_url="http://127.0.0.1:8313")
non_loopback_client = TestClient(app, base_url="http://10.0.0.5:8000")

_SESSION_PATH = "/api/auth/me"
_LOGOUT_PATH = "/api/auth/logout"


def test_loopback_page_request_passes() -> None:
    """本机页面的读请求与同源写请求照常放行。"""
    assert loopback_client.get(_SESSION_PATH).status_code == 200
    same_origin_response = loopback_client.post(
        _LOGOUT_PATH, headers={"Origin": "http://127.0.0.1:8313"}
    )
    assert same_origin_response.status_code == 204


def test_rebound_host_is_rejected_even_for_reads() -> None:
    """DNS rebinding：连接到回环但 ``Host`` 是外部域名，读请求也拒绝。"""
    rebound_response = loopback_client.get(_SESSION_PATH, headers={"Host": "evil.example:8313"})

    assert rebound_response.status_code == 403
    assert "evil.example" in rebound_response.json()["detail"]


@pytest.mark.parametrize(
    "source_headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "null", "Referer": "http://evil.example/page"},
        {"Referer": "http://evil.example/page"},
    ],
)
def test_cross_site_write_is_rejected(source_headers: dict[str, str]) -> None:
    """跨站写请求（含不透明来源）在到达路由前被拒绝。"""
    assert loopback_client.post(_LOGOUT_PATH, headers=source_headers).status_code == 403


@pytest.mark.parametrize(
    "source_headers",
    [
        {},
        {"Origin": "http://localhost:3000"},
        {"Origin": "null", "Referer": "http://127.0.0.1:8313/app/companion/"},
        {"Origin": "http://[::1]:8313"},
    ],
)
def test_local_write_sources_pass(source_headers: dict[str, str]) -> None:
    """非浏览器客户端（无来源头）、dev 前端与桌面小窗的写请求放行。"""
    assert loopback_client.post(_LOGOUT_PATH, headers=source_headers).status_code == 204


def test_non_loopback_connection_is_out_of_scope() -> None:
    """经网卡地址进来的连接（容器、显式对外监听）不归本守卫管。"""
    external_response = non_loopback_client.post(
        _LOGOUT_PATH, headers={"Origin": "http://console.example"}
    )

    assert external_response.status_code == 204
