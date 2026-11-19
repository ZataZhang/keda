"""模型评测（Model Evaluation）Console API 路由。

多执行预设 × 多评审模型的 Agent/模型效果评测：suite 清单与校验、执行/评审
预设目录、run 生命周期（创建 / 启动 / 取消 / 删除 / 详情汇总）、评审 payload
预览与显式授权。路由层只做 HTTP 语义映射与 DTO 序列化，编排逻辑在
``core.use_cases.model_evaluation_runtime`` facade 之后。

红线：任何评审 provider 调用只可能发生在 approve 授权之后；预览返回的
payload 文本与实际发送内容逐字节一致（hash 冻结、过期即 409）。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.shared.models.model_evaluation import EvaluationRunCreateRequest
from backend.core.use_cases.model_evaluation_runtime import (
    EvaluationActionError,
    cancel_run,
    create_run,
    delete_run,
    get_judge_preview,
    get_run_detail,
    list_preset_options,
    list_runs,
    list_suites,
    start_run,
    submit_consent,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner-console-evaluation"])

#: 动作错误分类 -> HTTP 状态码。
_ERROR_STATUS_BY_KIND = {
    "not_found": 404,
    "conflict": 409,
    "invalid": 400,
}


def _serialize(value: Any) -> Any:
    """递归地把 dataclass 转成 JSON 友好结构。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: _serialize(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


def _raise_http(exc: EvaluationActionError) -> NoReturn:
    """把动作错误按分类映射为 HTTPException。"""
    status_code = _ERROR_STATUS_BY_KIND.get(exc.kind, 400)
    raise HTTPException(status_code=status_code, detail=str(exc))


class CreateRunRequest(BaseModel):
    """创建评测 run 的请求体（冻结快照，不执行）。"""

    suite_id: str = Field(min_length=1)
    execution_presets: list[str] = Field(min_length=1)
    judge_presets: list[str] = Field(default_factory=list)
    repeats: int = Field(default=1, ge=1, le=5)
    include_holdout: bool = False


class ConsentRequest(BaseModel):
    """评审授权请求体：approve / deny + 预览返回的 manifest hash。"""

    decision: str
    manifest_hash: str = Field(min_length=1)


@router.get("/agent-runner/console/evaluation/suites")
def list_evaluation_suites() -> dict:
    """列出仓库评测集及其校验状态。"""
    try:
        suites = list_suites()
    except Exception as exc:  # noqa: BLE001 - registry 读取失败不应 500 白屏
        _logger.exception("评测集清单读取失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"评测集清单读取失败: {exc}") from exc
    return {"suites": suites}


@router.get("/agent-runner/console/evaluation/presets")
def list_evaluation_presets() -> dict:
    """执行预设与评审预设目录（含评审可用性探针，不发起网络调用）。"""
    try:
        return list_preset_options()
    except Exception as exc:  # noqa: BLE001 - 配置加载失败要能看得到原因
        _logger.exception("评测预设目录读取失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"评测预设目录读取失败: {exc}") from exc


@router.get("/agent-runner/console/evaluation/runs")
def list_evaluation_runs() -> dict:
    """按创建时间倒序列出全部评测 run。"""
    return {"runs": [_serialize(run) for run in list_runs()]}


@router.post("/agent-runner/console/evaluation/runs", status_code=201)
def create_evaluation_run(request: CreateRunRequest) -> dict:
    """创建 run：全量 fail-fast 校验并冻结快照，不启动任何执行。"""
    try:
        run = create_run(
            EvaluationRunCreateRequest(
                suite_id=request.suite_id,
                execution_presets=request.execution_presets,
                judge_presets=request.judge_presets,
                repeats=request.repeats,
                include_holdout=request.include_holdout,
            )
        )
    except EvaluationActionError as exc:
        _raise_http(exc)
    response = _serialize(run)
    response["estimated_candidate_executions"] = (
        len(run.case_ids) * len(run.execution_preset_snapshots) * run.repeats
    )
    return response


@router.get("/agent-runner/console/evaluation/runs/{run_id}")
def get_evaluation_run_detail(run_id: str) -> dict:
    """run 详情：候选结果、逐评审评分与派生汇总（聚合值由明细实时计算）。"""
    try:
        detail = get_run_detail(run_id)
    except EvaluationActionError as exc:
        _raise_http(exc)
    return _serialize(detail)


@router.post("/agent-runner/console/evaluation/runs/{run_id}/start")
def start_evaluation_run(run_id: str) -> dict:
    """显式启动候选执行（created -> queued）。本动作绝不触发评审调用。"""
    try:
        run = start_run(run_id)
    except EvaluationActionError as exc:
        _raise_http(exc)
    return _serialize(run)


@router.post("/agent-runner/console/evaluation/runs/{run_id}/cancel")
def cancel_evaluation_run(run_id: str) -> dict:
    """取消 run（judging 之后的授权不可撤回，返回 409）。"""
    try:
        run = cancel_run(run_id)
    except EvaluationActionError as exc:
        _raise_http(exc)
    return _serialize(run)


@router.delete("/agent-runner/console/evaluation/runs/{run_id}")
def delete_evaluation_run(run_id: str) -> dict:
    """删除 run：级联清除 DB 行与本机暂存 payload / worktree。"""
    if not delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"run '{run_id}' 不存在")
    return {"deleted": run_id}


@router.get("/agent-runner/console/evaluation/runs/{run_id}/judge-preview")
def preview_judge_payloads(run_id: str) -> dict:
    """待授权评审 payload 预览（所见即所发；manifest hash 一并返回）。"""
    try:
        return get_judge_preview(run_id)
    except EvaluationActionError as exc:
        _raise_http(exc)


@router.post("/agent-runner/console/evaluation/runs/{run_id}/judge-consent")
def post_judge_consent(run_id: str, request: ConsentRequest) -> dict:
    """授权动作：approve 排程评审调用；deny 收尾并清除暂存 payload。

    服务端重新核对请求 hash、run 冻结 hash 与当前暂存文件重算 hash；
    任何不一致返回 409 且不发起任何 provider 请求。
    """
    try:
        run = submit_consent(
            run_id=run_id,
            decision=request.decision,
            manifest_hash=request.manifest_hash,
        )
    except EvaluationActionError as exc:
        _raise_http(exc)
    return _serialize(run)
