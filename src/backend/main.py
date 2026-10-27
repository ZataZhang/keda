"""Backend application entrypoint.

Supports both FastAPI (``uvicorn backend.main:app``) and CLI (``kc``) modes.
The CLI entry point lives in ``backend.api.cli:main`` and is registered via
``[project.scripts]`` in ``pyproject.toml``.
"""

from __future__ import annotations

import os


def main() -> None:
    """Run the backend entrypoint placeholder."""
    import uvicorn

    from backend.infrastructure.executable_path import ensure_standard_executable_path

    # 直接以 uvicorn 启动时也要补齐 PATH：GUI / launchd 上下文没有 Homebrew 目录。
    ensure_standard_executable_path()
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("backend.api.app:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
