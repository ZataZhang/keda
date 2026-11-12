"""主演示录屏（``primary_demo``）：给人看的那一段录屏的数据结构、解析与录制约定。

rv 逐项证据是给 verifier 的；主演示是 PR 证据评论第一屏给人看的录屏。本模块不依赖
``agent_runner_structured_evidence``，由它在解析 manifest 时调用并把格式错误包装成
既有的 manifest 格式错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PRIMARY_DEMO_PROMPT_ZH = (
    "主演示录屏（给人看的那一段，可选但有用户可见行为时应提供）："
    "若 PRD 的人读呈递区有「主演示脚本」，按脚本另录一段 `demo-<slug>.mp4` 放在 `{evidence_dir}/`，"
    "不要拿回归 e2e 用例顺带录的视频充数；每一步在画面上叠加中文字幕说明在演示什么，"
    "结果画面停留 3 秒以上让人看清；某步要证明外部系统行为（真实模型、沙箱、第三方 API）时用真实依赖，"
    "拿不到凭据就照样录但在该步字幕写明「mock，未验证」。"
    "然后在 manifest 顶层写 `primary_demo`：`file` 为录屏纯文件名，`steps` 为数组，"
    "每项含 `caption`（该步字幕）、`result`（画面上实际看到的结果）、"
    "`mock`（该步用了 mock 时为 true）。"
)

PRIMARY_DEMO_PROMPT_EN = (
    "Primary demo recording (the one video for humans; optional, but provide it when "
    "there is user-visible behavior): if the PRD's human review surface has a primary "
    "demo script, record a separate `demo-<slug>.mp4` under `{evidence_dir}/` following "
    "that script — never reuse the video a regression e2e test recorded as a side "
    "effect. Overlay an on-screen caption for every step, hold each result on screen "
    "for at least 3 seconds, and use real dependencies for steps that prove external "
    "behavior (real model, sandbox, third-party API); when credentials are missing, "
    "still record the step but caption it `mock, not verified`. Then add a top-level "
    "`primary_demo` to the manifest: `file` is the bare recording file name and "
    "`steps` is an array whose entries carry `caption` (the step's caption), `result` "
    "(what the screen actually shows) and `mock` (true when that step used a mock)."
)


class PrimaryDemoFormatError(ValueError):
    """``primary_demo`` 字段形状不对；调用方包装成 manifest 格式错误。"""


@dataclass(frozen=True)
class PrimaryDemoStep:
    """主演示录屏中的一步：画面字幕、看到的结果，以及该步是否用了 mock。"""

    caption: str
    result: str
    mock: bool = False


@dataclass(frozen=True)
class PrimaryDemo:
    """给人看的主演示录屏（可选）：PR 证据评论第一屏展示它，rv 逐项证据折叠。"""

    file: str
    steps: tuple[PrimaryDemoStep, ...]


def parse_primary_demo(raw_demo: object, field_text: str) -> PrimaryDemo | None:
    """解析可选的 ``primary_demo``。

    Args:
        raw_demo: manifest 顶层 ``primary_demo`` 的原始值。
        field_text: 错误信息里指代该字段的文字。

    Returns:
        PrimaryDemo | None: 缺省返回 ``None``。

    Raises:
        PrimaryDemoFormatError: 字段形状不对。
    """
    if raw_demo is None:
        return None
    if not isinstance(raw_demo, dict):
        raise PrimaryDemoFormatError(f"{field_text} must be an object.")
    demo_file = raw_demo.get("file")
    if not isinstance(demo_file, str) or not demo_file.strip():
        raise PrimaryDemoFormatError(f"{field_text} needs a non-empty `file`.")
    raw_steps = raw_demo.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PrimaryDemoFormatError(f"{field_text} needs a non-empty `steps` array.")
    parsed_steps: list[PrimaryDemoStep] = []
    for raw_step in raw_steps:
        step_caption = raw_step.get("caption") if isinstance(raw_step, dict) else None
        step_result = raw_step.get("result") if isinstance(raw_step, dict) else None
        if not isinstance(step_caption, str) or not step_caption.strip():
            raise PrimaryDemoFormatError(f"{field_text} step needs a non-empty `caption`.")
        if not isinstance(step_result, str) or not step_result.strip():
            raise PrimaryDemoFormatError(f"{field_text} step needs a non-empty `result`.")
        parsed_steps.append(
            PrimaryDemoStep(
                caption=step_caption.strip(),
                result=step_result.strip(),
                mock=raw_step.get("mock") is True,
            )
        )
    # 与 evidence_files 一致：只认纯文件名，去掉 agent 常误加的目录前缀
    return PrimaryDemo(file=Path(demo_file.strip()).name, steps=tuple(parsed_steps))
