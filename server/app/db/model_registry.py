"""Register every ORM model for standalone processes.

The Web application imports routers that happen to register the complete SQLAlchemy
metadata.  Standalone workers do not have that side effect, so they must call this
function before opening a session or flushing business rows.
"""

from __future__ import annotations


def register_orm_models() -> None:
    """Import all ORM modules into the shared ``Base.metadata`` registry."""

    import server.app.modules.accounts.models  # noqa: F401
    import server.app.modules.ai_generation.models  # noqa: F401
    import server.app.modules.ai_models.models  # noqa: F401
    import server.app.modules.articles.models  # noqa: F401
    import server.app.modules.audit.models  # noqa: F401
    import server.app.modules.auto_review.models  # noqa: F401
    import server.app.modules.collector.models  # noqa: F401
    import server.app.modules.game_library.models  # noqa: F401
    import server.app.modules.image_library.models  # noqa: F401
    import server.app.modules.loop_skills.models  # noqa: F401
    import server.app.modules.pipelines.models  # noqa: F401
    import server.app.modules.prompt_templates.models  # noqa: F401
    import server.app.modules.quality_reference.models  # noqa: F401
    import server.app.modules.report.models  # noqa: F401
    import server.app.modules.skills.models  # noqa: F401
    import server.app.modules.system.models  # noqa: F401
    import server.app.modules.tasks.models  # noqa: F401
    import server.app.modules.video.models  # noqa: F401
    import server.app.modules.xhs_cards.models  # noqa: F401
