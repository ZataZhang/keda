# rv-4 附：旧样本导出比对（"改动前"与"改动后"）

比对的两份导出都是对同一份真实 `console.db`、同一个仓库、带 `--with-gh` 的完整导出：

- 改动前：快照功能的导出器改动之前保存的一份（本地临时文件，sha256 前 16 位 `c6a2959029a38080`）。
- 改动后：本功能完成后、编辑 Issue #306 正文期间导出的一份（sha256 前 16 位 `d0c5e4415f7b35ef`）。

两份导出文件在本机临时目录里，不在仓库内；下表是逐样本比对的结果，比较字段为：输入来源、标题、正文、提交终点、起点、是否已解决、规模档位。

共同样本 **74** 个（不含有快照的 #306）：**73** 个一致，**1** 个不同。

| 样本 | 结果 | 原因 |
|---|---|---|
| keda#issue-156 | 一致 |  |
| keda#issue-157 | 一致 |  |
| keda#issue-159 | 一致 |  |
| keda#issue-193 | 一致 |  |
| keda#issue-194 | 一致 |  |
| keda#issue-196 | 一致 |  |
| keda#issue-197 | 一致 |  |
| keda#issue-207 | 一致 |  |
| keda#issue-215 | 一致 |  |
| keda#issue-216 | 一致 |  |
| keda#issue-218 | 一致 |  |
| keda#issue-219 | 一致 |  |
| keda#issue-220 | 一致 |  |
| keda#issue-223 | 一致 |  |
| keda#issue-228 | 一致 |  |
| keda#issue-229 | 一致 |  |
| keda#issue-230 | 一致 |  |
| keda#issue-234 | 一致 |  |
| keda#issue-235 | 一致 |  |
| keda#issue-239 | 一致 |  |
| keda#issue-242 | 一致 |  |
| keda#issue-245 | 一致 |  |
| keda#issue-246 | 一致 |  |
| keda#issue-247 | 一致 |  |
| keda#issue-248 | 一致 |  |
| keda#issue-250 | 一致 |  |
| keda#issue-256 | 一致 |  |
| keda#issue-258 | 一致 |  |
| keda#issue-262 | 一致 |  |
| keda#issue-263 | 一致 |  |
| keda#issue-264 | 一致 |  |
| keda#issue-265 | 一致 |  |
| keda#issue-266 | 一致 |  |
| keda#issue-273 | 一致 |  |
| keda#issue-274 | 一致 |  |
| keda#issue-278 | 一致 |  |
| keda#issue-282 | 一致 |  |
| keda#issue-283 | 一致 |  |
| keda#issue-284 | 一致 |  |
| keda#issue-288 | 一致 |  |
| keda#issue-290 | 一致 |  |
| keda#issue-291 | 一致 |  |
| keda#issue-293 | **不同** | 该 Issue 的 PR #301 在两次导出之间由 OPEN 变为 MERGED（提交 5→6 个，resolved False→True），与快照功能无关 |
| keda#issue-294 | 一致 |  |
| keda#issue-297 | 一致 |  |
| keda#issue-303 | 一致 |  |
| keda#issue-53 | 一致 |  |
| keda#issue-71 | 一致 |  |
| keda#issue-72 | 一致 |  |
| keda#issue-73 | 一致 |  |
| keda#issue-84 | 一致 |  |
| keda#issue-87 | 一致 |  |
| keda#issue-92 | 一致 |  |
| keda#issue-97 | 一致 |  |
| keda-main#issue-101 | 一致 |  |
| keda-main#issue-102 | 一致 |  |
| keda-main#issue-103 | 一致 |  |
| keda-main#issue-104 | 一致 |  |
| keda-main#issue-105 | 一致 |  |
| keda-main#issue-110 | 一致 |  |
| keda-main#issue-111 | 一致 |  |
| keda-main#issue-115 | 一致 |  |
| keda-main#issue-116 | 一致 |  |
| keda-main#issue-119 | 一致 |  |
| keda-main#issue-120 | 一致 |  |
| keda-main#issue-124 | 一致 |  |
| keda-main#issue-126 | 一致 |  |
| keda-main#issue-128 | 一致 |  |
| keda-main#issue-144 | 一致 |  |
| keda-main#issue-175 | 一致 |  |
| keda-main#issue-193 | 一致 |  |
| keda-main#issue-82 | 一致 |  |
| keda-main#issue-85 | 一致 |  |
| keda-main#issue-86 | 一致 |  |
