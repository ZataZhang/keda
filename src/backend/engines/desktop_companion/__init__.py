"""桌面 companion：Console 之外的独立系统窗口。

本子树是 :class:`backend.core.shared.interfaces.desktop_companion.DesktopCompanionLauncher`
的唯一实现层：负责 helper 进程/系统窗口的生命周期，不含任何任务数据语义。
桌面运行时（pywebview）随 KedaCode 默认安装；环境缺失时本模块仍可 import，只对外报告
``not_installed`` 状态，保证 Web Dashboard 不受影响。
"""
