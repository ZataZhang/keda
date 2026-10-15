"""daemon 单轮轮询的活动信号。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PassActivity:
    """一次 ``run_once`` 执行里观察到的工作量，供 daemon 判断是否空闲。

    由 daemon 创建并传入，``run_once`` 在读到 ready 队列后回填。可变对象而非返回值，
    是为了不改变 ``run_once`` 既有的退出码返回约定。

    Attributes:
        ready_found: 本轮 ready 队列里读到的 Issue 数；``0`` 且没有别的活动时即空闲。
    """

    ready_found: int = 0
