"""模型评测运行时能力的 core 编排入口（薄 facade）。

按四层依赖方向（``api → core → engines → infrastructure``），``api/`` 只依赖
``core/``；本模块把 :mod:`backend.engines.model_evaluation.service` 的编排能力
转发给上层。``core/`` 静态依赖 ``engines/`` 是允许的（见
``docs/ai-standards/architecture.md``），不再用 ``importlib`` 动态加载。
本模块只做层次归位，不新增编排逻辑；各名字语义以该 engines 模块为准。
"""

from __future__ import annotations

from backend.engines.model_evaluation.service import (
    EvaluationActionError,
    cancel_run,
    create_run,
    delete_run,
    get_judge_preview,
    get_run_detail,
    list_preset_options,
    list_runs,
    list_suites,
    recover_interrupted,
    start_run,
    submit_consent,
)

__all__ = [
    "EvaluationActionError",
    "cancel_run",
    "create_run",
    "delete_run",
    "get_judge_preview",
    "get_run_detail",
    "list_preset_options",
    "list_runs",
    "list_suites",
    "recover_interrupted",
    "start_run",
    "submit_consent",
]
