"""GET /api/system/db-pool 资源指标端点测试（Task 3，封堵 #10）。

验证：
- 占用 N 条 DB 连接后请求端点，`checked_out` 反映占用、`size`/`max` 等关键字段在场；
- 非 admin（operator）访问返回 403。

带 @pytest.mark.mysql：需要 GEO_TEST_DATABASE_URL，裸跑 pytest 自动跳过。
"""

from __future__ import annotations

import pytest

from server.tests.utils import build_test_app, create_extra_user


@pytest.mark.mysql
def test_db_pool_reflects_checked_out_connections(monkeypatch) -> None:
    test_app = build_test_app(monkeypatch)
    try:
        # 在 build_test_app 设好 GEO_DATA_DIR 后再导入 engine（import 期会建数据目录）
        from server.app.db.session import engine

        client = test_app.client

        # 基线：先读一次，确认 checked_out 是个非负整数、关键字段在场
        baseline = client.get("/api/system/db-pool")
        assert baseline.status_code == 200, baseline.text
        body = baseline.json()
        pool = body["pool"]
        assert isinstance(pool["checked_out"], int)
        assert isinstance(pool["size"], int)
        # configured max = pool_size + max_overflow，必须暴露
        assert pool["max"] >= pool["size"]
        baseline_checked_out = pool["checked_out"]

        # 手动占用 N 条连接（不还），断言端点能观测到上升
        n = 3
        held = [engine.connect() for _ in range(n)]
        try:
            resp = client.get("/api/system/db-pool")
            assert resp.status_code == 200, resp.text
            pool2 = resp.json()["pool"]
            assert pool2["checked_out"] >= baseline_checked_out + n, (
                f"expected checked_out to rise by >= {n}; "
                f"baseline={baseline_checked_out} now={pool2['checked_out']}"
            )
        finally:
            for conn in held:
                conn.close()
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_db_pool_requires_admin(monkeypatch) -> None:
    test_app = build_test_app(monkeypatch)
    try:
        _uid, operator_client = create_extra_user(test_app, "op-metrics", role="operator")
        resp = operator_client.get("/api/system/db-pool")
        assert resp.status_code == 403, resp.text
    finally:
        test_app.cleanup()
