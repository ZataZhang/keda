"""配置文件读-改-写的领域错误（TOML 写回原语与其调用方共用）。

``config.toml``（机器级）与 ``.kedacode.toml``（仓库级）的写回在
:mod:`backend.infrastructure.config.toml_section_editor` 里完成，但「坏文件」、「这次
操作根本不会生效」与「这次撞上了并发写入」这三类失败必须由入口层（console API / CLI）
捕获并映射成可读失败，不能让它们变成 traceback、500 空壳或一个静默的成功。

本模块放在 ``core/shared/models`` 是因为依赖方向只允许向内：infrastructure 可以
导入它（见 ``hooks/shared/check_architecture.py`` 的白名单），core / engines / api
也在各自的合法方向内使用同一份错误类型——一处定义，两侧都能判读。
"""

from __future__ import annotations


class ConfigEditError(ValueError):
    """配置文件的一次读-改-写被拒绝，且**目标文件未被改动**。"""


class ConfigDocumentInvalidError(ConfigEditError):
    """磁盘上的现有配置不是合法 TOML。

    此时任何写回都不该发生：盲目覆盖会把用户手写的注释与其余段落一起抹掉，
    所以先修文件，再重试写操作。
    """


class ConfigKeyMissingError(ConfigEditError):
    """要求「必须已存在」的目标表或键在**目标层**不存在。

    删除一个本层根本没声明的键（例如在仓库范围删除只有全局层声明的预设、或解绑
    一个当前层没有绑定的阶段）不会改动任何配置，因此它不能报成功——否则调用方
    以为设置已生效，实际仍由上一层的值生效。
    """


class ConfigLockBusyError(ConfigEditError):
    """目标配置的写锁在**有界等待**内没有拿到，另一个写入者仍持有它。

    锁按目标文件派生且有超时上限，所以这里既不是"卡死"也不是"配置坏了"：文件一字
    未动，重试即可。之所以要单独成一种错误：调用方需要区分"这次操作不合法"（改请求）
    和"这次撞上了并发写入"（原样重试），后者在 CLI 侧映射成 ``CONFLICT(5)``、在
    console API 侧映射成 ``409``，而不是被吞成一次通用失败。
    """
