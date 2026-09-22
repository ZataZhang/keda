"""守护本机查看器服务端（读 + 仅暂存）的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/view/server.py``、``launch.py`` 与 ``instance.py``。核心
不变量：

1. **写边界只剩「暂存」这一个口，其余写方法仍然一律拒绝。** 服务端唯一的写入口是
   ``POST /api/stage``（只做 ``git add``）；``do_PUT`` / ``do_DELETE`` / ``do_PATCH`` 既
   不存在也一律 405，指向别的路由的 POST 同样 405。这条边界由用户拍板从「完全不写」收窄
   到这里（2026-09-22），因此它既不能悄悄扩大，也不能因为「更明确地拒绝」而在其余写方法上
   补同名处理器——那样 ``rg -n "def do_PUT" scripts/shared/view/`` 这类静态断言就失去判别力。
   写口额外要求 ``Content-Type: application/json``：跨站表单式 POST 是「简单请求」，浏览器
   会直接发出去，而 JSON 类型强制预检、预检又必然失败，于是「某个网页在你不知情时改动你的
   索引」这条路被封住。
2. **路径不得越出仓库。** ``..`` 与符号链接都要在 ``resolve()`` 之后再断言，顺序颠倒
   时符号链接逃逸会漏网；拒绝信息本身也不得回声绝对路径，否则「被拒绝」就成了仓库外
   路径的探测口。
3. **改动分区是封闭枚举，且分区取值不会流进 git 参数。** 分区（``staged`` /
   ``unstaged`` / ``untracked``）只用于在服务端选择命令，取值本身从不作为位置参数交给
   ``git``；路径参数另在 ``--`` 之后传入并已做越界校验。白名单外的取值一律 400，
   ``--output=...`` 这类以 ``-`` 开头的内容因此没有任何机会被 git 当成选项解析。
   ``POST /api/stage`` 的 ``scope`` 同一条口径。
4. **界面数字必须与终端逐项一致。** 三个分区各自的 ``--numstat -z`` 条目形状不同（普通
   条目与重命名条目 token 数不同，未跟踪那一段走 ``--no-index`` 的双路径形式）；把其中
   任一种处理错都会静默漏掉文件或统计，而界面看上去仍然"有内容"。
5. **陈旧登记必须被接管，且登记清理不得误伤他人。** 复用判定依赖三项交叉校验
   （进程存活 / 端口可连 / 仓库一致）；空闲回收清理登记时要确认登记仍属于自己，
   否则 ``--no-reuse`` 起的更新实例会被旧实例顺手抹掉登记。
6. **服务只绑本机回环地址。** 绑成 ``0.0.0.0`` 会把一个只读接口开给整个局域网。
7. **重命名的配对不能被 pathspec 吃掉。** ``git diff <rev> -- <新路径>`` 会把旧路径
   排除出候选，同一个文件随即降级成「新增」且正文整篇算成新增行；单文件 diff 必须
   先查出旧路径、再把新旧两条路径一起交给 git。纯重命名则要能自我说明，不能被界面
   说成「与当前内容一致」。
8. **「路径 + 分区」才是改动条目的身份。** 同一个文件可以在暂存之后又被修改，于是同时
   出现在两段里，而且两段的逐行改动并不相同；只按路径取 diff 会拿到另一段的内容。
9. **预览是显式动作，不是默认行为。** ``/api/file`` 只报告该文件支持哪种预览（``preview``
   字段），Markdown 的渲染结果由 ``/api/markdown`` 按需给出——把渲染并进正文应答会让每次
   打开 ``.md`` 都多付一次渲染，也把源码顶掉。后缀判定只在服务端一处。
10. **``/raw/`` 保持路径原样，但边界一条不少。** 相对引用必须能解析回同一路由，所以这条
   路由不能走静态资源的文件名白名单；替代防护是 ``resolve()`` 之后再断言仍在仓库根之下，
   越界、目录、缺失分别拒绝。表外后缀一律以二进制流供出，不会以 ``text/html`` 出现在
   浏览器里。
11. **图片预览不受文本上限约束，且必须真的以 ``image/*`` 供出。** 图片走「后缀命中即返回」，
   正文一次都不读，因此 256 KiB 那条上限对它没有意义——截图动辄超过它。服务端有两份按后缀
   的表（图片后缀、``/raw/`` 的 Content-Type），漏掉任何一边都只会得到一片空白的预览。
12. **图片改动的旧新两版必须与分区对得上，且各版按自己的路径取。** 已暂存比 HEAD ↔ 索引、
   未暂存比索引 ↔ 工作区、未跟踪只有工作区那一版；旧侧不是「同一路径的上一个版本」——重命名
   时它在**旧路径**上。来源记混、路径取错，或对一条本分区里并不存在的改动也给对比，界面就会
   把删除说成新增、拿同一版既当旧又当新，或者为一条不存在的改动画两张图。
13. **``/raw/`` 的 ``rev`` 是封闭枚举，且不改变边界。** 历史版本的字节从对象库读（不是工作区
    那份），路径照旧逐字经过同一份越界断言；未列出的取值 400，该版本里没有这个文件 404。取值
    是任人可填的查询参数，因此它绝不能作为字符串流进 git。
14. **Markdown 预览里的相对引用必须改写，且只改相对的那几种。** 预览片段内联在查看器页面
    （``/``）里，相对引用照着页面根解析必然落空（图片就是这么变成空白的）。``src`` 一律指向
    ``/raw/``、指向另一份 Markdown 的 ``href`` 指向查看器直达链接；带 scheme 的、纯片段的、
    带查询串的、以及解析后跑出仓库根的一律原样保留——猜错比不改更糟。手写的 raw HTML 标签
    与 markdown 语法走同一条路，因此不能只照顾其中一种。改写出来的地址必须真的能取回字节，
    只断言字符串会让「改成了另一个取不到的东西」也通过。

用例全部打在真实进程与真实 HTTP 上：被测的是绑定、路由分发与 ``git`` 子进程这条
完整链路，桩掉其中任何一段都测不到本文列出的不变量。
"""

from __future__ import annotations

import html
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

_PROJECT_ROOT_PATH = Path(__file__).resolve().parents[3]
_VIEW_SCRIPTS_PATH = _PROJECT_ROOT_PATH / "scripts" / "shared" / "view"
_SERVER_SCRIPT_PATH = _VIEW_SCRIPTS_PATH / "server.py"
_LAUNCH_SCRIPT_PATH = _VIEW_SCRIPTS_PATH / "launch.py"

# server.py / launch.py 不是包的一部分，import 前需把它们所在目录放到 sys.path。
if str(_VIEW_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_VIEW_SCRIPTS_PATH))

import instance  # noqa: E402
import server  # noqa: E402

_LOOPBACK_HOST = "127.0.0.1"
_SERVER_START_TIMEOUT_SECONDS = 10.0
_SERVER_POLL_INTERVAL_SECONDS = 0.05
_STOP_TIMEOUT_SECONDS = 10.0

#: 超过 ``workspace.MAX_FILE_BYTES``（256 KiB）的正文，用于验「不返回正文」。
_OVERSIZE_BYTE_COUNT = 300 * 1024

#: 一段够像 PNG 的字节（真签名 + IHDR 头），用于图片预览用例。守卫测试不解码图片，
#: 只需首字节不是合法 UTF-8，因此它同时能验「二进制字节不被当文本处理」。
_IMAGE_SIGNATURE_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

#: 进入图片预览的后缀。与 ``workspace._IMAGE_SUFFIXES`` 一一对应——这里刻意再列一遍：
#: 服务端那两个集合（图片后缀、``/raw/`` 的 Content-Type）哪天改了，这条会先炸。
_IMAGE_SUFFIXES_UNDER_TEST = (".gif", ".ico", ".jpeg", ".jpg", ".png", ".webp")

#: 重命名用例的图片内容。刻意造到 512 字节：改动再小也够 git 判成重命名（R087），而几十字节的
#: 二元文件改一个字节会被拆成「删 + 增」——那样就测不到「旧侧按旧路径取」这条了。
_RENAME_IMAGE_BYTES = _IMAGE_SIGNATURE_BYTES + bytes(range(256)) * 2

#: 图片改动用例的初始内容（进首个提交）。长度刻意各不相同，界面上的大小标签才有区分度，
#: 断言也才能把「两版各是哪一份」钉住。
#:
#: 重命名那一张刻意放在**仓库根**：终端 ``--numstat`` 对目录内的改名会写成
#: ``docs/{旧 => 新}`` 的紧凑形式，而本文件比较终端输出的助手只认 ``旧 => 新`` 的简单形式
#: （见 ``_expected_counts_by_path``，它的注释里也写明了这条约定）。放根目录既保住那条约定，
#: 也照样测到「旧侧按旧路径取」。
_IMAGE_VERSIONS_BEFORE_CHANGES = {
    "docs/photo.png": _IMAGE_SIGNATURE_BYTES + b"photo-v1",
    "docs/doomed.png": _IMAGE_SIGNATURE_BYTES + b"doomed-v1",
    "old-photo.png": _RENAME_IMAGE_BYTES,
}


def _flip_one_byte(payload: bytes) -> bytes:
    """把中间那个字节取反，用于造「重命名 + 改了内容」的图片版本。"""
    mutated_payload = bytearray(payload)
    middle_index = len(mutated_payload) // 2
    mutated_payload[middle_index] ^= 0xFF
    return bytes(mutated_payload)


