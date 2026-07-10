"""纯函数单测：resolve_public_base_url —— 反代后按 X-Forwarded-* 还原用户实际访问的 base_url。

不依赖 DB / app（无 pytest.mark.mysql），任何环境都能跑，是本 bug 修复的主门禁。
背景：边缘 nginx 终止 TLS、以 http 反代给后端，request.base_url 的 scheme 恒为 http，
导致 MCP 接入页把 https 访问显示成 http://。修复=读 X-Forwarded-Proto/Host 还原。
"""

from __future__ import annotations

from server.app.modules.mcp_catalog.connect_router import resolve_public_base_url


def test_no_forwarded_headers_uses_request_values():
    # dev 直连（无代理头）→ 回落 request 自身的 scheme/host
    assert (
        resolve_public_base_url(
            default_scheme="http",
            default_netloc="127.0.0.1:8000",
            forwarded_proto=None,
            forwarded_host=None,
        )
        == "http://127.0.0.1:8000"
    )


def test_forwarded_proto_https_overrides_scheme():
    # prod：边缘 nginx 设 X-Forwarded-Proto: https，即便后端连接是 http 也应还原 https
    assert (
        resolve_public_base_url(
            default_scheme="http",
            default_netloc="geo.example.com",
            forwarded_proto="https",
            forwarded_host=None,
        )
        == "https://geo.example.com"
    )


def test_forwarded_proto_comma_list_takes_first():
    # 多级代理时 X-Forwarded-Proto 可能是 "https, http"，取最外层客户端=第一个
    assert (
        resolve_public_base_url(
            default_scheme="http",
            default_netloc="geo.example.com",
            forwarded_proto="https, http",
            forwarded_host=None,
        )
        == "https://geo.example.com"
    )


def test_forwarded_host_overrides_host():
    # X-Forwarded-Host 存在时用它（含协议一并按实际访问还原）
    assert (
        resolve_public_base_url(
            default_scheme="http",
            default_netloc="internal-app:8000",
            forwarded_proto="https",
            forwarded_host="geo.example.com",
        )
        == "https://geo.example.com"
    )


def test_blank_forwarded_values_fall_back():
    # 空/空白的转发头视作缺失，回落 request 值（不产生 "://" 空串）
    assert (
        resolve_public_base_url(
            default_scheme="https",
            default_netloc="host",
            forwarded_proto="   ",
            forwarded_host="",
        )
        == "https://host"
    )


def test_result_has_no_trailing_slash():
    assert not resolve_public_base_url(
        default_scheme="http",
        default_netloc="h:8000",
        forwarded_proto=None,
        forwarded_host=None,
    ).endswith("/")
