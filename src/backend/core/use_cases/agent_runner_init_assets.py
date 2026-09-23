"""仓库初始化资产安装能力的 core 编排入口（薄 facade）。

合并转发两个相近的 init 期资产安装 engines 能力：

- :mod:`backend.engines.agent_runner.remote_template_skills` — 远程模板
  技能安装；
- :mod:`backend.engines.agent_runner.workflow_install` — workflow 文件
  安装。

使 ``api/`` 层只依赖 core。实现机制（模块加载时经 ``importlib`` 解析
engines 实现）见 :mod:`backend.core.use_cases.agent_runner_factory` 的
模块 docstring。
"""

from __future__ import annotations

import importlib

_engines_remote_template_skills_module = importlib.import_module(
    "backend.engines.agent_runner.remote_template_skills"
)
_engines_workflow_install_module = importlib.import_module(
    "backend.engines.agent_runner.workflow_install"
)

RemoteTemplateSkillInstallOptions = (
    _engines_remote_template_skills_module.RemoteTemplateSkillInstallOptions
)
install_remote_template_skills = (
    _engines_remote_template_skills_module.install_remote_template_skills
)
install_packaged_operator_skill = (
    _engines_remote_template_skills_module.install_packaged_operator_skill
)
ExistingFileRefusedError = _engines_workflow_install_module.ExistingFileRefusedError
UnknownWorkflowError = _engines_workflow_install_module.UnknownWorkflowError
WorkflowInstallOptions = _engines_workflow_install_module.WorkflowInstallOptions
install_workflow = _engines_workflow_install_module.install_workflow

__all__ = [
    "ExistingFileRefusedError",
    "RemoteTemplateSkillInstallOptions",
    "UnknownWorkflowError",
    "WorkflowInstallOptions",
    "install_remote_template_skills",
    "install_packaged_operator_skill",
    "install_workflow",
]
