"""Wave 0 / Task ACC —— 连接池负载复现签收脚本（一次性基线签收，非持续门禁）。

测量「单进程内 M 路并发 run_ai_format 时的并发持连接峰值」，隔离 Task 1a（慢 IO 期间
不持连接）带来的行为差异——与池绝对容量无关（测试 engine 默认池=15，生产=60），所以
看的是 *峰值并发持连接数*，不是绝对的「≪60」。

- 改造前（当前实现）：run_ai_format 在 ai_format.py:766 开 session、一路持到 895，
  跨整个 LLM 调用（默认 timeout 120s）。M 路并发 → 峰值并发持连接 ≈ M。
  本脚本断言此基线，证明仪器真实观测到「慢 IO 期间持连接」这一反模式。
- 改造后（Task 1a）：连接只在段1/段3 短暂出现，峰值应 ≪ M（≈1-3）。
  ⚠️ Task 1a 落地后，启用文件末尾的 AFTER 断言（现在启用它会失败——那个失败正是
  「Task 1a 尚未做」的证据）。

范围限定单进程：#110 的多进程/多人放大由 Task 6/7 的跨进程封顶覆盖，本脚本不涉。
持续防回归不靠本脚本，靠 Task 1a 的确定性单测 + Task G 运行期断言。

opt-in：标 `load`，默认不跑；需 `GEO_RUN_LOAD_TESTS=1` + `GEO_TEST_DATABASE_URL`。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from server.tests.utils import build_test_app

_M = 12  # 并发 run_ai_format 数（< 测试 engine 默认池上限 15，避免排队污染测量）
_LLM_SLEEP = 0.5  # 模拟 LLM 耗时；足够长以保证 M 路在该窗口内重叠


def _fake_completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _CheckoutPeak:
    """用 engine 的 checkout/checkin 事件统计并发持连接峰值（真实计数，非定时采样）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def attach(self, engine) -> None:
        @event.listens_for(engine, "checkout")
        def _on_checkout(dbapi_con, con_record, con_proxy):  # noqa: ANN001
            with self._lock:
                self.current += 1
                self.peak = max(self.peak, self.current)

        @event.listens_for(engine, "checkin")
        def _on_checkin(dbapi_con, con_record):  # noqa: ANN001
            with self._lock:
                self.current = max(0, self.current - 1)


@pytest.mark.mysql
@pytest.mark.load
def test_baseline_peak_connection_holding_under_concurrency(monkeypatch):
    # 设 AI Key，让 run_ai_format 越过 api_key 校验、真正走到（被 mock 的）LLM 调用
    monkeypatch.setenv("GEO_AI_FORMAT_API_KEY", "test-key")
    test_app = build_test_app(monkeypatch)
    client = test_app.client
    try:
        from server.app.modules.articles.ai_format import run_ai_format
        from server.app.modules.articles.models import Article

        # M 篇文章，各自上锁（满足 run_ai_format 的锁指纹检查）。微秒置 0 避免 DATETIME 精度错配。
        lock_started_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        article_ids: list[int] = []
        for i in range(_M):
            resp = client.post(
                "/api/articles",
                json={
                    "title": f"load-{i}",
                    "content_json": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": f"正文段落 {i}"}],
                            }
                        ],
                    },
                },
            )
            assert resp.status_code == 200
            article_ids.append(resp.json()["id"])

        with test_app.session_factory() as db:
            for aid in article_ids:
                a = db.get(Article, aid)
                a.ai_checking = True
                a.ai_checking_started_at = lock_started_at
            db.commit()

        peak = _CheckoutPeak()
        peak.attach(test_app.engine)

        # 模拟 LLM：记录"同时在 LLM 内"的并发度（验证 M 路确实重叠），并睡 _LLM_SLEEP。
        in_llm_lock = threading.Lock()
        state = {"in_llm": 0, "max_in_llm": 0}
        barrier = threading.Barrier(_M)

        def _fake_llm(**_):
            with in_llm_lock:
                state["in_llm"] += 1
                state["max_in_llm"] = max(state["max_in_llm"], state["in_llm"])
            try:
                time.sleep(_LLM_SLEEP)
            finally:
                with in_llm_lock:
                    state["in_llm"] -= 1
            return _fake_completion('{"heading_indices": []}')

        monkeypatch.setattr(
            "server.app.modules.articles.ai_format._call_litellm_completion", _fake_llm
        )

        def _run(aid: int) -> None:
            barrier.wait(timeout=10)
            run_ai_format(aid, include_images=False, lock_started_at=lock_started_at)

        with ThreadPoolExecutor(max_workers=_M) as ex:
            list(ex.map(_run, article_ids))

        print(
            f"\n[Task ACC baseline] M={_M} LLM_SLEEP={_LLM_SLEEP}s "
            f"max_in_llm={state['max_in_llm']} peak_checkouts={peak.peak}"
        )

        # 仪器有效性：M 路确实在 LLM 窗口内重叠，否则测量无意义
        assert state["max_in_llm"] == _M, (
            f"threads did not overlap in LLM window: {state['max_in_llm']}/{_M}"
        )

        # 改造前基线：当前实现 LLM 期间持连接 → 峰值并发持连接 ≈ M。
        # 若此处 < M，说明 run_ai_format 已被改造（或仪器失效）——此时应改走下方 AFTER 断言。
        assert peak.peak >= _M, (
            f"expected current code to hold ~{_M} connections during overlapping LLM calls, "
            f"got peak={peak.peak}"
        )

        # TODO(Task 1a 落地后启用，并删除上面的 baseline 断言)：
        # 连接只在段1/段3 短暂出现，峰值应 ≪ M。
        # assert peak.peak <= 3, f"Task 1a should drop peak to ~1-3, got {peak.peak}"
    finally:
        test_app.cleanup()