#: 这些写方法的处理器名字不得出现在服务端源码里：唯一的写入口是 ``POST /api/stage``，
#: 其余写方法既不存在也一律 405（逐个定义同名方法会让静态断言失去判别力）。
_FORBIDDEN_WRITE_METHOD_HANDLER_NAMES = ("do_PUT", "do_DELETE", "do_PATCH")

#: 唯一允许存在的写入口处理器名字，且它必须只出现一次。
_ALLOWED_WRITE_METHOD_HANDLER_NAME = "do_POST"


@dataclass(frozen=True)
class RunningViewServer:
    """一个跑在真实回环端口上的查看器服务，供用例发真实 HTTP 请求。

    Attributes:
        repository_root (Path): 被查看的仓库根。
        port (int): 服务监听端口。
        process_id (int): 服务进程号。
    """

    repository_root: Path
    port: int
    process_id: int

    def request(
        self, route: str, *, method: str = "GET", json_body: dict | None = None
    ) -> tuple[int, dict]:
        """对服务发一次真实 HTTP 请求。

        Args:
            route (str): 形如 ``/api/tree`` 的路由（含查询串）。
            method (str): HTTP 方法。
            json_body (dict | None): 需要带 JSON 请求体时给出对象；``None`` 表示**不带**
                正文，也不带 ``Content-Type``——写口对这种形态的应答正是「不是 JSON」那条，
                用例要的就是这个默认形态。

        Returns:
            tuple[int, dict]: 状态码与解析后的 JSON 正文。
        """
        request_body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        request = urllib.request.Request(  # 目标固定为本机回环
            f"http://{_LOOPBACK_HOST}:{self.port}{route}",
            data=request_body,
            method=method,
        )
        if request_body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as http_error:
            return http_error.code, json.loads(http_error.read().decode("utf-8"))

    def request_text(self, route: str) -> tuple[int, str, str]:
        """对服务发一次真实 HTTP 请求，返回原始文本与响应头里的内容类型。

        Args:
            route (str): 路由（含查询串）。

        Returns:
            tuple[int, str, str]: 状态码、正文文本与 ``Content-Type``。
        """
        request = urllib.request.Request(f"http://{_LOOPBACK_HOST}:{self.port}{route}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return (
                    response.status,
                    response.read().decode("utf-8"),
                    response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as http_error:
            return http_error.code, http_error.read().decode("utf-8"), ""

    def request_bytes(self, route: str) -> tuple[int, bytes, str]:
        """对服务发一次真实 HTTP 请求，返回原始字节与内容类型。

        图片这类应答的字节不是合法 UTF-8（PNG 签名首字节就是 ``0x89``），
        :meth:`request_text` 解码时会直接抛错，因此二进制核对必须走这条。

        Args:
            route (str): 路由（含查询串）。

        Returns:
            tuple[int, bytes, str]: 状态码、正文字节与 ``Content-Type``。
        """
        request = urllib.request.Request(f"http://{_LOOPBACK_HOST}:{self.port}{route}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return (
                    response.status,
                    response.read(),
                    response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as http_error:
            return http_error.code, http_error.read(), ""


def _run_git(repository_path: Path, *git_arguments: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_arguments],
        cwd=repository_path,
        capture_output=True,
        text=True,
        check=True,
    )


def _staged_paths(repository_root: Path) -> list[str]:
    """列出索引里相对 HEAD 有改动的路径（与终端 ``git diff --cached --name-only`` 逐项一致）。"""
    return _run_git(repository_root, "diff", "--cached", "--name-only").stdout.split()


def _find_free_port() -> int:
    """取一个本机回环上空闲的端口号。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation_socket:
        reservation_socket.bind((_LOOPBACK_HOST, 0))
        return int(reservation_socket.getsockname()[1])


def _find_defunct_process_id() -> int:
    """取一个确定已经退出的进程号。

    用 ``subprocess`` 起一个立刻退出的进程并 ``wait()`` 收尸，拿到的 pid 在随后的
    探测里必然被判为「进程已退出」——这正是陈旧登记用例需要的输入。
    """
    defunct_process = subprocess.Popen([sys.executable, "-c", "pass"])
    defunct_process.wait()
    return defunct_process.pid


def _build_fixture_repository(repository_root: Path) -> Path:
    """建一个带真实提交、重命名、二进制与超限文件的小仓库。

    三个改动分区各有内容，且刻意造出两种边界：

    - ``src/module.py`` 暂存之后又被修改，因此**同时出现在已暂存与未暂存两段**，两段的
      逐行改动还不一样——只按路径取 diff 会拿到另一段的内容。
    - ``draft.md → final.md`` 是「重命名 + 改一行」并且**改动也进了索引**：只有两段都在
      同一段内，才能验出「配对失效 → 整篇算成新增」这个缺陷。

    ``docs/`` 下另有三个受控且未改动的文件，供预览用例使用：``guide.md``（标题 + 表格 +
    围栏代码 + 四类需要区别对待的相对引用，足以判别渲染是否真的发生、以及引用有没有被改写）、
    ``page.html`` 与它相对引用的 ``page.css``
    （判别 ``/raw/`` 是否保持路径原样）。三个都进首个提交，因此不会落进任何改动分区。

    ``guide.md`` 里的四类引用：同目录图片 ``pixel.png``（要改写成 ``/raw/docs/pixel.png``）、
    指向另一份文档的 ``../final.md``（要改写成查看器直达链接）、带 scheme 的图片
    （一个字都不许动）、纯片段 ``#指南``（同上）；另加一段手写的 raw HTML ``<img>``，
    它与 markdown 语法的图片走同一条改写路径。

    图片改动另造四份形态：``docs/photo.png`` 改两次且一次进了索引（同时出现在已暂存与未暂存
    两段）、``docs/doomed.png`` 被暂存删除、``docs/old-photo.png → docs/moved-photo.png``
    重命名且改了内容、``docs/new-photo.png`` 未跟踪。四种形态各对应一条「旧侧该取哪一版」的
    判断，错一种就会在界面上把删除说成新增、或把重命名说成新增。
    """
    repository_root.mkdir(parents=True, exist_ok=True)
    _run_git(repository_root, "init", "-b", "main")
    _run_git(repository_root, "config", "user.email", "guard@example.com")
    _run_git(repository_root, "config", "user.name", "guard-test")

    (repository_root / "src").mkdir()
    (repository_root / "src" / "module.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8"
    )
    # 目录名像构建产物的**受控源码**：按目录名剪枝会把它整棵藏掉，因此这里真的放一份。
    (repository_root / "scripts" / "build").mkdir(parents=True)
    (repository_root / "scripts" / "build" / "tool.py").write_text(
        "BUILD_TOOL = True\n", encoding="utf-8"
    )
    (repository_root / "rename_me.txt").write_text("rename source\n", encoding="utf-8")
    (repository_root / "draft.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (repository_root / "big.txt").write_text("x" * _OVERSIZE_BYTE_COUNT, encoding="utf-8")
    (repository_root / "blob.bin").write_bytes(b"\x00\x01\x02binary payload")
    (repository_root / "docs").mkdir()
    (repository_root / "docs" / "guide.md").write_text(
        "# 指南\n\n正文段落。\n\n| 列 | 值 |\n| --- | --- |\n| a | 1 |\n\n"
        "```python\nprint(1)\n```\n\n"
        "![像素](pixel.png)\n\n"
        "![网图](https://example.invalid/a.png)\n\n"
        "[另一份文档](../final.md)\n\n"
        "[回到指南](#指南)\n\n"
        '<img src="pixel.png" alt="手写的">\n',
        encoding="utf-8",
    )
    (repository_root / "docs" / "page.html").write_text(
        '<!DOCTYPE html>\n<html><head><link rel="stylesheet" href="page.css"></head>'
        '<body><h1 id="page-title">页面</h1></body></html>\n',
        encoding="utf-8",
    )
    (repository_root / "docs" / "page.css").write_text("h1 { color: red; }\n", encoding="utf-8")
    # 每个可预览的图片后缀各放一份（守卫测试不解码，只要字节不是 UTF-8 即可），另放一份
    # 超限的用于钉「图片预览不受 256 KiB 上限约束」。
    for image_suffix in _IMAGE_SUFFIXES_UNDER_TEST:
        (repository_root / "docs" / f"pixel{image_suffix}").write_bytes(_IMAGE_SIGNATURE_BYTES)
    (repository_root / "docs" / "huge.png").write_bytes(
        _IMAGE_SIGNATURE_BYTES + b"\x00" * _OVERSIZE_BYTE_COUNT
    )
    # 待会儿要造改动状态的四张图：改了两次的、要删的、重命名的、未跟踪的。
    for image_relative_path, image_bytes in _IMAGE_VERSIONS_BEFORE_CHANGES.items():
        (repository_root / image_relative_path).write_bytes(image_bytes)
    _run_git(repository_root, "add", ".")
    _run_git(repository_root, "commit", "-m", "init")

    # --- 已暂存：一次纯重命名、一次「重命名 + 改一行」、一次只改了正文的修改 ---
    _run_git(repository_root, "mv", "rename_me.txt", "renamed.txt")
    _run_git(repository_root, "mv", "draft.md", "final.md")
    (repository_root / "final.md").write_text("alpha\nBETA\ngamma\n", encoding="utf-8")
    _run_git(repository_root, "add", "final.md")
    (repository_root / "src" / "module.py").write_text(
        "def answer():\n    return 43\n", encoding="utf-8"
    )
    _run_git(repository_root, "add", "src/module.py")

    # --- 未暂存：同一个文件再改一次，外加一次二进制修改；另加未跟踪文件与未忽略的依赖目录 ---
    (repository_root / "src" / "module.py").write_text(
        "def answer():\n    return 44\n", encoding="utf-8"
    )
    (repository_root / "blob.bin").write_bytes(b"\x00\x01\x02changed payload")
    (repository_root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repository_root / "untracked.bin").write_bytes(b"\x00\x01new binary payload")
    (repository_root / "node_modules").mkdir()
    (repository_root / "node_modules" / "left-pad.js").write_text(
        "module.exports = 1\n", encoding="utf-8"
    )

    # --- 图片改动：四种形态各造一份，用于「改动里旧新两版对比」的用例 ---
    # 已暂存：同一张图先改一次进索引，之后又改一次留给未暂存——于是它同时出现在两段里，
    # 两段的两版都不一样，这正是「索引既是新侧也是旧侧」那个容易搞混的地方。
    (repository_root / "docs" / "photo.png").write_bytes(_IMAGE_SIGNATURE_BYTES + b"photo-v2")
    _run_git(repository_root, "add", "docs/photo.png")
    (repository_root / "docs" / "photo.png").write_bytes(_IMAGE_SIGNATURE_BYTES + b"photo-v3")
    # 已暂存：删除一张图（新的一版里没有它）。
    _run_git(repository_root, "rm", "-q", "docs/doomed.png")
    # 已暂存：重命名 + 改内容——旧侧要按**旧路径**取，否则会被说成「新增」。
    _run_git(repository_root, "mv", "old-photo.png", "moved-photo.png")
    (repository_root / "moved-photo.png").write_bytes(_flip_one_byte(_RENAME_IMAGE_BYTES))
    _run_git(repository_root, "add", "moved-photo.png")
    # 未跟踪：新图片（只有工作区这一版）。
    (repository_root / "docs" / "new-photo.png").write_bytes(_IMAGE_SIGNATURE_BYTES + b"new-photo")
    return repository_root


@pytest.fixture
def fixture_repository(tmp_path: Path) -> Path:
    """提供一个带真实 git 状态的小仓库。"""
    return _build_fixture_repository(tmp_path / "fixture-repo")


def _wait_until_port_is_listening(port: int) -> bool:
    """轮询端口直到可连接。"""
    deadline_monotonic = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline_monotonic:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.settimeout(0.5)
            if probe_socket.connect_ex((_LOOPBACK_HOST, port)) == 0:
                return True
        time.sleep(_SERVER_POLL_INTERVAL_SECONDS)
    return False


@pytest.fixture
def running_view_server(fixture_repository: Path) -> RunningViewServer:
    """拉起一个真实服务进程，并在用例结束后确认它已被收掉。

    清理走「终止进程 + 清登记」，与 ``just view --stop`` 同一条路径：守卫测试自己
    不能变成孤儿进程的来源，这正是 PRD 记录过的那条教训。
    """
    port = _find_free_port()
    server_process = subprocess.Popen(
        [
            sys.executable,
            str(_SERVER_SCRIPT_PATH),
            "--repo-root",
            str(fixture_repository),
            "--port",
            str(port),
            "--idle-timeout",
            "0",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert _wait_until_port_is_listening(
        port
    ), f"查看器服务在 {_SERVER_START_TIMEOUT_SECONDS:.0f} 秒内没有开始监听端口 {port}"
    try:
        yield RunningViewServer(
            repository_root=fixture_repository,
            port=port,
            process_id=server_process.pid,
        )
    finally:
        server_process.terminate()
        try:
            server_process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait(timeout=_STOP_TIMEOUT_SECONDS)


def _run_launch(repository_root: Path, *launch_arguments: str) -> subprocess.CompletedProcess[str]:
    """在指定仓库里以真实入口运行 ``launch.py``。"""
    return subprocess.run(
        [sys.executable, str(_LAUNCH_SCRIPT_PATH), *launch_arguments],
        cwd=repository_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _run_launch_on_port(
    repository_root: Path, viewer_port: int, *launch_arguments: str
) -> subprocess.CompletedProcess[str]:
    """在**显式指定的端口**上以真实入口运行 ``launch.py``。

    这些用例刻意不走默认端口：不传 ``--port`` 时 launch 会优先用默认端口，而默认端口是**整台
    机器共用的一个常量**（``DEFAULT_VIEWER_PORT``，故意没做环境变量开关——给生产代码加测试
    专用旋钮是不行的）。``just test`` 默认 ``-n auto`` 并行，一个用例把自己的实例收掉之后，
    另一个用例的实例可能正好绑上同一个端口，于是「端口不再被监听」这类断言看到的是**别人的**
    服务，报出与代码无关的红。端口号不是这几条用例要验的东西（它们验的是复用登记与回收），
    各自给一个不会撞的空闲端口即可。

    ``--stop`` 不需要走这里：回收读登记里的端口，与 ``--port`` 无关。

    Args:
        repository_root (Path): 被操作的仓库。
        viewer_port (int): 本次实例要用的端口，由调用方用 :func:`_find_free_port` 取。
        *launch_arguments (str): 其余 launch 参数。

    Returns:
        subprocess.CompletedProcess[str]: 完成结果。
    """
    return _run_launch(repository_root, "--port", str(viewer_port), *launch_arguments)


def _stop_recorded_instance(repository_root: Path) -> None:
    """按登记收掉服务进程并清理登记，供用例结束时兜底。"""
    recorded_instance = instance.read_instance_record(repository_root)
    if recorded_instance is not None:
        try:
            os.kill(recorded_instance.process_id, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    instance.clear_instance_record(repository_root)


def test_repository_escape_requests_are_refused(running_view_server: RunningViewServer) -> None:
    """``..``、绝对路径与符号链接三种逃逸都必须被拒，且不回声绝对路径。"""
    outside_file_path = running_view_server.repository_root.parent / "outside-secret.txt"
    outside_file_path.write_text("SECRET-OUTSIDE-CONTENT\n", encoding="utf-8")
    escape_link_path = running_view_server.repository_root / "escape-link"
    escape_link_path.symlink_to(outside_file_path)

    for escape_route in (
        "/api/file?path=../outside-secret.txt",
        "/api/file?path=../../outside-secret.txt",
        "/api/file?path=/etc/hosts",
        "/api/file?path=escape-link",
        "/api/diff?path=../outside-secret.txt&section=unstaged",
    ):
        status_code, response_body = running_view_server.request(escape_route)
        assert status_code == 403, f"{escape_route} 未被拒绝：{status_code} {response_body}"
        response_text = json.dumps(response_body, ensure_ascii=False)
        assert "SECRET-OUTSIDE-CONTENT" not in response_text
        assert str(running_view_server.repository_root) not in response_text
        assert "/etc/hosts" not in response_text


def test_malformed_path_parameters_are_refused_not_dropped(
    running_view_server: RunningViewServer,
) -> None:
    """畸形路径参数（内嵌 NUL 字节）必须得到拒绝应答，而不是让连接被丢弃。

    路径里带 NUL 时 ``Path.resolve()`` 抛的是 ``ValueError`` 而不是 ``OSError``；只捕
    ``OSError`` 会让异常穿透到 HTTP 层，客户端拿不到任何应答（``curl`` 显示 000），
    服务端日志则留下一条 traceback——拒绝语义从「明确拒绝」退化成「无应答」。
    """
    for malformed_route in (
        "/api/file?path=module.py%00.txt",
        "/api/file?path=..%00",
        "/api/diff?path=..%00&section=unstaged",
    ):
        status_code, response_body = running_view_server.request(malformed_route)
        assert status_code == 403, f"{malformed_route} 未被拒绝：{status_code} {response_body}"
        assert "越出仓库范围" in response_body["error"]


def test_write_methods_outside_the_stage_route_are_refused(
    running_view_server: RunningViewServer,
) -> None:
    """只有 ``POST /api/stage`` 是写口：其它路由上的 POST、以及全部 PUT/DELETE/PATCH 一律 405。"""
    for write_method in ("PUT", "DELETE", "PATCH"):
        status_code, response_body = running_view_server.request(
            "/api/file?path=src/module.py", method=write_method
        )
        assert status_code == 405, f"{write_method} 未被拒绝：{status_code} {response_body}"
        assert "拒绝" in response_body["error"]

    # 指错路由的 POST 同样不是写口：白名单只有一条。
    misplaced_status, misplaced_body = running_view_server.request(
        "/api/file?path=src/module.py", method="POST", json_body={"scope": "all"}
    )
    assert misplaced_status == 405, misplaced_body
    assert "拒绝" in misplaced_body["error"]

    unknown_route_status, unknown_route_body = running_view_server.request("/api/not-whitelisted")
    assert unknown_route_status == 404
    assert "未知路由" in unknown_route_body["error"]

    asset_escape_status, asset_escape_body = running_view_server.request("/assets/../server.py")
    assert asset_escape_status == 404, asset_escape_body


def test_server_source_has_exactly_one_write_method_handler() -> None:
    """服务端源码里只能有**一个**写方法处理器，且必须是 ``do_POST``。

    这条静态断言与「写边界只剩暂存」互为因果：一旦有人为了「更明确地拒绝」而给 PUT /
    DELETE / PATCH 定义同名方法，写边界的可审查性就没了；而多出第二个写入口（比如某个
    顺手加上的 ``do_DELETE``）也会在这里显形——``rg -n "def do_" scripts/shared/view/``
    扫一眼能数清写入口，是这条边界唯一的看护方式。
    """
    server_source_text = _SERVER_SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden_handler_name in _FORBIDDEN_WRITE_METHOD_HANDLER_NAMES:
        assert forbidden_handler_name not in server_source_text
    assert (
        server_source_text.count(f"def {_ALLOWED_WRITE_METHOD_HANDLER_NAME}") == 1
    ), "服务端应当只有一个写方法处理器（暂存用），实际有多处或没有"


def test_server_binds_loopback_address_only(fixture_repository: Path) -> None:
    """服务绑定地址必须就是 ``127.0.0.1``，不是 ``0.0.0.0``。"""
    viewer_server = server.ViewerHTTPServer(
        server.ServerConfiguration(
            repository_root=fixture_repository,
            port=0,
            idle_timeout_seconds=0,
        ),
        server.ViewerRequestHandler,
    )
    try:
        assert viewer_server.server_address[0] == _LOOPBACK_HOST
    finally:
        viewer_server.server_close()


def test_source_is_served_verbatim_with_line_numbers(
    running_view_server: RunningViewServer,
) -> None:
    """文件正文必须逐行等于本地文件，且带语法高亮的行级标记。"""
    status_code, response_body = running_view_server.request("/api/file?path=src/module.py")

    assert status_code == 200
    assert response_body["kind"] == "text"
    assert response_body["highlighted"] is True
    local_source_lines = (
        (running_view_server.repository_root / "src" / "module.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert response_body["line_count"] == len(local_source_lines)
    assert len(response_body["lines"]) == len(local_source_lines)
    for line_index, local_source_line in enumerate(local_source_lines):
        rendered_line_html = response_body["lines"][line_index]
        assert _strip_html_tags(rendered_line_html) == local_source_line
    # 每行自身标签平衡：跨行 token span 必须在行边界闭合再重开，否则浏览器会把
    # 后面所有行的排版一起吃掉。
    for rendered_line_html in response_body["lines"]:
        assert rendered_line_html.count("<span") == rendered_line_html.count("</span>")


def test_tracked_files_win_over_directory_name_pruning(
    running_view_server: RunningViewServer,
) -> None:
    """受控文件即使在名为 ``build`` 的目录下也必须出现在文件树里。

    这条守的是一个真实踩过的坑：按目录名剪枝会把仓库里 ``scripts/build/`` 这类**源码**
    整棵藏掉，而文件树看上去仍然"有内容"，没人会注意到少了什么。目录名剪枝只该作用在
    未跟踪文件上；受控文件是「这不是构建产物」的权威信号。
    """
    status_code, response_body = running_view_server.request("/api/tree")
    assert status_code == 200
    tree_paths = set(response_body["paths"])

    assert "scripts/build/tool.py" in tree_paths
    assert "src/module.py" in tree_paths
    # 未跟踪且未被忽略的依赖目录仍要挡在文件树外。
    assert not any(tree_path.startswith("node_modules/") for tree_path in tree_paths)


def test_oversize_and_binary_files_are_marked_not_rendered(
    running_view_server: RunningViewServer,
) -> None:
    """超限与二进制文件返回明确标记，不返回正文也不留空白。"""
    oversize_status, oversize_body = running_view_server.request("/api/file?path=big.txt")
    assert oversize_status == 200
    assert oversize_body["kind"] == "oversize"
    assert oversize_body["limit_label"] == "256 KiB"
    assert "lines" not in oversize_body
    assert oversize_body["note"]

    binary_status, binary_body = running_view_server.request("/api/file?path=blob.bin")
    assert binary_status == 200
    assert binary_body["kind"] == "binary"
    assert "lines" not in binary_body
    assert binary_body["note"]


def test_markdown_preview_is_rendered_on_demand(
    running_view_server: RunningViewServer,
) -> None:
    """Markdown 的预览按需渲染：正文接口只报告能力，渲染结果由预览接口单独给出。

    两件事一起守：``/api/file`` 必须继续逐行等于磁盘原文（预览不能把源码顶掉），而
    ``/api/markdown`` 必须真的走了 Markdown 渲染——标题、表格、围栏代码都变成标签，
    而不是原文的 Markdown 语法。
    """
    file_status, file_body = running_view_server.request("/api/file?path=docs/guide.md")
    assert file_status == 200
    assert file_body["preview"] == {"mode": "markdown"}
    local_source_lines = (
        (running_view_server.repository_root / "docs" / "guide.md")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert file_body["line_count"] == len(local_source_lines)

    preview_status, preview_body = running_view_server.request("/api/markdown?path=docs/guide.md")
    assert preview_status == 200
    assert preview_body["format"] == "markdown"
    rendered_html = preview_body["html"]
    assert "<h1" in rendered_html
    assert "<table>" in rendered_html
    assert "<pre>" in rendered_html
    # 渲染过的反面证据：原文里的 Markdown 语法不该原样留在输出里。
    assert "| 列 | 值 |" not in rendered_html
    assert "```" not in rendered_html


def test_markdown_preview_rewrites_relative_references(
    running_view_server: RunningViewServer,
) -> None:
    """预览片段里的相对引用必须改写成能真正取到字节的地址，且只改该改的那几种。

    预览片段是内联进查看器页面（``/``）的，相对引用照着页面根解析必然落空——图片就是这么
    变成一片空白的。四类引用一起守：两种要改写（同目录图片、指向另一份文档的链接），两种
    一个字都不许动（带 scheme 的、纯片段的）。手写的 raw HTML ``<img>`` 与 markdown 语法的
    图片走同一条路，因此两条都得改。
    """
    status_code, response_body = running_view_server.request("/api/markdown?path=docs/guide.md")
    assert status_code == 200
    rendered_html = response_body["html"]

    # 同目录图片：markdown 语法那处与手写 raw HTML 那处都指向 /raw/，不再留下裸相对值。
    assert rendered_html.count('src="/raw/docs/pixel.png"') == 2
    assert 'src="pixel.png"' not in rendered_html
    # 指向另一份文档：改成查看器直达链接，点一下就在查看器里读它，而不是一屏原始 markdown。
    assert 'href="/?view=files&path=final.md"' in rendered_html
    assert 'href="../final.md"' not in rendered_html
    # 带 scheme 的与纯片段的一个字都不许动。
    assert 'src="https://example.invalid/a.png"' in rendered_html
    assert 'href="#指南"' in rendered_html

    # 改写出来的地址必须真的取得回来：只断言字符串的话，「改成了另一个同样取不到的地址」
    # 也能让用例变绿。
    raw_status, raw_bytes, raw_content_type = running_view_server.request_bytes(
        "/raw/docs/pixel.png"
    )
    assert raw_status == 200
    assert raw_content_type.startswith("image/")
    assert raw_bytes == _IMAGE_SIGNATURE_BYTES


def test_preview_descriptor_is_null_for_files_without_preview(
    running_view_server: RunningViewServer,
) -> None:
    """没有预览方式的文件必须给出 null，界面据此不摆预览控件。

    凭空给一个能力，界面就会摆出一个点了报错的开关。
    """
    status_code, response_body = running_view_server.request("/api/file?path=src/module.py")
    assert status_code == 200
    assert response_body["preview"] is None


def test_markdown_preview_refuses_non_markdown_and_missing_files(
    running_view_server: RunningViewServer,
) -> None:
    """预览接口对非 Markdown、缺失与超限文件给明确拒绝，不静默渲染出别的东西。

    ``big.txt`` 与 ``blob.bin`` 也在名单里：正文侧对它们走的是 oversize / binary 分支，
    预览侧如果放行就等于绕开那条上限。
    """
    for non_markdown_path in ("src/module.py", "docs/page.html", "blob.bin", "big.txt"):
        status_code, response_body = running_view_server.request(
            f"/api/markdown?path={non_markdown_path}"
        )
        assert status_code == 400, f"{non_markdown_path} 未被拒绝：{response_body}"
        assert "不是 Markdown 文件" in response_body["error"]

    missing_status, missing_body = running_view_server.request("/api/markdown?path=docs/nope.md")
    assert missing_status == 404, missing_body
    assert "未找到文件" in missing_body["error"]


def test_html_preview_is_served_verbatim_for_a_new_tab(
    running_view_server: RunningViewServer,
) -> None:
    """HTML 不在查看器里内联渲染：原样供出，由界面在新标签页打开。

    ``/raw/`` 保持路径原样，所以同一目录下的相对资源（这里是被 ``page.html`` 相对引用
    的 ``page.css``）必须能取到——这正是它不能走静态资源文件名白名单的原因。字节也必须
    与磁盘逐字节一致，否则「在新标签页打开」看到的就不是这个文件。
    """
    repository_root = running_view_server.repository_root
    file_status, file_body = running_view_server.request("/api/file?path=docs/page.html")
    assert file_status == 200
    assert file_body["preview"] == {"mode": "external", "url": "/raw/docs/page.html"}

    raw_status, raw_text, raw_content_type = running_view_server.request_text("/raw/docs/page.html")
    assert raw_status == 200
    assert raw_content_type.startswith("text/html")
    assert raw_text == (repository_root / "docs" / "page.html").read_text(encoding="utf-8")

    css_status, css_text, css_content_type = running_view_server.request_text("/raw/docs/page.css")
    assert css_status == 200
    assert css_content_type.startswith("text/css")
    assert css_text == (repository_root / "docs" / "page.css").read_text(encoding="utf-8")


def test_raw_route_keeps_non_html_suffixes_off_the_html_content_type(
    running_view_server: RunningViewServer,
) -> None:
    """表外后缀一律以二进制流供出，绝不落成 ``text/html``。

    服务端同时发 ``X-Content-Type-Options: nosniff``，两道合起来才挡住「一个 .txt 被
    浏览器按内容嗅探成 HTML 执行」。
    """
    for non_html_path in ("src/module.py", "big.txt", "untracked.txt"):
        status_code, _response_text, content_type = running_view_server.request_text(
            f"/raw/{non_html_path}"
        )
        assert status_code == 200, non_html_path
        assert content_type == "application/octet-stream", f"{non_html_path}: {content_type}"


def test_raw_route_escape_requests_are_refused(running_view_server: RunningViewServer) -> None:
    """``/raw/`` 保持路径原样，但越界、目录与缺失必须各自给出明确应答。

    这条路由是全服务里唯一一条路径不被压成文件名的读取路由，因此它的越界断言要在真实
    HTTP 上被钉住：``%2E%2E`` 解码后才暴露层级、符号链接只有 ``resolve()`` 之后才指向
    仓库外，两种都要落到同一条拒绝上；拒绝信息同样不得回声任何绝对路径。
    """
    outside_file_path = running_view_server.repository_root.parent / "raw-outside.txt"
    outside_file_path.write_text("SECRET-RAW-OUTSIDE\n", encoding="utf-8")
    escape_link_path = running_view_server.repository_root / "raw-escape-link"
    escape_link_path.symlink_to(outside_file_path)

    for escape_route in (
        "/raw/%2E%2E/raw-outside.txt",
        "/raw/%2E%2E%2Fraw-outside.txt",
        "/raw/raw-escape-link",
        "/raw/%2Fetc%2Fhosts",
    ):
        status_code, response_body = running_view_server.request(escape_route)
        assert status_code == 403, f"{escape_route} 未被拒绝：{status_code} {response_body}"
        response_text = json.dumps(response_body, ensure_ascii=False)
        assert "SECRET-RAW-OUTSIDE" not in response_text
        assert str(running_view_server.repository_root) not in response_text

    # 目录（含仓库根本身）不能当文件供出：那会把一个目录列表变成可浏览的入口。
    for directory_route in ("/raw/", "/raw/docs", "/raw/docs/"):
        directory_status, directory_body = running_view_server.request(directory_route)
        assert directory_status == 400, f"{directory_route} 未被拒绝：{directory_body}"
        assert "是一个目录" in directory_body["error"]

    missing_status, missing_body = running_view_server.request("/raw/docs/nope.html")
    assert missing_status == 404, missing_body
    assert "未找到文件" in missing_body["error"]


def test_each_image_suffix_is_previewed_and_served_as_an_image(
    running_view_server: RunningViewServer,
) -> None:
    """图片给出 ``/raw/`` 地址，且那个地址必须以 ``image/*`` 供出。

    两件事一起守。其一，图片走的是「后缀命中即返回」，正文一次都不读——所以这里另外核对
    ``/raw/`` 的字节与磁盘逐字节一致：界面显示的那张图必须就是这个文件。其二，服务端有两份
    按后缀的表（能预览的图片后缀、``/raw/`` 的 Content-Type），漏掉任何一边都会让预览拿到
    ``application/octet-stream`` 而渲染失败，浏览器不会报错、只会是一片空白。
    """
    repository_root = running_view_server.repository_root
    for image_suffix in _IMAGE_SUFFIXES_UNDER_TEST:
        image_relative_path = f"docs/pixel{image_suffix}"

        status_code, response_body = running_view_server.request(
            f"/api/file?path={image_relative_path}"
        )
        assert status_code == 200, f"{image_relative_path} 未被读取：{response_body}"
        assert response_body["kind"] == "image", image_relative_path
        assert response_body["url"] == f"/raw/{image_relative_path}"
        assert "lines" not in response_body
        assert (
            response_body["size"]
            == (repository_root / "docs" / f"pixel{image_suffix}").stat().st_size
        )

        raw_status, raw_bytes, raw_content_type = running_view_server.request_bytes(
            f"/raw/{image_relative_path}"
        )
        assert raw_status == 200, image_relative_path
        assert raw_content_type.startswith("image/"), f"{image_relative_path}: {raw_content_type}"
        assert raw_bytes == (repository_root / "docs" / f"pixel{image_suffix}").read_bytes()


def test_image_preview_is_not_subject_to_the_text_size_limit(
    running_view_server: RunningViewServer,
) -> None:
    """超限图片仍走图片预览，不被判成「文件过大」。

    256 KiB 上限约束的是「读进来逐行渲染的正文」；图片的字节由浏览器自己去 ``/raw/`` 取，
    服务端不读，也就没有可省的开销。截图动辄超过 256 KiB，把上限套到图片上会把最该能预览的
    那一类文件挡在门外。
    """
    repository_root = running_view_server.repository_root
    status_code, response_body = running_view_server.request("/api/file?path=docs/huge.png")
    assert status_code == 200
    assert response_body["kind"] == "image", response_body
    assert response_body["url"] == "/raw/docs/huge.png"

    raw_status, raw_bytes, raw_content_type = running_view_server.request_bytes(
        "/raw/docs/huge.png"
    )
    assert raw_status == 200
    assert raw_content_type.startswith("image/png")
    assert len(raw_bytes) == (repository_root / "docs" / "huge.png").stat().st_size
    assert len(raw_bytes) > _OVERSIZE_BYTE_COUNT


def test_image_change_offers_the_two_versions_of_that_section(
    running_view_server: RunningViewServer,
) -> None:
    """图片改动要给出本分区里旧新两版各自的地址，且两版取自哪里必须是分区说了算。

    「索引」既可能是新侧也可能是旧侧：已暂存段比 HEAD ↔ 索引，未暂存段比索引 ↔ 工作区。
    把两段的来源记混，界面就会拿同一版当「旧」和「新」，看的人却以为自己在看 diff。
    """
    repository_root = running_view_server.repository_root
    expected_worktree_bytes = (repository_root / "docs" / "photo.png").read_bytes()

    staged_status, staged_body = running_view_server.request(
        "/api/diff?path=docs/photo.png&section=staged"
    )
    assert staged_status == 200
    staged_panes = staged_body["image_comparison"]["panes"]
    assert [pane["label"] for pane in staged_panes] == ["HEAD 版本", "索引版本"]
    assert staged_panes[0]["url"] == "/raw/docs/photo.png?rev=head"
    assert staged_panes[1]["url"] == "/raw/docs/photo.png?rev=index"
    assert staged_body["image_comparison"]["note"] == ""

    unstaged_status, unstaged_body = running_view_server.request(
        "/api/diff?path=docs/photo.png&section=unstaged"
    )
    assert unstaged_status == 200
    unstaged_panes = unstaged_body["image_comparison"]["panes"]
    assert [pane["label"] for pane in unstaged_panes] == ["索引版本", "工作区版本"]
    assert unstaged_panes[0]["url"] == "/raw/docs/photo.png?rev=index"
    # 工作区那版不带 rev，与别处引用工作区文件的写法一致。
    assert unstaged_panes[1]["url"] == "/raw/docs/photo.png"

    # 地址真的指向不同的两版：内容与 git 对象逐字节一致，且与工作区那份不同。
    _, head_bytes, head_content_type = running_view_server.request_bytes(
        "/raw/docs/photo.png?rev=head"
    )
    _, index_bytes, _ = running_view_server.request_bytes("/raw/docs/photo.png?rev=index")
    assert head_content_type.startswith("image/png")
    assert head_bytes == _IMAGE_SIGNATURE_BYTES + b"photo-v1"
    assert index_bytes == _IMAGE_SIGNATURE_BYTES + b"photo-v2"
    assert index_bytes != expected_worktree_bytes
    assert head_bytes != index_bytes


def test_image_change_notes_the_version_that_is_missing(
    running_view_server: RunningViewServer,
) -> None:
    """只取得到一版时只画一版，并说清另一版为什么不在。

    删除与「未跟踪」这两种情形的旧侧都取不到，但原因完全不同：前者是这次改动删了它，后者
    是它还没进过索引。说成同一句话就会把删除报成「新增」或者反过来，而两者对读者意味着相反
    的事实。
    """
    deleted_status, deleted_body = running_view_server.request(
        "/api/diff?path=docs/doomed.png&section=staged"
    )
    assert deleted_status == 200
    deleted_comparison = deleted_body["image_comparison"]
    assert [pane["label"] for pane in deleted_comparison["panes"]] == ["HEAD 版本"]
    assert "删除" in deleted_comparison["note"]

    untracked_status, untracked_body = running_view_server.request(
        "/api/diff?path=docs/new-photo.png&section=untracked"
    )
    assert untracked_status == 200
    untracked_comparison = untracked_body["image_comparison"]
    assert [pane["label"] for pane in untracked_comparison["panes"]] == ["工作区版本"]
    assert "未跟踪" in untracked_comparison["note"]
    assert "删除" not in untracked_comparison["note"]


def test_renamed_image_takes_its_old_side_from_the_old_path(
    running_view_server: RunningViewServer,
) -> None:
    """重命名且改了内容的图片，旧侧必须按旧路径取，标签里也要写出旧路径。

    按新路径去 HEAD 里取只会取不到（那一版里新路径根本不存在），于是界面把一次重命名报成
    「新增」，还配一句「这次改动新增了它」——一个明确错误的结论。
    """
    status_code, response_body = running_view_server.request(
        "/api/diff?path=moved-photo.png&section=staged"
    )
    assert status_code == 200
    assert response_body["rename_from"] == "old-photo.png"

    comparison = response_body["image_comparison"]
    assert [pane["label"] for pane in comparison["panes"]] == [
        "HEAD 版本（old-photo.png）",
        "索引版本",
    ]
    assert comparison["panes"][0]["url"] == "/raw/old-photo.png?rev=head"
    assert comparison["note"] == ""

    _, head_bytes, _ = running_view_server.request_bytes("/raw/old-photo.png?rev=head")
    assert head_bytes == _RENAME_IMAGE_BYTES


def test_image_comparison_is_absent_outside_its_section_and_for_non_images(
    running_view_server: RunningViewServer,
) -> None:
    """不该出现对比的地方一律不给：不在本分区的路径、非图片的二进制、普通文本。

    不在本分区时两版的 blob 照样存在（工作区文件在磁盘上、索引条目也在），只看后缀与文件
    类型会为一条并不存在的改动凑出一对图；未跟踪那一段还会顺手说一句「还没有进过索引」——
    对已跟踪的文件来说那是假话。
    """
    out_of_section_status, out_of_section_body = running_view_server.request(
        "/api/diff?path=docs/photo.png&section=untracked"
    )
    assert out_of_section_status == 200
    assert out_of_section_body["image_comparison"] is None

    binary_status, binary_body = running_view_server.request(
        "/api/diff?path=untracked.bin&section=untracked"
    )
    assert binary_status == 200
    assert binary_body["image_comparison"] is None

    text_status, text_body = running_view_server.request(
        "/api/diff?path=src/module.py&section=unstaged"
    )
    assert text_status == 200
    assert text_body["image_comparison"] is None


def test_revision_raw_route_serves_blob_bytes_and_refuses_the_rest(
    running_view_server: RunningViewServer,
) -> None:
    """``/raw/`` 的 ``rev`` 参数只认封闭枚举，字节取自对象库，边界与工作区那侧一致。

    版本取值是任人可填的查询参数，因此它既不能作为字符串流进 git，也不能因为换了数据来源
    就绕过越界断言——两条一起在这里钉住。
    """
    status_code, head_bytes, content_type = running_view_server.request_bytes(
        "/raw/docs/photo.png?rev=head"
    )
    assert status_code == 200
    assert content_type.startswith("image/png")
    assert head_bytes != (running_view_server.repository_root / "docs" / "photo.png").read_bytes()

    unknown_status, unknown_body = running_view_server.request("/raw/docs/photo.png?rev=bogus")
    assert unknown_status == 400, unknown_body
    assert "不是可用的版本" in unknown_body["error"]

    # 该版本里没有这个文件：未跟踪的图片在 HEAD 与索引里都不存在。
    for missing_route in ("/raw/docs/new-photo.png?rev=head", "/raw/docs/new-photo.png?rev=index"):
        missing_status, missing_body = running_view_server.request(missing_route)
        assert missing_status == 404, f"{missing_route} 未被拒绝：{missing_body}"
        assert "该版本里没有这个文件" in missing_body["error"]

    # 越界与目录在带 rev 时同样被拒。
    for refused_route in ("/raw/%2E%2E/main.py?rev=head", "/raw/docs?rev=head"):
        refused_status, refused_body = running_view_server.request(refused_route)
        assert refused_status in {400, 403}, f"{refused_route} 未被拒绝：{refused_body}"
        response_text = json.dumps(refused_body, ensure_ascii=False)
        assert str(running_view_server.repository_root) not in response_text


def test_stage_route_puts_a_path_into_the_index(
    running_view_server: RunningViewServer,
) -> None:
    """``POST /api/stage`` 的 ``scope=path`` 把该路径的当前状态收进索引，且只动它一个。

    「只动它一个」与「能暂存」同样重要：写口一旦越界多暂存了别的改动，用户就没法再靠查看器
    把一处改动单独收进索引——那正是这个口子存在的全部理由。夹具里本来就有一批已暂存条目，
    因此这里比的是**前后差集**，而不是「索引里只有它」。
    """
    repository_root = running_view_server.repository_root
    staged_before = _staged_paths(repository_root)

    # 拿一个**还没进过索引**的未跟踪文件：它既是最常见的那一下点击，也最能暴露「顺手多暂存
    # 了别的改动」。
    status_code, response_body = running_view_server.request(
        "/api/stage", method="POST", json_body={"scope": "path", "path": "untracked.txt"}
    )
    assert status_code == 200, response_body
    assert response_body["scope"] == "path"
    assert response_body["path"] == "untracked.txt"

    staged_after = _staged_paths(repository_root)
    assert set(staged_after) - set(staged_before) == {"untracked.txt"}
    assert set(staged_before) - set(staged_after) == set()
    # 同一时刻还有别的未暂存改动（blob.bin、docs/photo.png），它们必须留在原地。
    assert "blob.bin" not in staged_after


def test_stage_route_with_scope_all_takes_every_change(
    running_view_server: RunningViewServer,
) -> None:
    """``scope=all`` 对应「改动」那一段列出的东西：已改的、已删的、新的都在内，忽略的不在。"""
    repository_root = running_view_server.repository_root
    # 造一次「工作区删掉了受控文件」：``-A`` 必须把这次删除也收进索引，而不是只收修改与新增。
    (repository_root / "big.txt").unlink()
    (repository_root / ".gitignore").write_text("build/\n", encoding="utf-8")
    ignored_path = repository_root / "build" / "ignored.txt"
    ignored_path.parent.mkdir(exist_ok=True)
    ignored_path.write_text("ignored\n", encoding="utf-8")

    status_code, response_body = running_view_server.request(
        "/api/stage", method="POST", json_body={"scope": "all"}
    )
    assert status_code == 200, response_body
    assert response_body["scope"] == "all"

    staged_paths = _staged_paths(repository_root)
    assert "src/module.py" in staged_paths
    assert "untracked.txt" in staged_paths
    assert "untracked.bin" in staged_paths
    assert "blob.bin" in staged_paths
    assert "big.txt" in staged_paths
    assert ".gitignore" in staged_paths
    # ``-A`` 不碰被忽略的路径：那是「这不是源码」的权威信号。
    assert not any(staged_path.startswith("build/") for staged_path in staged_paths)


def test_stage_route_refuses_everything_but_a_json_object(
    running_view_server: RunningViewServer,
) -> None:
    """写口只认 JSON 对象：非 JSON 类型 415、坏 scope 与缺 path 400、越界 403，且都不改索引。

    415 那一条是这里最要紧的防线，而不是格式洁癖：跨站表单式 POST 属于「简单请求」，浏览器
    会**直接发出去**（恶意页面读不到响应，但副作用已经发生）。要求 JSON 会把跨站请求变成
    预检请求，而本服务不返回任何 CORS 头，预检必然失败。
    """
    repository_root = running_view_server.repository_root
    staged_before = _staged_paths(repository_root)

    # 不带 Content-Type（= 浏览器跨站表单的自然形态）→ 415。
    not_json_status, not_json_body = running_view_server.request("/api/stage", method="POST")
    assert not_json_status == 415, not_json_body
    assert "application/json" in not_json_body["error"]

    for refused_body in ({"scope": "bogus"}, {"scope": "path"}, {"scope": "path", "path": ""}):
        status_code, response_body = running_view_server.request(
            "/api/stage", method="POST", json_body=refused_body
        )
        assert status_code == 400, f"{refused_body} 未被拒绝：{response_body}"

    out_of_scope_path = repository_root.parent / "stage-outside-secret.txt"
    out_of_scope_path.write_text("SECRET-OUTSIDE-CONTENT\n", encoding="utf-8")
    escape_status, escape_body = running_view_server.request(
        "/api/stage",
        method="POST",
        json_body={"scope": "path", "path": "../stage-outside-secret.txt"},
    )
    assert escape_status == 403, escape_body
    assert "SECRET-OUTSIDE-CONTENT" not in json.dumps(escape_body, ensure_ascii=False)

    assert _staged_paths(repository_root) == staged_before


def test_each_section_matches_its_own_terminal_git_output(
    running_view_server: RunningViewServer,
) -> None:
    """三个分区的文件集合与增删统计，必须分别与终端同参数命令逐项一致。

    夹具里刻意同时放了重命名、二进制与未跟踪文件——三种 ``--numstat`` 条目形状各不相同
    （普通条目、重命名条目的双路径、``--no-index`` 的双路径），只处理其中一种会静默漏掉
    文件或统计，而界面看上去仍然"有内容"。
    """
    repository_root = running_view_server.repository_root
    status_code, response_body = running_view_server.request("/api/changes")
    assert status_code == 200

    section_by_name = {
        changed_section["section"]: changed_section for changed_section in response_body["sections"]
    }
    assert list(section_by_name) == ["staged", "unstaged", "untracked"]

    for section_name, section_arguments in (
        ("staged", ("--cached",)),
        ("unstaged", ()),
    ):
        changed_section = section_by_name[section_name]
        terminal_name_only = _run_git(
            repository_root, "diff", *section_arguments, "--name-only"
        ).stdout.split()
        assert sorted(entry["path"] for entry in changed_section["files"]) == sorted(
            terminal_name_only
        )
        assert _expected_counts_by_path(
            _run_git(repository_root, "diff", *section_arguments, "--numstat").stdout
        ) == {entry["path"]: (entry["add"], entry["del"]) for entry in changed_section["files"]}
        assert changed_section["totals"] == _expected_totals(changed_section["files"])

    # 未跟踪那一段沿用文件树的目录剪枝，因此它不会等于 ls-files 的原始输出：差额必须
    # 恰好是同样被文件树剪掉的路径。两处口径若各列各的，这条就会炸。
    untracked_section = section_by_name["untracked"]
    untracked_section_paths = [entry["path"] for entry in untracked_section["files"]]
    terminal_untracked_paths = set(
        _run_git(repository_root, "ls-files", "--others", "--exclude-standard").stdout.split()
    )
    tree_status, tree_body = running_view_server.request("/api/tree")
    assert tree_status == 200
    assert set(untracked_section_paths) == terminal_untracked_paths & set(tree_body["paths"])

    # 未跟踪文件确实进了改动视图（这条以前是反向断言：口径从「不进列表」改成「单独一段」）。
    assert "untracked.txt" in untracked_section_paths, untracked_section

    # 未跟踪那一段明确不提供增删统计：git 的列表命令不报这个数，我们不自己编。
    assert untracked_section["stats_available"] is False
    assert [entry["status"] for entry in untracked_section["files"]] == ["A"] * len(
        untracked_section_paths
    )
    assert [(entry["add"], entry["del"]) for entry in untracked_section["files"]] == [
        (None, None)
    ] * len(untracked_section_paths)

    # 提供统计的两段必须准备好这一位，界面靠它区分「不提供」与「二进制」。
    assert all(
        section_by_name[section_name]["stats_available"] is True
        for section_name in ("staged", "unstaged")
    )


def test_changes_view_prunes_untracked_dependency_directories(
    running_view_server: RunningViewServer,
) -> None:
    """未跟踪那一段沿用文件树的目录剪枝，依赖目录不得淹没改动列表。

    文件树与改动列表共用同一份未跟踪口径：两处若各列各的，``node_modules/`` 会在文件树里
    被剪掉、却在改动列表里冒出来。
    """
    status_code, response_body = running_view_server.request("/api/changes")
    assert status_code == 200
    untracked_paths = [
        entry["path"]
        for changed_section in response_body["sections"]
        if changed_section["section"] == "untracked"
        for entry in changed_section["files"]
    ]
    assert "untracked.txt" in untracked_paths
    assert not any(path.startswith("node_modules/") for path in untracked_paths), untracked_paths


def test_untracked_files_are_not_counted_as_modified(
    running_view_server: RunningViewServer,
) -> None:
    """未跟踪文件只出现在未跟踪那一段，不会被并进前两段。

    并进去就等于把「未提交的新文件」与「改过的旧文件」混成一类，而这正是分段要分清的事。
    """
    status_code, response_body = running_view_server.request("/api/changes")
    assert status_code == 200
    index_section_paths = {
        entry["path"]
        for changed_section in response_body["sections"]
        if changed_section["section"] in {"staged", "unstaged"}
        for entry in changed_section["files"]
    }
    assert "untracked.txt" not in index_section_paths
    assert "untracked.bin" not in index_section_paths


def test_same_path_in_two_sections_reports_its_own_diff(
    running_view_server: RunningViewServer,
) -> None:
    """同一个文件同时出现在两段时，两段各自的 diff 必须不同且各自正确。

    条目身份是「路径 + 分区」：``src/module.py`` 先改到 ``return 43`` 并暂存，之后又改到
    ``return 44``，因此两段的新增行分别是 43 与 44。只按路径取 diff 会让两段显示同一份
    内容，且其中一段是错的。
    """
    staged_status, staged_body = running_view_server.request(
        "/api/diff?path=src/module.py&section=staged"
    )
    unstaged_status, unstaged_body = running_view_server.request(
        "/api/diff?path=src/module.py&section=unstaged"
    )
    assert staged_status == 200
    assert unstaged_status == 200

    assert _added_row_texts(staged_body) == ["    return 43"]
    assert _added_row_texts(unstaged_body) == ["    return 44"]
    assert staged_body["section_label"] == "已暂存"
    assert unstaged_body["section_label"] == "未暂存"


def test_unknown_section_is_refused(running_view_server: RunningViewServer) -> None:
    """分区是封闭枚举：白名单外的取值一律 400，且不会被交给 git。

    以前这条守的是「基线名字会变成 git 的位置参数」，所以要对本地分支做白名单校验。改成
    分区之后，取值只用于在服务端选命令、从不进 argv，选项注入在构造上就不可能——这条断言
    从「防注入」变成了「取值集合封闭」。
    """
    for rejected_section in ("--output=/tmp/leak", "-c", "--exec=rm -rf /", "worktree", "HEAD"):
        encoded_section = urllib.parse.quote(rejected_section, safe="")
        status_code, response_body = running_view_server.request(
            f"/api/diff?path=src/module.py&section={encoded_section}"
        )
        assert status_code == 400, f"{rejected_section} 未被拒绝：{response_body}"
        assert "不是可用的改动分区" in response_body["error"]

    # 缺失 section 同样拒绝，不静默落到某个默认分区。
    missing_status, missing_body = running_view_server.request("/api/diff?path=src/module.py")
    assert missing_status == 400, missing_body
    assert "不是可用的改动分区" in missing_body["error"]


def test_untracked_binary_diff_is_reported_as_binary_not_unchanged(
    running_view_server: RunningViewServer,
) -> None:
    """未跟踪的二进制文件必须被标成二进制，不能被说成「没有改动」。

    git 对二进制只给一行 ``Binary files ... differ``，解析后没有任何逐行内容。不单独记这
    一位，界面就会把「新加了一个二进制文件」报成「该文件没有改动」——一个明确错误的结论。
    """
    status_code, response_body = running_view_server.request(
        "/api/diff?path=untracked.bin&section=untracked"
    )
    assert status_code == 200
    assert response_body["binary"] is True
    assert response_body["rows"] == []
    assert response_body["empty"] is True
    assert response_body["rename_from"] is None


def test_missing_untracked_file_is_refused(running_view_server: RunningViewServer) -> None:
    """请求一个不存在的未跟踪文件要得到明确的 404，而不是让 git 非零退出变成 500。"""
    status_code, response_body = running_view_server.request(
        "/api/diff?path=not-there.txt&section=untracked"
    )
    assert status_code == 404, response_body
    assert "未找到文件" in response_body["error"]


def test_changed_file_diff_has_line_numbers(running_view_server: RunningViewServer) -> None:
    """单文件改动要给出可着色的逐行结构与新旧行号。"""
    status_code, response_body = running_view_server.request(
        "/api/diff?path=src/module.py&section=unstaged"
    )
    assert status_code == 200
    assert response_body["empty"] is False
    added_rows = [row for row in response_body["rows"] if row["kind"] == "add"]
    assert [row["text"] for row in added_rows] == ["    return 44"], response_body["rows"]
    assert all(row["new_no"] is not None for row in added_rows)
    assert any(row["kind"] == "hunk" for row in response_body["rows"])


def test_renamed_file_diff_is_paired_with_its_source(
    running_view_server: RunningViewServer,
) -> None:
    """重命名的单文件 diff 必须带回旧路径，并且配对不能失效。

    只给新路径做 pathspec 时 git 的配对会整体失效：同一个文件被报成 ``new file
    mode``，正文整篇算成新增行，界面也就说不出它重命名自何处。夹具里那对「重命名 +
    改一行（改动也已暂存）」正是为了让这种失效可判别——配对生效时新增行只有改过的那一行。
    """
    status_code, response_body = running_view_server.request(
        "/api/diff?path=final.md&section=staged"
    )
    assert status_code == 200
    assert response_body["rename_from"] == "draft.md"
    assert response_body["empty"] is False
    assert _added_row_texts(response_body) == ["BETA"], response_body["rows"]


def test_rename_without_content_change_still_reports_its_source(
    running_view_server: RunningViewServer,
) -> None:
    """纯重命名没有逐行改动，但仍要带出旧路径，界面才能说明它被改过名。

    这里的 ``empty`` 只表示「没有逐行内容可看」，不等于「与当前内容一致」——界面必须
    靠 ``rename_from`` 把两者区分开，否则会告诉用户一个错误结论。
    """
    status_code, response_body = running_view_server.request(
        "/api/diff?path=renamed.txt&section=staged"
    )
    assert status_code == 200
    assert response_body["rename_from"] == "rename_me.txt"
    assert response_body["rows"] == []
    assert response_body["empty"] is True


def test_plain_edit_reports_no_rename_source(running_view_server: RunningViewServer) -> None:
    """普通改动文件的 ``rename_from`` 必须是 null，界面才不会凭空说它被重命名。"""
    status_code, response_body = running_view_server.request(
        "/api/diff?path=src/module.py&section=unstaged"
    )
    assert status_code == 200
    assert response_body["rename_from"] is None


def test_second_launch_reuses_the_resident_instance(fixture_repository: Path) -> None:
    """连续两次打开必须复用同一实例：进程号不变。

    两次都显式给同一个空闲端口（理由见 :func:`_run_launch_on_port`）：这里验的是复用，不是
    默认端口选得对不对。
    """
    viewer_port = _find_free_port()
    try:
        first_launch_result = _run_launch_on_port(fixture_repository, viewer_port, "--no-open")
        assert first_launch_result.returncode == 0, first_launch_result.stderr
        first_instance = instance.read_instance_record(fixture_repository)
        assert first_instance is not None
        assert first_instance.port == viewer_port
        assert _wait_until_port_is_listening(first_instance.port)

        second_launch_result = _run_launch_on_port(fixture_repository, viewer_port, "--no-open")
        assert second_launch_result.returncode == 0, second_launch_result.stderr
        second_instance = instance.read_instance_record(fixture_repository)

        assert second_instance is not None
        assert second_instance.process_id == first_instance.process_id
        assert "复用既有实例" in second_launch_result.stdout
        assert "重新起服务" not in second_launch_result.stdout
    finally:
        _stop_recorded_instance(fixture_repository)


def test_stale_registry_is_taken_over_by_a_fresh_instance(fixture_repository: Path) -> None:
    """登记项指向已退出的进程时必须判为陈旧并接管，而不是打开连不通的页面。"""
    stale_process_id = _find_defunct_process_id()
    instance.write_instance_record(
        instance.ViewInstanceRecord(
            process_id=stale_process_id,
            port=_find_free_port(),
            repository_root=fixture_repository,
            started_at_iso="2026-01-01T00:00:00+0000",
        )
    )
    try:
        launch_result = _run_launch(fixture_repository, "--no-open")
        assert launch_result.returncode == 0, launch_result.stderr
        assert "重新起服务" in launch_result.stdout

        refreshed_instance = instance.read_instance_record(fixture_repository)
        assert refreshed_instance is not None
        assert refreshed_instance.process_id != stale_process_id
        assert _wait_until_port_is_listening(refreshed_instance.port)
    finally:
        _stop_recorded_instance(fixture_repository)


def test_launch_stop_recycles_the_instance(fixture_repository: Path) -> None:
    """``--stop`` 之后端口不再被监听，登记被清理。

    同样显式给一个空闲端口（理由见 :func:`_run_launch_on_port`）：这一条断言的正是「收掉之后
    这个端口不再被监听」，而默认端口全机共用，并行跑时那句话可能说的是别的用例的实例。
    """
    viewer_port = _find_free_port()
    try:
        assert _run_launch_on_port(fixture_repository, viewer_port, "--no-open").returncode == 0
        recorded_instance = instance.read_instance_record(fixture_repository)
        assert recorded_instance is not None
        assert recorded_instance.port == viewer_port
        assert _wait_until_port_is_listening(recorded_instance.port)

        stop_result = _run_launch(fixture_repository, "--stop")

        assert stop_result.returncode == 0, stop_result.stderr
        assert instance.read_instance_record(fixture_repository) is None
        assert not _is_port_listening(recorded_instance.port)
    finally:
        _stop_recorded_instance(fixture_repository)


def test_idle_timeout_exits_and_clears_registry(fixture_repository: Path) -> None:
    """空闲到时限后服务自行退出并清理登记。"""
    port = _find_free_port()
    server_process = subprocess.Popen(
        [
            sys.executable,
            str(_SERVER_SCRIPT_PATH),
            "--repo-root",
            str(fixture_repository),
            "--port",
            str(port),
            "--idle-timeout",
            "1",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    instance.write_instance_record(
        instance.ViewInstanceRecord(
            process_id=server_process.pid,
            port=port,
            repository_root=fixture_repository,
            started_at_iso="2026-01-01T00:00:00+0000",
        )
    )
    try:
        assert _wait_until_port_is_listening(port)

        assert _wait_until_process_exits(server_process), "空闲时限到点后服务没有退出"
        assert instance.read_instance_record(fixture_repository) is None
        assert not _is_port_listening(port)
    finally:
        if server_process.poll() is None:
            server_process.kill()
        server_process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        _stop_recorded_instance(fixture_repository)


def test_idle_timeout_zero_disables_automatic_recycling(fixture_repository: Path) -> None:
    """``--idle-timeout 0`` 关闭自动回收：手上有请求之外的等待也不会被收掉。"""
    port = _find_free_port()
    server_process = subprocess.Popen(
        [
            sys.executable,
            str(_SERVER_SCRIPT_PATH),
            "--repo-root",
            str(fixture_repository),
            "--port",
            str(port),
            "--idle-timeout",
            "0",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert _wait_until_port_is_listening(port)
        time.sleep(2.5)
        assert server_process.poll() is None, "关闭自动回收后服务不应自行退出"
        assert _is_port_listening(port)
    finally:
        server_process.terminate()
        server_process.wait(timeout=_STOP_TIMEOUT_SECONDS)


def test_registry_clear_never_removes_another_instances_record(fixture_repository: Path) -> None:
    """登记清理必须确认登记仍属于自己，否则会抹掉更新实例的记录。"""
    live_process_id = 4242
    instance.write_instance_record(
        instance.ViewInstanceRecord(
            process_id=live_process_id,
            port=8791,
            repository_root=fixture_repository,
            started_at_iso="2026-01-01T00:00:00+0000",
        )
    )

    assert (
        instance.clear_instance_record(fixture_repository, expected_process_id=live_process_id + 1)
        is False
    )
    assert instance.read_instance_record(fixture_repository) is not None
    assert (
        instance.clear_instance_record(fixture_repository, expected_process_id=live_process_id)
        is True
    )
    assert instance.read_instance_record(fixture_repository) is None


def test_registry_file_is_ignored_by_the_repository() -> None:
    """登记文件必须落在本仓库被 git 忽略的 ``.env*`` 规则里，不进入版本库。"""
    ignored_result = _run_git(
        _PROJECT_ROOT_PATH, "check-ignore", "--no-index", instance.REGISTRY_FILE_NAME
    )
    assert instance.REGISTRY_FILE_NAME in ignored_result.stdout


def _expected_counts_by_path(
    terminal_numstat_text: str,
) -> dict[str, tuple[int | None, int | None]]:
    """把终端 ``git diff --numstat`` 的输出整理成 路径 -> (新增, 删除)。

    非 ``-z`` 输出里重命名写成 ``old => new``；界面与 ``--name-only`` 一样只认新路径。
    夹具造的都是同目录整文件改名，因此必然是这种简单形式——若哪天不是，调用方的文件集合
    断言会先炸，不会让这里静默取到错的键。
    """
    expected_counts_by_path: dict[str, tuple[int | None, int | None]] = {}
    for numstat_line in terminal_numstat_text.splitlines():
        added_field, deleted_field, displayed_path = numstat_line.split("\t")
        changed_path = displayed_path.split(" => ")[-1]
        expected_counts_by_path[changed_path] = (
            None if added_field == "-" else int(added_field),
            None if deleted_field == "-" else int(deleted_field),
        )
    return expected_counts_by_path


def _expected_totals(changed_files: list[dict]) -> dict[str, int]:
    """把一组改动条目汇总成文件数与增删合计，作为分区 totals 的期望值。"""
    return {
        "files": len(changed_files),
        "add": sum(entry["add"] or 0 for entry in changed_files),
        "del": sum(entry["del"] or 0 for entry in changed_files),
    }


def _added_row_texts(diff_body: dict) -> list[str]:
    """取出一份逐行 diff 里所有新增行的正文。"""
    return [row["text"] for row in diff_body["rows"] if row["kind"] == "add"]


def _strip_html_tags(rendered_line_html: str) -> str:
    """剥掉行级 HTML 的标签并还原实体，用于核对渲染结果与源码逐行一致。"""
    return html.unescape(re.sub(r"<[^>]+>", "", rendered_line_html))


def _is_port_listening(port: int) -> bool:
    """判断端口当前是否可连接。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.settimeout(0.5)
        return probe_socket.connect_ex((_LOOPBACK_HOST, port)) == 0


def _wait_until_process_exits(server_process: subprocess.Popen) -> bool:
    """等待服务进程自行退出。"""
    deadline_monotonic = time.monotonic() + _STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline_monotonic:
        if server_process.poll() is not None:
            return True
        time.sleep(_SERVER_POLL_INTERVAL_SECONDS)
    return False
