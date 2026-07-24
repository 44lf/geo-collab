"""loop_skills 版本化(上传入库 + 版本管理)测试。"""

from __future__ import annotations

import io
import zipfile

import pytest


def _make_zip(files: dict[str, str]) -> bytes:
    """把 {posix_path: text} 打成 zip bytes。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buf.getvalue()


def _valid_files() -> dict[str, str]:
    """一份满足结构校验的最小合法 skill 包。"""
    return {
        "README.md": "# test bundle\n",
        "commands/goal.md": "# goal\n内容\n",
        "skills/geo-goal-orchestrator/SKILL.md": "---\nname: x\n---\norchestrator\n",
    }


@pytest.mark.mysql
def test_can_persist_bundle_version(monkeypatch):
    """能建一行 LoopSkillBundleVersion 并读回 —— 证明表已建 + 模型已注册。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            row = LoopSkillBundleVersion(
                version_label="v-test",
                bundle_sha256="0" * 64,
                files=[{"path": "README.md", "content": "x", "sha256": "0" * 64, "size": 1}],
                file_count=1,
                total_size=1,
                is_enabled=False,
                is_deleted=False,
                uploaded_by_user_id=None,
                notes=None,
            )
            db.add(row)
            db.commit()
            got = db.get(LoopSkillBundleVersion, row.id)
            assert got is not None
            assert got.version_label == "v-test"
            assert got.is_enabled is False
    finally:
        test_app.cleanup()


def test_build_bundle_from_file_map_matches_algorithm():
    """from_file_map 对同一批字节,算出的 sha 与手工按算法算的一致 + 文件按 posix 序。"""
    import hashlib

    from server.app.modules.loop_skills.service import build_bundle_from_file_map

    raw = {
        "commands/goal.md": b"g",
        "README.md": b"r",
        "skills/geo-goal-orchestrator/SKILL.md": b"o",
    }
    bundle = build_bundle_from_file_map(raw, version="v-x")
    # 文件按 posix 串排序:README.md < commands/... < skills/...
    assert [f.path for f in bundle.files] == [
        "README.md",
        "commands/goal.md",
        "skills/geo-goal-orchestrator/SKILL.md",
    ]
    # 手工复算 bundle sha
    h = hashlib.sha256()
    for f in bundle.files:
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.sha256.encode("ascii"))
        h.update(b"\x00")
    assert bundle.bundle_sha256 == h.hexdigest()
    assert bundle.version == "v-x"


