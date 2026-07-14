"""根因收割：chromium OS 级收割器 + 配置开关（纯逻辑、无 DB、collection-safe）。"""

from __future__ import annotations

from server.app.core.config import Settings


def test_harvest_settings_defaults():
    fields = Settings.model_fields
    assert fields["publish_harvest_enabled"].default is True
    assert fields["publish_harvest_rejoin_seconds"].default == 5.0
