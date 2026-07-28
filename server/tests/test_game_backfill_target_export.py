from types import SimpleNamespace

import pytest

from server.scripts import export_game_backfill_targets


def test_build_target_manifest_maps_rows_and_icon_local():
    rows = [
        SimpleNamespace(
            id=1,
            name="原神",
            stock_category_id=10,
            icon_url="/api/stock-images/123/file",
        ),
        SimpleNamespace(
            id=2,
            name="王者荣耀",
            stock_category_id=11,
            icon_url="https://remote.example/icon.png",
        ),
    ]

    manifest = export_game_backfill_targets.build_manifest(
        rows,
        bundle_id="stability-001",
        max_screenshots=3,
        delay_seconds=30,
    )

    assert manifest["schema_version"] == 1
    assert manifest["bundle_id"] == "stability-001"
    assert manifest["targets"] == [
        {
            "target_game_id": 1,
            "name": "原神",
            "category_id": 10,
            "icon_local": True,
        },
        {
            "target_game_id": 2,
            "name": "王者荣耀",
            "category_id": 11,
            "icon_local": False,
        },
    ]


def test_build_target_manifest_rejects_out_of_range_size():
    rows = [
        SimpleNamespace(id=i, name=f"game-{i}", stock_category_id=None, icon_url=None)
        for i in range(1, 22)
    ]

    with pytest.raises(ValueError, match="between 1 and 20"):
        export_game_backfill_targets.build_manifest(
            rows,
            bundle_id="too-many",
            max_screenshots=3,
            delay_seconds=30,
        )
