"""机器身份持久化端口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.core.shared.models.machine_identity import MachineIdentity


class IMachineIdentityStore(ABC):
    """读取或创建本机稳定身份的抽象存储。"""

    @abstractmethod
    def load_or_create(self) -> MachineIdentity:
        """原子读取或创建机器身份。"""
