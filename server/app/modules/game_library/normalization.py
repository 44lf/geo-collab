"""Lightweight game-name normalization shared by game-library import paths."""

from __future__ import annotations

import re

_GAME_PREFIX_RE = re.compile(r"^游戏[0-9一二三四五六七八九十百]+、\s*")
_BRACKET_CHARS = "《》〈〉「」『』\"'“”‘’ \t　"


def normalize_game_name(value: str) -> str:
    """Strip generated heading prefixes, title brackets, quotes, and whitespace."""
    normalized = (value or "").strip()
    normalized = _GAME_PREFIX_RE.sub("", normalized)
    return normalized.strip(_BRACKET_CHARS)
