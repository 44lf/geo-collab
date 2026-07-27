"""Release A 回归门禁。"""

import ast
from pathlib import Path


def test_pipeline_does_not_import_scheme_modules():
    roots = [
        Path("server/app/modules/pipelines/nodes/ai_compose.py"),
        Path("server/app/modules/pipelines/nodes/ai_generate_node.py"),
    ]
    forbidden = {"scheme_router", "scheme_service", "scheme_executor"}
    for path in roots:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(any(part in name for part in forbidden) for name in imported)
