# Human Review Checklist — P1-FEAT-20260930-141135-iar-agent-machine-contract

> 这是把 PRD §9.2 里仍**未勾的 3 项 Human-Confirmed** 汇总成一页的人审清单。你只需读这一份，不必翻 PRD、证据目录或对话记录。
> 打开方式：`just prd review tasks/archive/P1-FEAT-20260930-141135-iar-agent-machine-contract.md`。
>
> 交付侧状态：独立 verifier（qoder）**PASS / risk=green**；全量门禁 **2995 passed / 1 skipped**（唯一 skip 为既有 alembic 空迁移守卫）；rv-1…rv-7 在最终工作树全量复采 7/7 PASS；`just lint --full` 与 `uv run mkdocs build --strict` 全绿。
> 术语：**机读契约** = `iar` CLI 对机器消费者的输出/退出码承诺；**envelope（信封）** = JSON 模式失败时 stderr 上的五字段结构化错误（`error/message/suggestion/retryable/exit_code`）；**jq** = 命令行 JSON 解析器，这里充当"输出是否纯净"的裁判；**负控** = 故意让检查变红的对照实验，证明检查本身有判别力、不是恒真。

**怎么回复**：对每一项给一个结论即可——`同意` / `有差异：<说明>`。全部同意即视为人审通过。

---

## 审阅顺序总表

| # | 你要决定的事 | 判错的后果 | 结论 |
|---|---|---|---|
| 1 | 机器输出必须显式声明（`--json` / `--output json`），默认永远是人类表格，不做"非 TTY 自动切 JSON" | 若其实做了自动切换，所有把 `iar …` 接进管道的既有脚本会静默拿到 JSON，大面积假死且难排查 | ☐ 同意 ☐ 有差异 |
| 2 | 失败退出码从"几乎全是 1"改为按类别区分（3 未找到 / 4 无权限 / 5 冲突 / 10 dry-run 通过，1 兜底） | 若码表没真正接上，六类会塌成同一个码，agent 又回到"parse stderr 猜原因" | ☐ 同意 ☐ 有差异 |
| 3 | §9.1 两个呈递物（rv-1 纯 JSON、rv-3 语义退出码）已亲眼过目 | —— | ☐ 同意 ☐ 有差异 |

---

## 1. 决策一 · 机器输出必须显式声明、默认人类输出

**你要决定**：接受"机器模式必须显式传 `--json` / `--output json`，默认永远是给人看的表格，**不做**非 TTY 自动切 JSON"。判错的话——如果实现其实做了自动切换，所有把 `iar issue list | grep …` 这类管道接进脚本的地方会在无人察觉的情况下改吃 JSON，运维脚本大面积静默假死。

> **PRD 原文（§9.2 Human-Confirmed item 1）**：
> 决策一（JSON 显式声明、默认人类输出、不做非 TTY 自动切换）已确认

**大白话**：同一条命令，不加旗标时输出和改动前一字不差（表格、`--help` 文本都冻结）；只有显式要机器输出时，stdout 才变成可以直接喂给 `jq` 的纯 JSON。

**证据（关键行逐字摘录）**：
- 零回归是双侧快照测出来的（`rv-6-zero-regression.txt`）：8 条只读命令在"改造前基线树"与"当前树"各跑一次，stdout+stderr 逐字节 `cmp` 相同——`ok [覆盖面] 8 条只读命令的人类输出全部逐字节一致`；旧写法 `iar issue list --output json` 同样逐字节不变（`ok [out-issue-list-output-json] stdout+stderr 逐字节相同且 exit=0`）。
- 检查有判别力（`rv-6-negative-control.txt`）：把比对方向反过来，实测丢失 171 个词元——`ok [判别力] 反向比较丢失 171 个词元 ⇒ 正向的「0 丢失」是真的测出来的，不是恒真`。
- 机器模式确实纯（`rv-1-issue-list-json.txt`）：`ok [stdout 是 JSON 数组] stdout satisfies jq -e type=="array"`；负控（`rv-1-negative-control.txt`）：同命令去掉 `--json` 后 stdout 被 jq 拒绝、首行是 `┏━━━` 表格边框——"纯 JSON"只属于显式声明的机器模式。

**可选复跑**（不跑也能判）：
```bash
uv run iar issue list --repo-id keda | head -3        # 期望：Rich 表格边框，不是 JSON
uv run iar issue list --json --repo-id keda | jq -e 'type=="array"'   # 期望：true
```

**结论**：☐ 同意 ☐ 有差异：__________

---

## 2. 决策二 · 语义退出码 3/4/5/10、1 兜底

**你要决定**：接受"引入语义退出码是对外契约的行为变更"——"未找到"返回 `3`、"无权限"返回 `4`、"冲突"返回 `5`、`iar run --dry-run` 校验通过返回 `10`，其余失败仍返回 `1` 兜底；只判断"是否非零"的脚本不受影响。判错的话——如果码表没真正接上，六类场景会全塌成同一个码，agent 仍无法区分"该换仓库""该重试""该改命令"。

