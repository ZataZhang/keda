"""Backend application entrypoint.

Runs the FastAPI app for local development (``python -m backend.main``).
The CLI entry point lives in ``backend.api.cli_entry:main`` and is registered via
``[project.scripts]`` in ``pyproject.toml``.
"""

from __future__ import annotations

import os


def main() -> None:
    """Run the FastAPI app on loopback by default.

    The panel has no authentication: ``/api/auth/login`` trusts the local
    single-user boundary. Binding it to ``0.0.0.0`` would expose runner,
    repository and label write operations to the whole network, so the
    default host is ``127.0.0.1``; a caller that really wants exposure must
    pass ``HOST`` explicitly and take responsibility for the missing auth.
    """
    import uvicorn

    from backend.infrastructure.executable_path import ensure_standard_executable_path

    # 直接以 uvicorn 启动时也要补齐 PATH：GUI / launchd 上下文没有 Homebrew 目录。
    ensure_standard_executable_path()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("backend.api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
