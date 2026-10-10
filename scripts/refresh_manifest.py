"""刷新 Issue #266 的结构化运行证据清单。"""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    """将本轮逐项运行结果登记到证据 manifest。"""
    evidence_dir = Path(__file__).resolve().parent.parent
    manifest_path = evidence_dir / "evidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items_by_number = {
        manifest_item["item_number"]: manifest_item
        for manifest_item in manifest["items"]
    }

    item_one = items_by_number[1]
    item_one["evidence_files"].append("rv-1-daemon-ceiling.txt")
    item_one["output_summary"] = (
        "本轮重新运行真实 kc daemon：策略 2、容量 10 时，0/1/2 个预置在跑任务对应新认领预算 "
        "2/1/0，峰值不超过 2；未设置策略时继承容量 10 并认领 6 个。负控令 fake gh 将两个真实 "
        "running 标签误报为空，观察到新认领 2 个加原有 2 个，共 4 个 agent/running，超过 ceiling=2。"
        "实现代码仍与原验证 commit 13e52394308b40097010931c3d0880a3f27be42a 一致。"
    )

    item_two = items_by_number[2]
    item_two["evidence_files"].append("rv-2-unified-backfill-start.txt")
    item_two["output_summary"] = (
        "本轮真实 kc backlog advance 覆盖 6 个 pending：未设置继承容量 10 并补入 6、策略 2 时补入 2 "
        "并排队 4、2 个在跑时补入 0；真实 start-global 路由在容量 4 下分别启动 4/2 个并排队 "
        "2/4 个。报告包含 ceiling、source、free_slots，fresh SQLite 读回确认批量请求没有持久化策略。"
        "负控在错误策略 2 下只补入 2，与继承场景应补入 6 的 oracle 冲突并变红。"
    )

    item_three = items_by_number[3]
    item_three["evidence_files"].extend(
        ["rv-3-tree-equivalence.txt", "rv-3-backlog-control-roundtrip.txt"]
    )
    item_three["output_summary"] = (
        "rv-3 的既有真实 console 运行已先以旧 bundle 口径产生页面断言红态，再恢复 bundle 后通过继承、策略、"
        "受限、保存 fresh、恢复继承 fresh 五态；页面 PATCH / fresh GET 与 SQLite 行删除一致，五态截图和 "
        "RESULT: PASS / RV-3 PASSED 均保留。本轮复核证明自成功运行 commit "
        "13e52394308b40097010931c3d0880a3f27be42a 以来 frontend-public/ 与 src/backend/ 无变更。"
        "当前环境的追加重跑未能启动 Chromium，诊断记录在 rv-3-backlog-control-roundtrip.txt，"
        "不作为产品红态或绿态。"
    )
    item_three["risks"] = (
        "本轮 just console-sync 成功，但当前 CUA 没有可用 browser provider，连接 Chrome 被拒绝；"
        "Playwright Chromium 启动报 Permission denied (1100)。本轮追加尝试不计为页面通过。既有真实页面红→绿证据仍有效："
        "自其 commit 起 frontend-public/ 与 src/backend/ 未变，复核结果见 rv-3-tree-equivalence.txt。"
    )
    item_three["stdout_assertions"] = [
        assertion
        for assertion in item_three["stdout_assertions"]
        if assertion["pattern"] != "Permission denied"
    ]

    item_four = items_by_number[4]
    item_four["evidence_files"].append("rv-4-explicit-run.txt")
    item_four["output_summary"] = (
        "本轮负控令错误 ceiling=1 进入定向入口，真实 kc run --issue 未启动目标 Issue，start/end 探针为空并变红。"
        "无注入时策略为 1 且另有 1 个在跑任务，真实 kc run --issue 100 exit=0 并完整完成提交发布；"
        "真实 kc run --all-ready 在 daemon 活跃时仍 exit=5。"
    )

    item_five = items_by_number[5]
    item_five["evidence_files"].append("rv-5-failclosed-recovery.txt")
    item_five["output_summary"] = (
        "本轮负控旧分支令 daemon pass_failed=1 且漏掉 running 恢复候选，断言变红。正常分支中 fake gh 的标签计数错误被 "
        "fail-closed，daemon 未崩溃、无新认领、running 恢复候选被发现且 ready 项保留；在途探针显示 "
        "running_touched=True、pass_failed=0。"
    )

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"更新 manifest: {manifest_path}")
    print(f"检查点数量: {len(manifest['items'])}")


if __name__ == "__main__":
    main()
