"""通过 kc console 的 HTTP 入口检查 Stats 静态页面与引用 bundle。"""

from __future__ import annotations

import re
import sys
from urllib.request import urlopen


def main() -> None:
    """读取真实 kc console 路由，并确认 Stats bundle 含新增区块文案。"""
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8763"
    with urlopen(f"{base_url}/app/stats") as page_response:
        page_html = page_response.read().decode("utf-8")
        print(f"page_status={page_response.status}")
        print(f"page_final_url={page_response.url}")

    bundle_paths = sorted(set(re.findall(r"/_next/static/chunks/[^\"']+?\.js", page_html)))
    assert bundle_paths, "Stats page did not reference a JavaScript bundle"
    has_agent_performance_section = False
    for bundle_path in bundle_paths:
        with urlopen(f"{base_url}{bundle_path}") as bundle_response:
            bundle_text = bundle_response.read().decode("utf-8", errors="replace")
            print(f"bundle_status={bundle_response.status} path={bundle_path}")
            assert bundle_response.status == 200
            if "Agent 与预设执行表现" in bundle_text:
                has_agent_performance_section = True
                print(f"bundle_contains_agent_performance=True path={bundle_path}")

    assert has_agent_performance_section, "Stats bundles are missing the Agent performance section"


if __name__ == "__main__":
    main()