> **PRD 原文（§9.2 Human-Confirmed item 2）**：
> 决策二（引入语义退出码 `3/4/5/10`、`1` 兜底、码表文档化）已确认

**大白话**：失败时先看 `$?` 就知道失败类别，不用再 parse stderr 的自然语言；stderr 上还有一条结构化建议直接告诉你下一条命令。

**证据（关键行逐字摘录）**：
- 六码两两不同（`rv-3-exit-codes.txt`）：`ok [not_found 退出码] exit=3`，且一行汇总 `not_found=3 usage=2 permission=4 conflict=5 dry_run=10 general=1`（脚本另断言 `sort -u | wc -l == 6`，码表没接入会全塌成 1 而变红）。
- 每个失败类都给了能跑的下一条命令：not_found → stderr 含 `next: iar registry list`；permission_denied → `gh auth login -h github.com`；conflict → `iar workflow install preview --force`（该建议被原样执行验证成功）。
- "3 是本次引入的语义"是基线对照测出来的（`rv-3-negative-control.txt`）：`baseline_exit_for_not_found=1（改造前）vs 3（改造后）`——改造前提交树跑同一命令 exit=1。
- 不污染既有脚本：人类模式 `iar run --dry-run`（不带 `--json`）仍 exit 0，`10` 只在机器模式生效（负控第三段实测）。
- 码表已文档化且同源：`iar schema --json` 的 `exit_codes.values` 覆盖 `0/1/2/3/4/5/10`（rv-5），skill 与 `docs/` 的码表与运行时导出集合一一对应（rv-7 集合相等校验）。

**可选复跑**（不跑也能判）：
```bash
bash -c 'uv run iar logs --repo-id does-not-exist --issue 1; echo $?'   # 期望：3
bash -c 'uv run iar logs --issue 1 --kind review_daemon; echo $?'       # 期望：2（与 3 可区分）
uv run iar schema --json | jq -c '.exit_codes.values'                    # 期望：7 类码表
```

**结论**：☐ 同意 ☐ 有差异：__________

---

## 3. §9.1 呈递区过目（rv-1 纯 JSON + rv-3 语义退出码）

**你要决定**：亲眼看过两个呈递物——机器模式的 stdout 纯净度与语义退出码的真实终端捕获。这是把前两项决策从"信证据"落到"见过实物"的一步。

> **PRD 原文（§9.2 Human-Confirmed item 3）**：
> §9.1 呈递区各项已亲眼看过（截图/自验，二选一或都做）

**大白话**：打开下面两个文本文件（或粘两行命令进终端），确认看到的东西和承诺的一致。

**证据（呈递物 + 10 秒自检）**：
- rv-1：`.iar/evidence/rv-1-issue-list-json.txt`（`open .iar/evidence/rv-1-issue-list-json.txt`）。自检：搜 `ok [stdout 是 JSON 数组]` 与 `两次独立进程的 stdout 逐字节一致` 两行；负控文件 `rv-1-negative-control.txt` 搜 `stdout rejected by jq`。
- rv-3：`.iar/evidence/rv-3-exit-codes.txt`（`open .iar/evidence/rv-3-exit-codes.txt`）。自检：搜 `ok [not_found 退出码] exit=3` 与 `not_found=3 usage=2 permission=4 conflict=5 dry_run=10 general=1`；负控文件 `rv-3-negative-control.txt` 搜 `baseline_exit_for_not_found=1（改造前）vs 3（改造后）`。
- 或复跑：`bash .iar/evidence/scripts/rv1.sh && bash .iar/evidence/scripts/rv3.sh`，两行 `rv-1 PASSED` / `rv-3 PASSED` 即结论。

**结论**：☐ 同意 ☐ 有差异：__________

---

## 附：已由执行侧与 verifier 交叉核验、人默认可跳过的部分

- rv-2（stdout 无日志、消息走 stderr）、rv-4（失败 envelope 五字段 + suggestion 真能跑）、rv-5（schema 同源派生）、rv-6（零回归双侧快照）、rv-7（skill/docs 同步）为 `reviewer: verifier` 项：agent 自验 + 独立 verifier（qoder）复核 PASS，细节见同目录 `*.evidence-report.md` 与 `*.verifier-report.md`。
- 唯一披露的对外收紧：`iar issue list --output bogus` 从"静默回落表格（exit 0）"改为"用法错误（exit 2）"——只影响原先未定义行为的非法取值，PRD §14 已记录。
- 唯一披露的空态语义：`iar logs --json` 在 Issue 尚无输出时 exit 0 + stdout 0 字节，读作"暂无数据"（PRD §12）。
