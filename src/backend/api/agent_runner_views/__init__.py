"""agent runner CLI 终端呈现模块包。

承载从 ``engines/agent_runner/`` 迁入的终端实时视图：CLI 渲染属于接入层
职责（依赖 ``rich``、直接写终端），不应住在 engines 层。

- :mod:`backend.api.agent_runner_views.live_terminal` — 多 agent
  deliberation 输出的实时视图（``create_output_view``）。
- :mod:`backend.api.agent_runner_views.runner_live_view` — daemon 并行
  处理 Issue 时的实时视图（``create_runner_live_view``）。
- :mod:`backend.api.agent_runner_views.live_panels` — 两个视图共用的
  Rich live-panel 渲染器。
"""
