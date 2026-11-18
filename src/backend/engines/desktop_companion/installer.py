"""下载并安装与当前 CLI 版本匹配的桌面 App（macOS 菜单栏 / Windows 系统托盘）。"""

from __future__ import annotations

import hashlib
import os
import plistlib
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_RELEASE_REPOSITORY = "ZataZhang/keda"
_APP_BUNDLE_IDENTIFIER = "com.kedacode.desktop"
_APP_NAME = "KedaCode.app"
_WINDOWS_EXECUTABLE_NAME = "KedaCode.exe"
_WINDOWS_APPLICATION_DIRECTORY_NAME = "KedaCode"
_HTTP_TIMEOUT_SECONDS = 60
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class DesktopCompanionInstallError(RuntimeError):
    """桌面 App 下载、校验或安装失败。"""


def is_desktop_companion_installed() -> bool:
    """检查本机是否已安装 KedaCode 桌面 App。

    macOS 检查用户或系统 Applications 目录里的 bundle identifier；Windows 检查
    ``kc desktop install`` 的安装目录里是否存在可执行文件。

    Returns:
        已安装时返回 ``True``。
    """
    try:
        if sys.platform == "win32":
            return any(
                executable_path.is_file() for executable_path in _windows_install_candidates()
            )
        if sys.platform != "darwin":
            return False
        application_paths = (
            Path.home() / "Applications" / _APP_NAME,
            Path("/Applications") / _APP_NAME,
        )
        return any(
            _app_bundle_identifier(application_path) == _APP_BUNDLE_IDENTIFIER
            for application_path in application_paths
        )
    except (OSError, RuntimeError):
        return False


def install_desktop_companion(*, version: str) -> Path:
    """下载并安装指定 CLI 版本对应的桌面 App。

    Args:
        version: 当前已安装的 kedacode 发行版本。

    Returns:
        安装后的可执行路径；macOS 返回 ``KedaCode.app`` bundle，Windows 返回
        ``KedaCode.exe``。

    Raises:
        DesktopCompanionInstallError: 平台不支持、发布资产缺失或安装失败。
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise DesktopCompanionInstallError(
            f"Cannot find a desktop app release for kedacode version {version!r}."
        )
    if sys.platform == "darwin":
        return _install_macos_app(version=version)
    if sys.platform == "win32":
        return _install_windows_app(version=version)
    raise DesktopCompanionInstallError(
        "The KedaCode desktop app is available on macOS and Windows only."
    )


def _install_macos_app(*, version: str) -> Path:
    """下载 DMG、校验并安装到用户级 Applications 目录。"""
    architecture_name = _release_architecture()
    asset_name = f"kedacode-desktop-macos-{architecture_name}.dmg"
    release_tag = f"v{version}"

    try:
        home_path = Path.home()
        application_directory = home_path / "Applications"
    except (OSError, RuntimeError) as error:
        raise DesktopCompanionInstallError(
            f"Cannot locate the user Applications directory: {error}"
        ) from error

    try:
        application_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="kedacode-desktop-") as temporary_directory:
            temporary_path = Path(temporary_directory)
            disk_image_path = temporary_path / asset_name
            _download_and_verify_release_asset(
                release_tag=release_tag,
                asset_name=asset_name,
                destination_path=disk_image_path,
            )
            return _install_disk_image(
                disk_image_path=disk_image_path,
                application_directory=application_directory,
                expected_version=version,
            )
    except DesktopCompanionInstallError:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        plistlib.InvalidFileException,
        ValueError,
    ) as error:
        raise DesktopCompanionInstallError(
            f"Could not install the KedaCode desktop app: {error}"
        ) from error


def _install_windows_app(*, version: str) -> Path:
    r"""下载 NSIS 安装包并静默安装到当前用户目录。

    不传 ``/D``：Tauri 的 per-user 安装默认落在 ``%LOCALAPPDATA%\KedaCode``，
    与用户双击安装包时走同一条路径；NSIS 的 ``/D`` 在 Python 参数转义下带空格的
    用户目录（``C:\Users\John Doe``）会被加引号，而 NSIS 明确要求 ``/D`` 不带引号。
    安装位置还会被 NSIS 记在注册表里，升级时沿用既有目录。
    """
    if _windows_desktop_app_running():
        raise DesktopCompanionInstallError(
            "KedaCode is running. Quit it from the tray menu, then retry `kc desktop install`."
        )

    asset_name = f"kedacode-desktop-windows-{_windows_release_architecture()}-setup.exe"
    release_tag = f"v{version}"
    install_directory = _windows_install_directory()

    try:
        with tempfile.TemporaryDirectory(prefix="kedacode-desktop-") as temporary_directory:
            installer_path = Path(temporary_directory) / asset_name
            _download_and_verify_release_asset(
                release_tag=release_tag,
                asset_name=asset_name,
                destination_path=installer_path,
            )
            _run_windows_installer(installer_path)
    except DesktopCompanionInstallError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise DesktopCompanionInstallError(
            f"Could not install the KedaCode desktop app: {error}"
        ) from error

    installed_executable = next(
        (candidate for candidate in _windows_install_candidates() if candidate.is_file()),
        None,
    )
    if installed_executable is None:
        raise DesktopCompanionInstallError(
            "The Windows installer finished but KedaCode.exe was not found under "
            f"{install_directory}."
        )
    return installed_executable


def _windows_local_app_data() -> Path:
    """当前用户的 ``%LOCALAPPDATA%``，缺失时回落到 ``~/AppData/Local``。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    return Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"


