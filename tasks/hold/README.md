# Hold（暂缓 PRD）

`tasks/hold/` 存放**已写好但暂缓执行**的 PRD：需求成立、结构完整，但当前没有紧迫场景或落点，主动从待执行队列里移出。

与相邻目录的区别：

| 目录 | 含义 |
|---|---|
| `tasks/inbox/` | 还没成形的随手想法 |
| `tasks/pending/` | 待执行 / 进行中 |
| `tasks/hold/` | **已成形但暂缓**，不参与当前排期 |
| `tasks/archive/` | 已交付（代码落地） |

## 约定

- **不进任何自动看板**：`just prd status`、PRD 领锁（`just prd start` / `just implement`）、验收 hook 都只识别 `tasks/pending/` 与 `tasks/archive/`，本目录被有意排除——暂缓的东西不该出现在「待办」或「等验收」里。
- **不是归档**：这里的 PRD 没有交付、没有证据包，`git log` 里也没有对应实现。不要把这里当作已完成的记录。
- **移入**：从 `tasks/pending/` 用 `git mv` 移来，并在 PRD 顶部补一行可 grep 的 Hold 说明（原因 + 日期），在 §14 Change Log 记一条 `Type: doc`。
- **移出**：当它变得可排期时，`git mv` 回 `tasks/pending/`，把横幅恢复为 `⬜ 未开工`，并追加一条 Change Log 说明解除暂缓的原因。
- **删除**：如果确认需求不再成立，直接删除，不必留痕——档案价值由 §13 Decision Log 与 git 历史承载。