@pytest.mark.mysql
def test_get_active_bundle_falls_back_to_seed(monkeypatch):
    """无启用版 → get_active_bundle 回落种子 build_bundle()(5 文件)。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            active = vs.get_active_bundle(db)
            assert len(active.files) == 5  # 种子
            assert vs.list_versions(db) == []  # 库里还没有上传版
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_persists_unenabled(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            meta = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="v1",
                notes="note",
                uploaded_by_user_id=None,
            )
            db.commit()
            assert meta.is_enabled is False
            assert meta.version_label == "v1"
            assert meta.file_count == 3
            assert meta.total_size > 0
            assert len(meta.bundle_sha256) == 64
            # 未启用 → 列表可见、get_active 仍回落种子
            assert [m.id for m in vs.list_versions(db)] == [meta.id]
            assert len(vs.get_active_bundle(db).files) == 5
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_rejections(monkeypatch):
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            # 缺必需文件
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip({"README.md": "x"}),
                    version_label=None,
                    notes=None,
                    uploaded_by_user_id=None,
                )
            # 非白名单路径
            bad = dict(_valid_files())
            bad["evil.sh"] = "rm -rf"
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, _make_zip(bad), version_label=None, notes=None, uploaded_by_user_id=None
                )
            # zip-slip
            slip = dict(_valid_files())
            slip["../escape.md"] = "x"
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, _make_zip(slip), version_label=None, notes=None, uploaded_by_user_id=None
                )
            # 非 zip
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, b"not a zip", version_label=None, notes=None, uploaded_by_user_id=None
                )
            # 非 utf-8 成员
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for p, t in _valid_files().items():
                    zf.writestr(p, t)
                zf.writestr("skills/x/bin.md", b"\xff\xfe\x00binary")
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, buf.getvalue(), version_label=None, notes=None, uploaded_by_user_id=None
                )
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_resource_and_label_limits(monkeypatch):
    """C:条目数 / 累计解压体积 / 重名;B:label 控制字符 / 超长 —— 全部 ValidationError。"""
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            # 条目数超限:required + 60 个 skills/ 小文件 > 50
            too_many = _valid_files()
            for i in range(60):
                too_many[f"skills/pad/{i}.md"] = "x"
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip(too_many),
                    version_label=None,
                    notes=None,
                    uploaded_by_user_id=None,
                )

            # 累计解压体积超限:3 个 1.5MB 高压缩比文件(压缩后仍 < 2MB zip 上限,但解压累计 4.5MB > 4MB)
            big = _valid_files()
            for i in range(3):
                big[f"skills/big/{i}.md"] = "a" * (1_500_000)
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, _make_zip(big), version_label=None, notes=None, uploaded_by_user_id=None
                )

            # 重名路径:zip 允许同名条目,dict-based _make_zip 造不出,直接用 ZipFile 写两次
            dup = io.BytesIO()
            with zipfile.ZipFile(dup, "w") as zf:
                for p, t in _valid_files().items():
                    zf.writestr(p, t)
                zf.writestr("commands/goal.md", "第二份 goal")  # 与 required 同名
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, dup.getvalue(), version_label=None, notes=None, uploaded_by_user_id=None
                )

            # label 含换行(header 注入面)
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip(_valid_files()),
                    version_label="a\r\nInjected: x",
                    notes=None,
                    uploaded_by_user_id=None,
                )
            # label 超长
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip(_valid_files()),
                    version_label="x" * 201,
                    notes=None,
                    uploaded_by_user_id=None,
                )

            # 中文 label 合法(latin-1 由 router 头部 percent-encode 兜底,service 层放行)
            m = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="2026-07-08 严格版",
                notes=None,
                uploaded_by_user_id=None,
            )
            assert m.version_label == "2026-07-08 严格版"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_enable_is_singleton_and_switch(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            a = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="A",
                notes=None,
                uploaded_by_user_id=None,
            )
            b = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="B",
                notes=None,
                uploaded_by_user_id=None,
            )
            db.commit()
            vs.enable_version(db, a.id)
            vs.enable_version(db, b.id)  # 切到 B
            enabled = (
                db.execute(
                    __import__("sqlalchemy")
                    .select(LoopSkillBundleVersion.id)
                    .where(LoopSkillBundleVersion.is_enabled.is_(True))
                )
                .scalars()
                .all()
            )
            assert enabled == [b.id]  # 恰好一个,且是 B
            assert vs.get_active_bundle(db).version == "B"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_soft_delete_rejects_enabled(monkeypatch):
    from server.app.shared.errors import ConflictError
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            a = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="A",
                notes=None,
                uploaded_by_user_id=None,
            )
            db.commit()
            vs.enable_version(db, a.id)
            with pytest.raises(ConflictError):
                vs.soft_delete_version(db, a.id)  # 当前启用版,拒删
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_enable_from_zero_concurrent_stays_singleton(monkeypatch):
    """从零启用态并发 enable(A)/enable(B) —— GET_LOCK 保证最终恰好一个启用版。"""
    import threading

    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            a = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="A",
                notes=None,
                uploaded_by_user_id=None,
            )
            b = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="B",
                notes=None,
                uploaded_by_user_id=None,
            )
            db.commit()
            ids = [a.id, b.id]

        barrier = threading.Barrier(2)
        errors: list[Exception] = []  # 捕获而非吞:DeepSeek 建议的 except:pass 会放大假绿

        def _worker(vid: int) -> None:
            s = SessionLocal()
            try:
                barrier.wait()
                vs.enable_version(s, vid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                s.close()

        threads = [threading.Thread(target=_worker, args=(i,)) for i in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 锁正确时二者应串行成功(各自临界区极短、10s 超时足够)——不应有 ConflictError
        assert errors == [], f"并发 enable 不应抛错(锁应让二者串行成功): {errors}"

        with SessionLocal() as db:
            enabled = (
                db.execute(
                    __import__("sqlalchemy")
                    .select(LoopSkillBundleVersion.id)
                    .where(LoopSkillBundleVersion.is_enabled.is_(True))
                )
                .scalars()
                .all()
            )
            assert len(enabled) == 1  # 恰好一个,不是两个

        # A 的杀手锏:证明锁**真释放**(初稿的 session.commit 后 RELEASE 落错连接=假绿抓不到)。
        # 用全新连接 GET_LOCK(name, 0) 立即取:若前面泄漏了锁,这里会返回 0。
        #
        # 偏差说明(相对任务 brief 原文):brief 原用 `from server.app.db.session import engine` +
        # `engine.connect()` 做探针。但 build_test_app(见 server/tests/utils.py:205)只
        # monkeypatch 了 `server.app.db.session.SessionLocal`,**没有**重绑模块级 `engine`
        # ——那个 `engine` 可能指向与本测试 DB 不同的 server/库。若探针连去别的 server,
        # GET_LOCK 在那边永远能立即拿到(因为 enable_version 从未在那边加过锁),探针会
        # 假性返回 1、把"锁未释放"的真 bug 掩盖成误报的绿。改为从
        # `SessionLocal().get_bind()` 取 bind——这与 enable_version 内部
        # `session.get_bind()` 用的是同一个 bind(同一个被 monkeypatch 的 TestingSessionLocal
        # 绑定的 engine),确保探针查的是"真被加过锁的那个连接池/server"。
        from sqlalchemy import text as _text

        probe_bind = SessionLocal().get_bind()  # 同 enable_version 的 bind(测试库/同一 server)
        with probe_bind.connect() as probe:
            got = probe.execute(
                _text("SELECT GET_LOCK(:k, 0)"), {"k": "geo_loop_skill_enable"}
            ).scalar()
            assert got == 1, "锁泄漏:enable 完成后应能立即再取同名锁"
            probe.execute(_text("SELECT RELEASE_LOCK(:k)"), {"k": "geo_loop_skill_enable"})
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_old_readonly_endpoints_alias_to_official_skill(monkeypatch):
    """Task 7:旧 /loop-skill-bundle/{info,download.zip,versions,install-payload}
    改读官方 skill(slug=goal)的当前版本,不再读 LoopSkillBundleVersion 表。

    (原 test_version_endpoints_end_to_end 整个改写——旧的上传/启用/点名下载
    机制随本任务下线，见下面 test_old_write_endpoints_are_removed。)
    """
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    c = test_app.client
    try:
        # 官方 skill 尚未 seed(Task 8 才建种子)时:info/download.zip 找不到官方包 → 400,
        # versions 列表友好地回空(不报错)。
        assert c.get("/api/mcp/loop-skill-bundle/info").status_code == 400
        assert c.get("/api/mcp/loop-skill-bundle/versions").json()["versions"] == []

        # 直接用 skill_service 建官方 skill(slug=goal, is_official=True)——
        # 模拟 Task 8 seed 完成后的状态。
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import skill_service as svc

        with SessionLocal() as db:
            sk, _version_row = svc.create_version(
                db,
                entries=[
                    ("commands/goal.md", b"# goal\n"),
                    (
                        "skills/geo-goal-orchestrator/SKILL.md",
                        b"---\nname: x\n---\norchestrator\n",
                    ),
                ],
                name="/goal loop skills",
                uploaded_by=None,
            )
            sk.slug = "goal"
            sk.is_official = True
            db.commit()

        # info 现在返回官方 skill 当前版本(2 个文件)
        info = c.get("/api/mcp/loop-skill-bundle/info").json()
        assert info["version"] == "v1"
        assert {f["path"] for f in info["files"]} == {
            "commands/goal.md",
            "skills/geo-goal-orchestrator/SKILL.md",
        }

        # versions 列表也读同一个官方 skill(唯一版本即当前版本)
        listed = c.get("/api/mcp/loop-skill-bundle/versions").json()["versions"]
        assert len(listed) == 1
        assert listed[0]["version_label"] == "v1"
        assert listed[0]["is_enabled"] is True

        # download.zip:content-type + 文件名 ASCII 安全(沿用旧回归断言)
        z = c.get("/api/mcp/loop-skill-bundle/download.zip")
        assert z.status_code == 200
        assert z.headers["content-type"] == "application/zip"
        cd = z.headers["content-disposition"]
        assert 'filename="geo-loop-skills-' in cd and ".zip" in cd
        z.headers["content-disposition"].encode("latin-1")
        z.headers["x-bundle-version"].encode("latin-1")

        # install-payload(MCP token 路由)同样读官方 skill,version 查询参数被忽略
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        pr = c.get(
            "/api/mcp/loop-skill-bundle/install-payload",
            params={"version": "查无此版-应被忽略"},
            headers={"X-MCP-Token": "secret"},
        )
        assert pr.status_code == 200
        body = pr.json()
        assert body["ok"] is True
        assert any(f["path"] == "commands/goal.md" for f in body["data"]["files"])
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_old_write_endpoints_are_removed(monkeypatch):
    """旧写端点(上传/启用/删除/按 id 下载)已下线——router.py 里对应的处理函数已删除。

    实际状态码取决于 main.py 的 SPA 兜底路由 `@app.get("/{full_path:path}")`
    (挂在所有 API 路由之后,对任何路径都有 path 匹配,但只注册了 GET):
    - `/loop-skill-bundle/versions` 路径本身还挂着 GET(list_bundle_versions 只读列表别名保留),
      所以 POST 同一路径命中 405(方法不允许)。
    - `/versions/1/enable`(POST)、`/versions/1`(DELETE)——没有任何具名路由匹配这两个路径,
      但 SPA 兜底路由按 path 模式仍会匹配(它接受任意 path),只是方法只登记了 GET,
      于是 Starlette 按「path 匹配、method 不匹配」判成 405,而不是 404。
    - `/versions/1/download.zip`(GET)——方法匹配 SPA 兜底路由的 GET,处理函数才真正执行,
      内部判断 full_path 以 `api/` 开头显式抛 404——这个才是货真价实的「路径不存在」404。
    """
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    c = test_app.client
    try:
        assert (
            c.post(
                "/api/mcp/loop-skill-bundle/versions",
                files={"file": ("bundle.zip", _make_zip(_valid_files()), "application/zip")},
                data={"version_label": "v-e2e"},
            ).status_code
            == 405
        )
        assert c.post("/api/mcp/loop-skill-bundle/versions/1/enable").status_code == 405
        assert c.delete("/api/mcp/loop-skill-bundle/versions/1").status_code == 405
        assert c.get("/api/mcp/loop-skill-bundle/versions/1/download.zip").status_code == 404
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_install_loop_skills_tool_reads_official_skill(monkeypatch):
    """install_loop_skills() 现在打 /api/mcp/skills/goal/install-payload,读官方
    skill 当前版本;`version` 参数保留仅为签名兼容,已被忽略。"""
    import asyncio

    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import skill_service as svc

        with SessionLocal() as db:
            sk, _version_row = svc.create_version(
                db,
                entries=[("skills/geo-goal-orchestrator/SKILL.md", b"orchestrator content")],
                name="/goal loop skills",
                uploaded_by=None,
            )
            sk.slug = "goal"
            sk.is_official = True
            db.commit()

        # 用 monkeypatch 把工具内部 async _aget 换成 async fake(直接打测试 client,带 MCP token 语义)。
        # 注意:install_loop_skills 内部 `await _aget(...)`,因此这里必须返回 coroutine,
        # 否则 `await dict` 会抛 TypeError(计划 Step 1 原稿用的是同步 fake_aget,async monkeypatch 不稳)。
        import server.mcp.tools.action as action

        async def fake_aget(path, *, params=None):
            # 直接调后端 install-payload(测试 client 已带 admin,MCP 路由需 token)
            monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
            from server.app.core import config

            config.get_settings.cache_clear()
            r = test_app.client.get(path, params=params or {}, headers={"X-MCP-Token": "secret"})
            return {"ok": True, "data": r.json(), "error": None}

        monkeypatch.setattr(action, "_aget", fake_aget)

        # 传 version 也应被忽略,一律读官方 skill 当前版本
        out = asyncio.run(action.install_loop_skills(version="some-old-label"))
        assert out["ok"] is True
        assert out["data"]["version"] == "v1"
        assert len(out["data"]["files"]) == 1
        assert out["data"]["files"][0]["path"] == "skills/geo-goal-orchestrator/SKILL.md"
    finally:
        test_app.cleanup()