def _windows_install_directory() -> Path:
    r"""Tauri per-user 安装的默认目录（``%LOCALAPPDATA%\KedaCode``）。"""
    return _windows_local_app_data() / _WINDOWS_APPLICATION_DIRECTORY_NAME


def _windows_install_candidates() -> tuple[Path, ...]:
    """已知的 Windows 安装位置，首个元素是 Tauri 的 per-user 默认目录。"""
    local_app_data = _windows_local_app_data()
    return (
        local_app_data / _WINDOWS_APPLICATION_DIRECTORY_NAME / _WINDOWS_EXECUTABLE_NAME,
        local_app_data
        / "Programs"
        / _WINDOWS_APPLICATION_DIRECTORY_NAME
        / _WINDOWS_EXECUTABLE_NAME,
    )


def _windows_desktop_app_running() -> bool:
    """桌面 App 是否正在运行。

    NSIS 升级遇到运行中的 App 会弹窗要求先退出；静默安装下这个弹窗会把
    ``kc desktop install`` 卡住，因此提前检查并给出明确错误。
    """
    try:
        completed_process = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_WINDOWS_EXECUTABLE_NAME}", "/NH"],
            check=False,
            capture_output=True,
        )
    except OSError:
        return False
    return _WINDOWS_EXECUTABLE_NAME.lower().encode() in completed_process.stdout.lower()


def _run_windows_installer(installer_path: Path) -> None:
    """以静默模式运行 NSIS 安装包（``/S``），安装位置交给 Tauri 的默认策略。"""
    completed_process = subprocess.run(
        [str(installer_path), "/S"],
        check=False,
        capture_output=True,
    )
    if completed_process.returncode != 0:
        error_text = completed_process.stderr.decode("utf-8", errors="replace").strip()
        error_detail = f": {error_text}" if error_text else "."
        raise DesktopCompanionInstallError(
            "The Windows installer failed "
            f"(exit code {completed_process.returncode}){error_detail}"
        )


def _windows_release_architecture() -> str:
    """Windows 发布资产只有 x64：ARM64 机器由系统仿真运行。"""
    return "x64"


def _release_architecture() -> str:
    """把本机 CPU 架构映射到 Release 资产名。"""
    machine_name = platform.machine().lower()
    if machine_name in {"arm64", "aarch64"}:
        return "arm64"
    if machine_name in {"x86_64", "amd64"}:
        return "x64"
    raise DesktopCompanionInstallError(
        f"The KedaCode macOS app does not have a build for {machine_name!r}."
    )


def _release_asset_url(*, release_tag: str, asset_name: str) -> str:
    """生成固定仓库与 tag 的公开 Release 资产 URL。"""
    safe_tag = urllib.parse.quote(release_tag, safe="")
    safe_asset_name = urllib.parse.quote(asset_name, safe="")
    return (
        f"https://github.com/{_RELEASE_REPOSITORY}/releases/download/"
        f"{safe_tag}/{safe_asset_name}"
    )


