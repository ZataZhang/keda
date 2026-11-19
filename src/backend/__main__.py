"""Entry point for ``python -m backend``.

Reuses :func:`backend.main.main` so the bind host / port rules live in one
place; the loopback default is what keeps the unauthenticated panel local.
"""

from __future__ import annotations

from backend.main import main

if __name__ == "__main__":
    main()
