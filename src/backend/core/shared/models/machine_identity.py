"""跨机器协作使用的稳定机器身份值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MachineIdentity:
    """一台 KedaCode 安装的持久身份。"""

    machine_id: str
    created_at: datetime