def _download_release_bytes(*, release_tag: str, asset_name: str) -> bytes:
    """读取体积较小的 Release 元数据资产。"""
    request = urllib.request.Request(
        _release_asset_url(release_tag=release_tag, asset_name=asset_name),
        headers={"User-Agent": "kedacode-desktop-installer"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise DesktopCompanionInstallError(
                f"Release {release_tag} does not contain {asset_name}. "
                "Upgrade kedacode to a version with a published desktop app."
            ) from error
        raise DesktopCompanionInstallError(
            f"GitHub returned HTTP {error.code} while downloading {asset_name}."
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DesktopCompanionInstallError(
            f"Could not download {asset_name} from GitHub: {error}"
        ) from error


def _download_and_verify_release_asset(
    *,
    release_tag: str,
    asset_name: str,
    destination_path: Path,
) -> None:
    """用同一 Release 的 SHA256SUMS 校验磁盘镜像。"""
    checksum_text = _download_release_bytes(
        release_tag=release_tag,
        asset_name="SHA256SUMS",
    ).decode("utf-8")
    expected_digest = _expected_asset_digest(checksum_text, asset_name)
    request = urllib.request.Request(
        _release_asset_url(release_tag=release_tag, asset_name=asset_name),
        headers={"User-Agent": "kedacode-desktop-installer"},
    )
    actual_digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            with destination_path.open("wb") as destination_file:
                while downloaded_chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
                    destination_file.write(downloaded_chunk)
                    actual_digest.update(downloaded_chunk)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise DesktopCompanionInstallError(
                f"Release {release_tag} does not contain {asset_name}. "
                "Upgrade kedacode to a version with a published desktop app."
            ) from error
        raise DesktopCompanionInstallError(
            f"GitHub returned HTTP {error.code} while downloading {asset_name}."
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DesktopCompanionInstallError(
            f"Could not download {asset_name} from GitHub: {error}"
        ) from error

    if actual_digest.hexdigest() != expected_digest:
        destination_path.unlink(missing_ok=True)
        raise DesktopCompanionInstallError(
            f"Checksum verification failed for {asset_name}; the app was not installed."
        )


def _expected_asset_digest(checksum_text: str, asset_name: str) -> str:
    """从校验和清单中精确读取目标文件的 SHA-256。"""
    for checksum_line in checksum_text.splitlines():
        checksum_fields = checksum_line.split(maxsplit=1)
        if len(checksum_fields) != 2:
            continue
        checksum_value, listed_asset_name = checksum_fields
        if listed_asset_name.lstrip("*") == asset_name and re.fullmatch(
            r"[0-9a-fA-F]{64}", checksum_value
        ):
            return checksum_value.lower()
    raise DesktopCompanionInstallError(
        f"SHA256SUMS does not contain a valid checksum for {asset_name}."
    )


def _install_disk_image(
    *,
    disk_image_path: Path,
    application_directory: Path,
    expected_version: str,
) -> Path:
    """挂载 DMG、校验 bundle 身份并安全替换用户级 App。"""
    # hdiutil 回报的 mount-point 来自挂载表，是解析软链之后的真实路径；macOS 默认把
    # TMPDIR 放在 /var/folders 下，而 /var 是指向 /private/var 的软链，直接用请求的
    # 路径字符串比对会永远不相等。先解析成真实路径，挂载参数与比对统一使用它。
    mount_path = (disk_image_path.parent / "mounted").resolve()
    mount_path.mkdir()
    mount_result = subprocess.run(
        [
            "hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            str(mount_path),
            "-plist",
            str(disk_image_path),
        ],
        check=False,
        capture_output=True,
    )
    if mount_result.returncode != 0:
        error_text = mount_result.stderr.decode("utf-8", errors="replace").strip()
        raise DesktopCompanionInstallError(
            f"Could not mount the downloaded disk image: {error_text or 'hdiutil failed'}."
        )

    mounted_device_path: str | None = None
    try:
        mount_metadata = plistlib.loads(mount_result.stdout)
        if not isinstance(mount_metadata, dict):
            raise DesktopCompanionInstallError("The mounted disk image metadata is invalid.")
        mount_entities = mount_metadata.get("system-entities", [])
        if not isinstance(mount_entities, list):
            raise DesktopCompanionInstallError("The mounted disk image metadata is invalid.")
        mounted_entity = next(
            (
                mount_entity
                for mount_entity in mount_entities
                if isinstance(mount_entity, dict)
                and mount_entity.get("mount-point") == str(mount_path)
            ),
            None,
        )
        if mounted_entity is None:
            raise DesktopCompanionInstallError("The downloaded disk image has no mounted volume.")
        mounted_device_path = mounted_entity.get("dev-entry")

        app_bundle_paths = sorted(mount_path.glob("*.app"))
        if len(app_bundle_paths) != 1:
            raise DesktopCompanionInstallError(
                "The downloaded disk image must contain exactly one macOS application."
            )
        app_bundle_path = app_bundle_paths[0]
        app_metadata = _read_app_metadata(app_bundle_path)
        if app_metadata.get("CFBundleIdentifier") != _APP_BUNDLE_IDENTIFIER:
            raise DesktopCompanionInstallError(
                "The downloaded application does not have the KedaCode bundle identifier."
            )
        if app_metadata.get("CFBundleShortVersionString") != expected_version:
            raise DesktopCompanionInstallError(
                "The downloaded application version does not match the installed CLI."
            )
        return _copy_app_bundle(
            source_path=app_bundle_path,
            application_directory=application_directory,
        )
    except plistlib.InvalidFileException as error:
        raise DesktopCompanionInstallError(
            f"The downloaded disk image metadata is invalid: {error}"
        ) from error
    finally:
        detach_target = mounted_device_path or str(mount_path)
        subprocess.run(
            ["hdiutil", "detach", "-quiet", detach_target],
            check=False,
            capture_output=True,
        )


def _read_app_metadata(app_bundle_path: Path) -> dict[str, object]:
    """读取 macOS App 的 Info.plist。"""
    info_plist_path = app_bundle_path / "Contents" / "Info.plist"
    with info_plist_path.open("rb") as info_plist_file:
        app_metadata = plistlib.load(info_plist_file)
    if not isinstance(app_metadata, dict):
        raise DesktopCompanionInstallError("The downloaded application metadata is invalid.")
    return app_metadata


def _app_bundle_identifier(app_bundle_path: Path) -> object | None:
    """读取已安装 App 的 bundle identifier；不存在或不可读时返回 ``None``。"""
    try:
        return _read_app_metadata(app_bundle_path).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException, DesktopCompanionInstallError):
        return None


def _copy_app_bundle(*, source_path: Path, application_directory: Path) -> Path:
    """先完整复制到临时目录，再原子替换用户现有 App。"""
    application_path = application_directory / _APP_NAME
    if (
        application_path.exists()
        and _app_bundle_identifier(application_path) != _APP_BUNDLE_IDENTIFIER
    ):
        raise DesktopCompanionInstallError(
            f"Another application already exists at {application_path}; it was left unchanged."
        )

    with tempfile.TemporaryDirectory(
        prefix=".kedacode-app-", dir=application_directory
    ) as staging_directory:
        staging_path = Path(staging_directory)
        staged_app_path = staging_path / _APP_NAME
        subprocess.run(
            ["ditto", str(source_path), str(staged_app_path)],
            check=True,
            capture_output=True,
        )
        if _app_bundle_identifier(staged_app_path) != _APP_BUNDLE_IDENTIFIER:
            raise DesktopCompanionInstallError("The copied KedaCode application failed validation.")

        previous_app_path = staging_path / "previous.app"
        if application_path.exists():
            application_path.rename(previous_app_path)
        try:
            staged_app_path.rename(application_path)
        except OSError:
            if previous_app_path.exists() and not application_path.exists():
                previous_app_path.rename(application_path)
            raise
    return application_path


__all__ = [
    "DesktopCompanionInstallError",
    "install_desktop_companion",
    "is_desktop_companion_installed",
]
