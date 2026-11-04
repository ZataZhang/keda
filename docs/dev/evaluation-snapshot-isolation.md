# 评测候选快照隔离

v2 suite 的 `held_out` 和 `candidate_hidden` 路径用于防止候选 Agent 直接读取判分材料或已知答案。隔离必须覆盖工作树和候选 Git 历史，否则候选仍能通过 Git 还原已删除的文件。

## 创建候选仓库

执行器解析冻结的来源 commit 和完整 tree hash，再用临时 Git index 导出基线。创建候选仓库前，先从该 index 中剔除当前 case 声明的 `held_out` 与 `candidate_hidden` 路径。新的仓库只包含剩余基线文件，只有一个根提交，也不共享来源仓库的 Git 对象库。

因此隐藏路径既不在候选工作树，也不在候选提交中；`git show HEAD:<path>` 无法读取，`git checkout -- .` 也无法恢复。run 和结果仍记录来源 commit 的完整 tree hash，候选仓库的 tree hash 则对应过滤后的可见文件。

## 候选结束后的判分顺序

1. 恢复基线 `pyproject.toml` 与 `uv.lock`。
2. 删除候选可能新建的隐藏路径，再收集代码 diff，避免把判分材料写进评审 payload。
3. 从冻结来源 commit 恢复 `held_out` 和 `candidate_hidden` 的基线版本。
4. 将 suite 资料中的 `held_out` 测试文件覆盖到快照，运行固定 check recipe。

Agent 执行失败时不运行判分，但在记录已有代码 diff 前仍会删除候选创建的隐藏路径。检查完成后，执行器删除整个候选仓库。

## 保证范围

`candidate_hidden` 只隐藏 suite 明确列出的基线文件，不是通用文件系统沙箱。公开历史、网络资料或模型已知信息仍可能暴露任务内容，因此 holdout 仍用于衡量相对变化，不代表保密评测。

回归测试会确认隐藏路径不在工作树与 `HEAD`、来源仓库的答案 blob 不可从候选仓库读取、`git checkout -- .` 无法恢复隐藏文件，并且生成的评审 diff 不包含这些路径。
