#!/usr/bin/env python3
"""RV fixture 用的 prd 契约解析桩：仅为满足 daemon 启动接线预检。

真实 prd skill 的 ``scripts/prd_contract.py`` 承载 PRD 格式解析实现；RV 场景不
生成/解析 PRD（就绪 Issue 直接带 ``agent/ready`` 入队），因此这里只提供一个可
导入的最小占位，确保 ``ensure_prd_machine_contract_available`` 的文件存在性检查
通过。绝不进入代码 diff。
"""

MACHINE_CONTRACT_VERSION = 5


def main() -> None:  # pragma: no cover - fixture 桩，不被 RV 场景调用
    raise SystemExit("prd_contract fixture stub: not invoked by RV scenarios")


if __name__ == "__main__":
    main()
