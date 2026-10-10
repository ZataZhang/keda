"""smoke：验证 fixture → fake gh → kc backlog advance 全链路能打通。"""

from __future__ import annotations

import sys

from fixture_lib import Fixture

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))


def main() -> int:
    fx = Fixture("smoke", capacity=10)
    result = fx.run_kc(["backlog", "advance", "--dry-run", "--repo-id", "rv-fixture"])
    print("=== exit", result.returncode)
    print("--- stdout ---")
    print(result.stdout)
    print("--- stderr (tail) ---")
    print("\n".join(result.stderr.splitlines()[-40:]))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
