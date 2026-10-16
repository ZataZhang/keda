"""模型评测运行时能力的 core 编排入口（薄 facade）。

按四层依赖方向（``api → core → engines → infrastructure``），``api/`` 路由
不得直接 import ``backend.engines.model_evaluation``；本模块作为 core 侧的
统一入口转发 engines 编排能力。实现机制遵循
:mod:`backend.core.use_cases.agent_runner_factory` 先例：core 不得静态
``import backend.engines.*``，故在模块加载时经 ``importlib.import_module``
解析 engines 实现并绑定为模块级名字。

本模块只做层次归位，不新增编排逻辑；各名字语义以
:mod:`backend.engines.model_evaluation.service` 为准。
"""

from __future__ import annotations

import importlib

_engines_service_module = importlib.import_module("backend.engines.model_evaluation.service")

EvaluationActionError = _engines_service_module.EvaluationActionError
cancel_run = _engines_service_module.cancel_run
create_run = _engines_service_module.create_run
delete_run = _engines_service_module.delete_run
get_judge_preview = _engines_service_module.get_judge_preview
get_run_detail = _engines_service_module.get_run_detail
list_preset_options = _engines_service_module.list_preset_options
list_runs = _engines_service_module.list_runs
list_suites = _engines_service_module.list_suites
recover_interrupted = _engines_service_module.recover_interrupted
start_run = _engines_service_module.start_run
submit_consent = _engines_service_module.submit_consent
