import datetime as dt

import pytest

from server.app.modules.pipelines.service import validate_agent_fields
from server.app.shared.errors import ValidationError


def test_validate_ok_minimal():
    validate_agent_fields(
        name="智能体",
        type="general",
        tags=[],
        schedule_kind="none",
        schedule_minute=None,
        schedule_hour=None,
        schedule_weekday=None,
        window_start=None,
        window_end=None,
    )


def test_validate_name_too_long():
    with pytest.raises(ValidationError):
        validate_agent_fields(
            name="x" * 51,
            type="general",
            tags=[],
            schedule_kind="none",
            schedule_minute=None,
            schedule_hour=None,
            schedule_weekday=None,
            window_start=None,
            window_end=None,
        )


def test_validate_bad_type_and_tags():
    with pytest.raises(ValidationError):
        validate_agent_fields(
            name="a",
            type="weird",
            tags=[],
            schedule_kind="none",
            schedule_minute=None,
            schedule_hour=None,
            schedule_weekday=None,
            window_start=None,
            window_end=None,
        )
    with pytest.raises(ValidationError):
        validate_agent_fields(
            name="a",
            type="general",
            tags=["1", "2", "3", "4", "5", "6"],
            schedule_kind="none",
            schedule_minute=None,
            schedule_hour=None,
            schedule_weekday=None,
            window_start=None,
            window_end=None,
        )


def test_validate_schedule_consistency():
    # daily 缺 hour
    with pytest.raises(ValidationError):
        validate_agent_fields(
            name="a",
            type="general",
            tags=[],
            schedule_kind="daily",
            schedule_minute=30,
            schedule_hour=None,
            schedule_weekday=None,
            window_start=None,
            window_end=None,
        )
    # weekly 全齐 OK
    validate_agent_fields(
        name="a",
        type="general",
        tags=[],
        schedule_kind="weekly",
        schedule_minute=30,
        schedule_hour=9,
        schedule_weekday=0,
        window_start=None,
        window_end=None,
    )


def test_validate_window_order():
    with pytest.raises(ValidationError):
        validate_agent_fields(
            name="a",
            type="general",
            tags=[],
            schedule_kind="none",
            schedule_minute=None,
            schedule_hour=None,
            schedule_weekday=None,
            window_start=dt.time(20, 0),
            window_end=dt.time(8, 0),
        )
