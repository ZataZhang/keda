"""Console 启动托管 runner 的 per-run 选项（与 ``kc run`` 同名旗标一一对应）。

网页「开始此 PRD」的高级选项不是第二套执行语义：每一项都映射到 ``kc run`` 的
一个真实旗标，最终由被启动的子进程按 CLI 既有路径解析。因此本模块只负责两件事——

1. 把选项折算成 argv 片段（:meth:`RunnerLaunchOptions.cli_flags`），全部缺省时
   返回**空元组**，使「不带选项」的启动链路与选项引入前逐字节一致；
2. 用与 CLI 相同的规则拒绝非法组合（互斥的发布档位、依赖 ``preset`` 的覆盖旗标），
   让错误在发起端就暴露成一次明确的拒绝，而不是在子进程里静默降级。

本模块只掌握**选项自身**的规则；CLI 里还有一组取决于**启动目标**的规则（``direct_pr``
对带 PRD 锚点的 Issue 是硬性用法拒绝、发布档位旗标不能配 ``--all-ready``）。这些规则
由知道自己目标的调用方在同一发起端拒绝（见
:func:`backend.core.use_cases.backlog_actions.start_prd`），否则网页只会得到一个
「已开始」提示加上一条立刻以用法错误退出的托管进程。
"""

from __future__ import annotations

from dataclasses import dataclass

#: ``--agent`` 的路由别名默认值：与 CLI 缺省一致，等价于不传该旗标。
DEFAULT_RUN_AGENT = "auto"


class RunnerLaunchOptionsError(ValueError):
    """启动选项组合非法（规则与 ``kc run`` 同名旗标的校验一致）。"""


def _normalized_flag(value: str | None) -> str | None:
    """把旗标取值去空白归一；空串与纯空白视为「未给出」。"""
    if value is None:
        return None
    return value.strip() or None


@dataclass(frozen=True)
class RunnerLaunchOptions:
    """一次 runner 启动的可选旗标集合，默认值即「不加任何旗标」。

    Attributes:
        fast_merge: ``--fast-merge``：本轮跳过合并前的独立验证门禁。
        direct_pr: ``--direct-pr``：本轮只保留机械步骤，直接开 Draft PR。CLI 只允许
            它作用于**无 PRD 锚点**的 Issue，目标由调用方判定并拒绝。
        agent: ``--agent`` 的取值；``None``、空串或 ``"auto"`` 表示不传该旗标。
        preset: ``--preset``：把本轮生命周期阶段锚定到命名模型预设。
        model: ``--model``：预设字段的单次覆盖，必须与 ``preset`` 同时给出。
        reasoning_effort: ``--reasoning-effort``：同上。
    """

    fast_merge: bool = False
    direct_pr: bool = False
    agent: str | None = None
    preset: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def _flag_values(self) -> dict[str, str | None]:
        """返回四个文本旗标归一后的取值（键为旗标名，供两处复用）。"""
        return {
            "agent": _normalized_flag(self.agent),
            "preset": _normalized_flag(self.preset),
            "model": _normalized_flag(self.model),
            "reasoning_effort": _normalized_flag(self.reasoning_effort),
        }

    def _emittable_flag_values(self) -> dict[str, str | None]:
        """真正会产生 argv 的取值：``--agent`` 的路由别名默认值按「未给出」处理。

        :meth:`cli_flags` 与 :meth:`is_default` 共用这一份判定，避免「``auto`` 到底算
        不算带选项」在两个方法里各说一套。
        """
        flag_values = self._flag_values()
        if flag_values["agent"] == DEFAULT_RUN_AGENT:
            flag_values["agent"] = None
        return flag_values

    def is_default(self) -> bool:
        """是否没有任何选项生效（与 :meth:`cli_flags` 返回空元组同判据）。"""
        return not (
            self.fast_merge or self.direct_pr or any(self._emittable_flag_values().values())
        )

    def cli_flags(self) -> tuple[str, ...]:
        """构建与 CLI 同名旗标等价的 argv 片段。

        Returns:
            形如 ``("--fast-merge", "--agent", "codex", "--preset", "strong")``
            的元组；全部缺省时为空元组。

        Raises:
            RunnerLaunchOptionsError: 档位互斥、覆盖旗标缺少 ``preset``，或取值
                不是合法的单一 token。
        """
        if self.fast_merge and self.direct_pr:
            raise RunnerLaunchOptionsError(
                "fast_merge and direct_pr are mutually exclusive; direct_pr already "
                "includes everything fast_merge skips, so combining them is ambiguous "
                "rather than stronger."
            )
        flag_values = self._emittable_flag_values()
        if (flag_values["model"] or flag_values["reasoning_effort"]) and not flag_values["preset"]:
            raise RunnerLaunchOptionsError(
                "model / reasoning_effort require preset: they are one-shot overrides "
                "of a preset's same-name fields, not standalone switches."
            )

        flags: list[str] = []
        if self.fast_merge:
            flags.append("--fast-merge")
        if self.direct_pr:
            flags.append("--direct-pr")
        for flag_name, flag_value in flag_values.items():
            if not flag_value:
                continue
            flags.extend([f"--{_cli_option_name(flag_name)}", _single_token(flag_value, flag_name)])
        return tuple(flags)


def _cli_option_name(field_name: str) -> str:
    """把数据类字段名转成对应的 CLI 旗标拼写（``reasoning_effort`` → ``reasoning-effort``）。"""
    return field_name.replace("_", "-")


def _single_token(flag_value: str, field_name: str) -> str:
    """把旗标取值收敛成单个 argv token。

    argv 是列表而非 shell 字符串，取值本身无法注入命令；这里仍拒绝带空白或以
    ``-`` 开头的取值，好让「选项拼错」表现为一次可理解的 4xx，而不是让子进程
    把它当成另一个旗标解析后报出难以定位的用法错误。
    """
    if flag_value.startswith("-") or any(character.isspace() for character in flag_value):
        raise RunnerLaunchOptionsError(
            f"'{field_name}' must be a single option value without leading dashes: {flag_value!r}."
        )
    return flag_value


__all__ = [
    "DEFAULT_RUN_AGENT",
    "RunnerLaunchOptions",
    "RunnerLaunchOptionsError",
]
