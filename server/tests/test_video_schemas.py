from __future__ import annotations

import pytest
from pydantic import ValidationError as PydValidationError

from server.app.modules.video.schemas import Shot, Storyboard


def test_storyboard_minimal_ok():
    sb = Storyboard(title="标题", shots=[Shot(subtitle="第一段", narration="第一段口播")])
    assert sb.aspect_ratio == "9:16"
    assert sb.bgm == "default"
    assert sb.description == ""
    assert sb.tags == []
    assert len(sb.shots) == 1


def test_storyboard_rejects_empty_shots():
    with pytest.raises(PydValidationError):
        Storyboard(title="标题", shots=[])


def test_storyboard_rejects_bad_aspect_ratio():
    with pytest.raises(PydValidationError):
        Storyboard(title="标题", aspect_ratio="1:1", shots=[Shot(subtitle="a", narration="a")])


def test_shot_asset_id_optional():
    s = Shot(subtitle="a", narration="a")
    assert s.asset_id is None
    assert s.duration_hint is None
