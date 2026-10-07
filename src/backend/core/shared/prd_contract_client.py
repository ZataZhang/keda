"""调用 prd skill 的 Machine Contract 解析脚本 —— PRD 格式解析的唯一入口。

keda 不再自带 PRD 格式解析：格式定义与实现都归 prd skill 的
``scripts/prd_contract.py``，本模块只负责"把 PRD 文本交给它、把 JSON 拿回来"。
这样"契约文本说一套、执行方各自实现一套"的漂移在结构上不可能再发生——历史上
已经发生过一次（契约模板写的分组标题形式被 keda 自己的解析器拒掉，交付门禁因此
把整个人属项误判成执行项）。

**为什么用子进程而不是进程内 import**：skill 由用户在用户级目录安装、可能随时被
更新（``kc init`` / sync），子进程给出干净的边界与超时，也避免把用户级代码的
异常与全局状态带进 runner 进程。代价是每次解析一次进程启动（几十毫秒量级），
相对 agent 运行的分钟级可忽略。

**本模块只搬运结构，不做门禁裁决**：哪些未勾项该拦、哪些属于人属项该放行，由
调用方按契约取用字段自行决定。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from backend.core.shared.prd_skill_location import resolve_prd_contract_script

DEFAULT_TIMEOUT_SECONDS = 60
"""单次解析的墙钟上限。解析是纯文本处理，正常在百毫秒内完成。"""


class PrdContractError(RuntimeError):
    """prd skill 的解析脚本缺失、执行失败或输出不可用时抛出。"""


def _run_contract_script(script_path: Path, arguments: list[str]) -> dict[str, Any]:
    """执行解析脚本并返回其 ``--json`` payload。

    Args:
        script_path: ``prd_contract.py`` 路径。
        arguments: 传给脚本的位置参数。

    Returns:
        解析出的 payload 字典。

    Raises:
        PrdContractError: 脚本不存在、执行失败、超时或输出不是合法 JSON。
    """
    if not script_path.is_file():
        raise PrdContractError(
            f"prd skill 的解析脚本不存在：{script_path}。keda 不再自带 PRD 格式解析，"
            "它由 prd skill 的 scripts/prd_contract.py 提供。请安装或更新模板 skill"
            "（`kc init` / skill sync），或用 KEDACODE_PRD_SKILL_PATH 指向一个完整的 "
            "prd SKILL.md（其兄弟 scripts/ 目录必须一并安装）。"
        )
    try:
        completed = subprocess.run(
            [sys.executable, str(script_path), "--json", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as timeout_error:
        raise PrdContractError(
            f"prd skill 解析脚本超时（{DEFAULT_TIMEOUT_SECONDS}s）：{script_path}"
        ) from timeout_error
    except OSError as run_error:
        raise PrdContractError(
            f"无法执行 prd skill 解析脚本 {script_path}：{run_error}"
        ) from run_error

    if completed.returncode != 0:
        raise PrdContractError(
            f"prd skill 解析脚本以退出码 {completed.returncode} 结束：{script_path}"
            f"\n--- stderr ---\n{(completed.stderr or '').strip()[:2000]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as decode_error:
        raise PrdContractError(
            f"prd skill 解析脚本的输出不是合法 JSON：{script_path}"
            f"\n--- stdout 前 2000 字符 ---\n{completed.stdout[:2000]}"
        ) from decode_error
    if not isinstance(payload, dict):
        raise PrdContractError(
            f"prd skill 解析脚本的 JSON 顶层不是对象：{script_path}（got {type(payload).__name__}）"
        )
    return payload


def contract_version(script_path: Path | None = None) -> int | None:
    """读取解析脚本声明的 Machine Contract 版本号（payload 里的 ``contract_version``）。

    Args:
        script_path: 显式脚本路径；``None`` 时按 skill 解析优先级定位。

    Returns:
        payload 里的整数版本号；缺字段或类型不对时返回 ``None``。

    Raises:
        PrdContractError: 脚本不可用（缺失 / 执行失败 / 输出非法）。
    """
    resolved_script_path = script_path or resolve_prd_contract_script()
    payload = _run_contract_script(resolved_script_path, [])
    version = payload.get("contract_version")
    return version if isinstance(version, int) else None


def parse_prd_contract(file_content: str) -> dict[str, Any]:
    """把 PRD 文本交给 skill 的解析脚本，返回它的单份 PRD payload。

    Args:
        file_content: PRD 全文。

    Returns:
        payload 里的 ``prds[0]``（含 ``checklist`` 与 ``change_log`` 两个子对象）。

    Raises:
        PrdContractError: 脚本不可用，或它没能解析出这一份 PRD。
    """
    script_path = resolve_prd_contract_script()
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as handle:
        handle.write(file_content)
        temporary_prd_path = Path(handle.name)
    try:
        payload = _run_contract_script(script_path, [str(temporary_prd_path)])
    finally:
        temporary_prd_path.unlink(missing_ok=True)

    prds = payload.get("prds")
    if not isinstance(prds, list) or not prds:
        errors = payload.get("errors")
        raise PrdContractError(
            f"prd skill 解析脚本没有返回任何 PRD 结果：{script_path}" f"（errors={errors!r}）"
        )
    first_prd = prds[0]
    if not isinstance(first_prd, dict):
        raise PrdContractError(f"prd skill 解析脚本返回的 PRD 条目不是对象：{script_path}")
    return first_prd
