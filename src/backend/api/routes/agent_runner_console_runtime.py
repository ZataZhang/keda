"""统一运行时看板与桌面 companion 的 Console 端点。

从 ``agent_runner_console.py`` 按行数红线拆出的新模块（与
``agent_runner_console_issues.py`` / ``agent_runner_console_runs.py`` 同一先例）：
本文件只做来源校验、参数解析与用例调用，聚合语义全部在
:mod:`backend.core.use_cases.runtime_dashboard`。

两条边界写在这里：

- **任务列表只来自已注册仓库的 Console 观测路径**（监控快照 + 运行/attempt/
  生命周期/invocation 账本），不掺托管进程日志，也不新增数据源；invocation
  账本只标识当前会话最近一条未收口调用的执行器，不据此断言进程存活；
- **桌面窗口只允许当前 loopback Console 的同源请求启动**：绑定地址与端口取自
  ASGI ``scope["server"]``（由 uvicorn 写入，客户端改不了，伪造 ``Host`` 也改不了），
  再要求 ``Origin`` 与该绑定同 scheme、同端口、host 属于回环别名且不带路径；
  因此浏览器扩展、其他本地页面、DNS rebinding 都拿不到启动系统窗口的能力。
  校验通过后**返回绑定侧 origin**，窗口内容固定为
  ``{绑定 origin}/app/companion/``，不接受调用方给的 URL、路径、命令，也不回显
  请求头里的 host。
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from backend.core.shared.interfaces.desktop_companion import (
    DesktopCompanionLauncher,
    DesktopCompanionSnapshot,
)
from backend.core.use_cases.agent_runner_evidence_branch import (
    list_evidence_branches,
    read_branch_file,
    read_branch_page,
)
from backend.core.use_cases.agent_runner_evidence_media import EvidenceMedia, find_evidence_media
from backend.core.use_cases.agent_runner_factory import (
    create_console_store,
    create_process_supervisor,
    create_registry_editor,
    load_fresh_agent_runner_settings,
    resolve_repository_targets_with_diagnostics,
)
from backend.core.use_cases.desktop_companion import create_desktop_companion_launcher
from backend.core.use_cases.monitor_snapshots import get_snapshot_overview
from backend.core.use_cases.runtime_dashboard import build_runtime_task_summary

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-runner-console-runtime"])

#: 本 Console 进程的身份：helper 用它识别「上一个 Console 留下的孤儿窗口」。
#: 每次进程启动重新生成，因此重启 Console 一定会让旧窗口自行退出。
CONSOLE_SESSION = uuid.uuid4().hex

#: 允许的 loopback 主机名：``localhost`` 与 ``127.0.0.1`` 指向同一个 Console。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _enabled_repo_ids() -> list[str]:
    """取当前 registry 中启用仓库的标识列表，作为摘要的展示白名单。"""
    settings = load_fresh_agent_runner_settings()
    contexts, _failures = resolve_repository_targets_with_diagnostics(settings)
    return [context.repo_id for context in contexts]


def _repository_paths() -> dict[str, Path]:
    """取 registry 中仓库标识到本地路径的映射；读失败时返回空映射。"""
    try:
        return {
            entry.repo_id: Path(entry.path)
            for entry in create_registry_editor().list_repositories()
            if entry.path_exists
        }
    except Exception:  # noqa: BLE001 - 读 registry 失败只影响证据按钮，不影响看板。
        _logger.warning("读取 registry 路径失败，证据按钮不可用", exc_info=True)
        return {}


def _evidence_media(repository_paths: dict[str, Path], task: Any) -> EvidenceMedia | None:
    """解析该任务的证据：先看本地，本地没有页面时回退到证据分支。

    分支清单有 TTL 缓存，因此同一轮看板刷新不会为每张卡重复打远端。
    """
    repo_path = repository_paths.get(task.repo_id)
    if repo_path is None:
        return None
    try:
        media = find_evidence_media(repo_path, task.issue_number, task.prd_path)
    except OSError:
        return None
    if media.page_ref is not None:
        return media
    # 列表里只判断「有没有证据分支」，不在这里拉取分支内容：一次 ls-remote
    # 的结果已缓存，真正读页面留到点击时按需进行。
    try:
        branch = list_evidence_branches(repo_path).get(task.issue_number)
    except OSError:
        return media
    if branch is None:
        return media
    return replace(media, page_ref=branch, source=branch)


def _format_loopback_origin(host: str, port: int) -> str:
    """拼出回环 origin；IPv6 字面量必须加方括号，否则不是合法 URL。"""
    hostname = f"[{host}]" if ":" in host else host
    return f"http://{hostname}:{port}"


def _bound_loopback_server(request: Request) -> tuple[str, int]:
    """取 uvicorn 实际绑定的回环 ``host/port``（不读 ``Host`` 头）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ``tuple[str, int]``：绑定的主机与端口。

    Raises:
        HTTPException: 503——ASGI ``scope["server"]`` 缺失或形状异常；403——绑定
            地址不是回环。
    """
    server = request.scope.get("server")
    if not server or len(server) != 2:
        raise HTTPException(status_code=503, detail="无法确定 Console 的绑定地址。")
    bound_host, bound_port = str(server[0]), int(server[1])
    if bound_host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="桌面窗口仅允许本机回环 Console 启动。")
    return bound_host, bound_port


def _trusted_console_origin(request: Request) -> str:
    """校验请求来自当前 Console 的同源页面，返回窗口加载用的权威 origin。

    ``Origin`` 头只用于**判定同源**，不会成为窗口地址：回环别名（``localhost`` 与
    ``127.0.0.1``）指向同一个 Console，但 helper 的 argv 与 WebView 加载的 URL 必须
    写后端自己绑定的那个 origin，客户端改不了。

    Args:
        request: FastAPI 请求对象。

    Returns:
        由绑定地址推出的、形如 ``http://127.0.0.1:8313`` 的已校验 origin。

    Raises:
        HTTPException: 403 或 503——见 :func:`_bound_loopback_server`，以及缺少
            ``Origin`` / ``Referer``、origin 与当前 Console 的
            scheme/host 别名/端口不一致。
    """
    bound_host, bound_port = _bound_loopback_server(request)

    raw_origin = (request.headers.get("origin") or "").strip()
    # WebKit（pywebview 的 companion 窗口）对同源 POST 可能不带 Origin，或写成
    # "null"。此时退回同源 Referer 校验：跨源页面带的是自己的 Referer，一样会被
    # 下面的 host/port 校验拒绝，安全性不降级。
    from_origin_header = bool(raw_origin) and raw_origin.lower() != "null"
    if not from_origin_header:
        raw_origin = (request.headers.get("referer") or "").strip()
    if not raw_origin:
        raise HTTPException(
            status_code=403,
            detail="缺少 Origin / Referer，无法确认同源，拒绝该桌面窗口操作。",
        )
    parsed = urlsplit(raw_origin)
    try:
        request_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        # 端口写成非数字或越界时 urlsplit 直接抛错：按「无法确认同源」拒绝，
        # 不能让伪造的 Origin 头把请求打成 500。
        raise HTTPException(
            status_code=403,
            detail=f"Origin {raw_origin!r} 端口无法解析，拒绝该桌面窗口操作。",
        ) from exc
    # Referer 天然带页面路径（/app/companion/），因此只在 Origin 头路径必须为空。
    path_is_origin_shaped = (not from_origin_header) or parsed.path in {"", "/"}
    if (
        parsed.scheme != "http"
        or (parsed.hostname or "") not in _LOOPBACK_HOSTS
        or request_port != bound_port
        or not path_is_origin_shaped
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Origin {raw_origin!r} 与当前本机 Console 不一致，拒绝该桌面窗口操作。",
        )
    return _format_loopback_origin(bound_host, bound_port)


def _trusted_bound_origin(request: Request) -> str:
    """由 ASGI 绑定地址推出 Console origin，供只读 GET 使用；非回环时返回空串。"""
    server = request.scope.get("server")
    if not server or len(server) != 2:
        return ""
    bound_host, bound_port = str(server[0]), int(server[1])
    if bound_host not in _LOOPBACK_HOSTS:
        return ""
    return _format_loopback_origin(bound_host, bound_port)


def _companion_launcher() -> DesktopCompanionLauncher:
    """取本进程共享的 companion launcher（工单计数必须跨请求保持）。"""
    return create_desktop_companion_launcher(console_session=CONSOLE_SESSION)


def _companion_payload(snapshot: DesktopCompanionSnapshot, console_origin: str) -> dict[str, Any]:
    """把 companion 快照转成前端与 helper 共用的 JSON 载荷。"""
    payload = asdict(snapshot)
    payload["hint"] = snapshot.hint
    payload["window_exists"] = snapshot.window_exists
    # 只有确实存在窗口时才给出加载地址，避免前端把「可以开」画成「已经开着」。
    payload["companion_url"] = (
        f"{console_origin}/app/companion/" if snapshot.window_exists else None
    )
    return payload


@router.get("/agent-runner/console/runtime/tasks")
def get_runtime_tasks() -> dict[str, Any]:
    """返回统一运行看板任务摘要：状态分桶、身份、阶段、执行者与耗时。

    改动树触达读数**不在本响应里**：唯一来源仍是现有
    ``GET /agent-runner/backlog/prds/{encoded_path}/progress`` 契约，前端按
    ``prd_path`` 取数，避免出现第二份影响树检测逻辑。
    """
    store = create_console_store()
    enabled_repo_ids = _enabled_repo_ids()
    snapshot_overview = get_snapshot_overview(store, enabled_repo_ids=enabled_repo_ids)
    repository_paths = _repository_paths()
    summary = build_runtime_task_summary(
        snapshot_overview=snapshot_overview,
        run_history_store=store,
        prd_lifecycle_store=store,
        enabled_repo_ids=enabled_repo_ids,
        evidence_media=lambda task: _evidence_media(repository_paths, task),
    )
    try:
        supervisor = create_process_supervisor()
        process_records = [
            *supervisor.list_processes(),
            *supervisor.list_unmanaged_processes(create_registry_editor().list_repositories()),
        ]
        review_repo_ids = {
            record.repo_id
            for record in process_records
            if record.kind == "review_daemon" and record.status == "running"
        }
    except Exception:  # noqa: BLE001 - 探活失败必须显示未知，不能假报未启动。
        _logger.warning("读取 PR 值守进程失败", exc_info=True)
        review_repo_ids = None
    payload = asdict(summary)
    payload["tasks"] = [
        {
            **asdict(task),
            "task_key": task.task_key,
            "review_daemon_running": (
                task.repo_id in review_repo_ids if review_repo_ids is not None else None
            ),
        }
        for task in summary.tasks
    ]
    return payload


@router.get("/agent-runner/console/runtime/evidence/video")
def get_runtime_evidence_video(
    repo_id: str,
    issue: int,
    prd_path: str | None = None,
) -> FileResponse:
    """播放某个任务的本地证据视频。

    路径只按 registry 登记的仓库根目录解析，不接受调用方给的路径，因此不构成
    任意文件读取；解析不到视频时返回 404，前端据此不显示播放按钮。

    Args:
        repo_id: 注册仓库标识。
        issue: Issue 编号。
        prd_path: 该 Issue 关联的 PRD 相对路径；用于定位证据目录。

    Returns:
        FileResponse: 视频文件流。
    """
    repo_path = _repository_paths().get(repo_id)
    if repo_path is None:
        raise HTTPException(status_code=404, detail="Repository is not registered.")
    media = find_evidence_media(repo_path, issue, prd_path)
    if media.video_path is None:
        raise HTTPException(status_code=404, detail="No evidence video for this task.")
    return FileResponse(media.video_path)


#: 人审页里允许内联的图片后缀；页面正文只按 Markdown 返回，不执行任何脚本。
_EVIDENCE_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

#: 人审页正文上限：超过说明这是数据文件而不是给人看的页面。
_EVIDENCE_PAGE_MAX_BYTES = 512 * 1024

#: 证据端点前缀，与 router 挂载前缀一致。
_EVIDENCE_ROUTE_PREFIX = "/api/v1/agent-runner/console/runtime/evidence"


def _resolve_evidence_page(repo_id: str, issue: int, prd_path: str | None) -> Path:
    """解析人审页路径；解析不到直接 404。"""
    repo_path = _repository_paths().get(repo_id)
    if repo_path is None:
        raise HTTPException(status_code=404, detail="Repository is not registered.")
    media = find_evidence_media(repo_path, issue, prd_path)
    if media.page_path is None:
        raise HTTPException(status_code=404, detail="No human review page for this task.")
    return media.page_path


def _image_url_base(repo_id: str, issue: int, prd_path: str | None, source: str) -> str:
    """拼人审页图片的取图前缀；页面内的相对路径由前端拼在后面。"""
    params = {"repo_id": repo_id, "issue": str(issue), "source": source}
    if prd_path:
        params["prd_path"] = prd_path
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return f"{_EVIDENCE_ROUTE_PREFIX}/file?{query}&name="


@router.get("/agent-runner/console/runtime/evidence/page")
def get_runtime_evidence_page(
    repo_id: str,
    issue: int,
    prd_path: str | None = None,
    allow_branch_fallback: bool = True,
) -> dict[str, Any]:
    """返回该任务的人审页正文与取图前缀。

    先读本地证据目录；本地没有页面时，若允许回退，就从证据分支读取同一份页面，
    并在 source 里标明来源，前端据此显示「来自哪个分支」。

    Args:
        repo_id: 注册仓库标识。
        issue: Issue 编号。
        prd_path: 该 Issue 关联的 PRD 相对路径。
        allow_branch_fallback: 本地没有页面时是否回退到证据分支。

    Returns:
        dict[str, Any]: 页面路径、Markdown 正文、取图前缀与证据来源。
    """
    repo_path = _repository_paths().get(repo_id)
    if repo_path is None:
        raise HTTPException(status_code=404, detail="Repository is not registered.")
    local = find_evidence_media(repo_path, issue, prd_path)
    if local.page_path is not None:
        if local.page_path.stat().st_size > _EVIDENCE_PAGE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Human review page is too large to render.")
        return {
            "path": local.page_path.name,
            "markdown": local.page_path.read_text(encoding="utf-8"),
            "image_base": _image_url_base(repo_id, issue, prd_path, source="local"),
            "source": "local",
        }
    if not allow_branch_fallback:
        raise HTTPException(status_code=404, detail="No human review page for this task.")
    branch_page = read_branch_page(repo_path, issue)
    if branch_page is None:
        raise HTTPException(status_code=404, detail="No human review page for this task.")
    if len(branch_page.markdown.encode("utf-8")) > _EVIDENCE_PAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Human review page is too large to render.")
    return {
        "path": branch_page.page_name,
        "markdown": branch_page.markdown,
        "image_base": _image_url_base(repo_id, issue, prd_path, source=branch_page.branch),
        "source": branch_page.branch,
    }


@router.get("/agent-runner/console/runtime/evidence/file")
def get_runtime_evidence_file(
    repo_id: str,
    issue: int,
    name: str,
    prd_path: str | None = None,
    source: str = "local",
) -> Response:
    """返回人审页内联的图片，本地没有时按 source 从证据分支读取。

    只允许图片后缀、且 name 必须是纯文件名，因此不构成任意文件读取。

    Args:
        repo_id: 注册仓库标识。
        issue: Issue 编号。
        name: 页面里写的相对文件名。
        prd_path: 该 Issue 关联的 PRD 相对路径。
        source: 证据来源；local 读本地，其他值视为证据分支名。

    Returns:
        Response: 图片字节流。
    """
    if Path(name).name != name or Path(name).suffix.lower() not in _EVIDENCE_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported evidence file name.")
    repo_path = _repository_paths().get(repo_id)
    if repo_path is None:
        raise HTTPException(status_code=404, detail="Repository is not registered.")
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if source == "local":
        page_path = find_evidence_media(repo_path, issue, prd_path).page_path
        if page_path is None:
            raise HTTPException(status_code=404, detail="No human review page for this task.")
        image_path = page_path.parent / name
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail="Evidence image not found.")
        return FileResponse(image_path)
    payload = read_branch_file(repo_path, issue, name)
    if payload is None:
        raise HTTPException(status_code=404, detail="Evidence image not found.")
    return Response(content=payload, media_type=media_type)


@router.get("/agent-runner/console/desktop-companion/status")
def get_desktop_companion_status(request: Request) -> dict[str, Any]:
    """返回桌面 companion 状态；helper 与网页看板共用这个只读探针。

    GET 不要求 ``Origin``（浏览器对同源 GET 可能不带该头），窗口动作端点才做
    同源校验。
    """
    console_origin = _trusted_bound_origin(request)
    snapshot = _companion_launcher().snapshot(console_origin=console_origin)
    return _companion_payload(snapshot, console_origin)


@router.post("/agent-runner/console/desktop-companion/window")
def open_desktop_companion_window(request: Request) -> dict[str, Any]:
    """开启或聚焦桌面小窗：仅接受当前 loopback Console 的同源 POST。"""
    console_origin = _trusted_console_origin(request)
    snapshot = _companion_launcher().open_window(console_origin=console_origin)
    return _companion_payload(snapshot, console_origin)


@router.post("/agent-runner/console/desktop-companion/window/close")
def close_desktop_companion_window(request: Request) -> dict[str, Any]:
    """请求桌面小窗自行关窗退出；同样要求同源，避免跨页面触发。"""
    console_origin = _trusted_console_origin(request)
    snapshot = _companion_launcher().close_window()
    return _companion_payload(snapshot, console_origin)


__all__ = [
    "CONSOLE_SESSION",
    "close_desktop_companion_window",
    "get_desktop_companion_status",
    "get_runtime_tasks",
    "open_desktop_companion_window",
    "router",
]
