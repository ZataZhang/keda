"""从远程模板仓库同步 KedaCode 所需的用户级 Skills。"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.shared.interfaces.agent_runner import IProcessRunner

from backend.core.shared.models import product_identity
from backend.core.shared.models.agent_spec import BUILTIN_AGENT_SPECS
from backend.core.shared.prd_skill_location import keda_owned_skills_root


REMOTE_TEMPLATE_SKILLS_REPOSITORY_URL = "https://github.com/ZataZhang/zata-codes-template.git"
"""用户级 Skill 的唯一远程内容来源。

必须写当前用户名 ``ZataZhang``。旧名 ``zata-zhangtao`` 已废弃：GitHub 的仓库级
重定向让旧 URL 目前仍能 clone（实测 301），所以这类残留极难被发现；但废弃的用户名
**可以被他人重新注册**，一旦被抢注，``kc init`` 就会从陌生人的仓库拉取内容，并把
结果当作 agent 指令安装进每个用户的 skills 目录。这是供应链路径，不是文案问题。
"""

REMOTE_TEMPLATE_SKILLS_REF = "main"
"""每次 ``kc init`` 要获取的模板分支。"""

REMOTE_TEMPLATE_SKILL_NAMES: tuple[str, ...] = ("prd", "code-reviewer")
"""KedaCode 安装且仅安装的远程模板 Skills。"""

_SKILLS_DIR_ENV_SUFFIX = "SKILLS_DIR"

_REMOTE_SKILL_PROTECTED_FILENAME = "SKILL.md"
"""用于识别用户同名 Skill 是否被改动的最小契约文件。"""

_SKILL_REFERENCES_SUBDIR = "references"
"""子命令说明所在子目录；目录内递归的 ``.md`` 与主文件同属发行包受管集合。"""

_LEGACY_ACTION_ABSENT = "absent"
_LEGACY_ACTION_REMOVE = "remove"
_LEGACY_ACTION_PRESERVE = "preserve"
"""旧名 operator Skill 副本的三种处理决策（安装根逐个判定）。"""

#: 改名前随包发行的 operator Skill 主文件在 Git 历史中出现过的全部 sha256 摘要
#: （旧目录只有一个随包文件，摘要取自该文件在各提交下的 blob，去重后 12 条）。
#: 用途：``kc init`` 装完新名 skill 后要清理各安装根遗留的旧名副本——只有副本的
#: ``SKILL.md`` 逐字节等于某个**历史随包版本**（即用户从未改动过它）时才允许自动
#: 删除；改动过的副本一律保留并回传提示，避免静默丢失用户的私有定制。
#: 重新计算：对旧 skill 路径 ``git log --all --format=%H -- <旧路径>/SKILL.md`` 的
#: 每个提交 ``git show "<提交>:<旧路径>/SKILL.md" | shasum -a 256`` 后去重。
_LEGACY_OPERATOR_SKILL_DIGESTS: frozenset[str] = frozenset(
    {
        "1b5ee146b236c664d14e385850e822ce2dbd68f0ed2008d21cbd6905e4dbe5df",
        "246b656d519b09a7a367e76e14b98ba7c380761c429288abd9bb6d057f95b78b",
        "278f9abf702720060dfa26da005792628382688aa58ca9796332f3e9cb12e30d",
        "33c9c8bf0457c36ee57a3a2a6f7d0d51724df91245f6c108bb3baa0717a83434",
        "34fb57de40d6573a26bb5ab5130f11159b71c900ee182a390f5685d24beb4304",
        "49bb0fd0116fd30e7942b6ee9c0ee55b0169a271660b5300966d351bd248b237",
        "546e2a57e42de6a13407dbd8531dd79f3f08f7fa6bd510f1d366c924f0ea0346",
        "9718dbfd462d2668b11da8488fc6d87d12d2a8441481e4f90262f1fb942d4e86",
        "9b29be9e44839998010b11257bbbe6a73d3f8c9a4c18915cc81bfcd6d13b475c",
        "b7707c2a454061c5d19b2be96638fd9f8f03cc9be51d7f39b7f97e20aa6caa0e",
        "cd572233ecc3ad8beff7f6e38ee63908c61bc7b26878ab33a2c565fb347ef2a7",
        "e5ab20ba4278e2322df4b893a9726c3cab6aa34ae01148245ba7623899a0edd7",
    }
)


class RemoteTemplateSkillInstallError(RuntimeError):
    """远程模板 Skill 拉取或同步失败时抛出。"""


@dataclass(frozen=True)
class PackagedSkillInstallResult:
    """随发行包安装的本地 Skill 计划结果。

    Attributes:
        target_path: 新名 operator Skill 在本安装根下的目标目录。
        action: 安装决策，``install`` / ``up-to-date`` / ``preserve-conflict`` /
            ``overwrite`` 之一。
        dry_run: 是否没有写入任何文件。
        legacy_skill_path: 本次检查到的旧名副本路径；该安装根没有旧副本时为 ``None``。
        legacy_action: 旧副本处理决策，见 :data:`_LEGACY_ACTION_ABSENT` 等三个常量。
        legacy_notice: 可直接展示给用户的旧副本处理说明；无需说明时为 ``None``。
    """

    target_path: Path
    action: str
    dry_run: bool
    legacy_skill_path: Path | None = None
    legacy_action: str = _LEGACY_ACTION_ABSENT
    legacy_notice: str | None = None


def install_packaged_operator_skill(
    *, target_skills_root: Path, dry_run: bool, force: bool
) -> PackagedSkillInstallResult:
    """安装随发行包发行的 operator Skill，并清理旧名下的历史随包副本。

    新名安装语义不变：同名用户 Skill 的**受管文件**（主文件 + ``references/`` 下
    全部 ``.md``）与随包不一致时默认保留，``force=True`` 才覆盖；用户自己放进
    Skill 目录的非受管文件不参与比对、不触发冲突。旧名副本按安装根逐个判定
    （``kc init`` 会为每个安装根各调用一次本函数）：目录里只有已知随包文件、且
    ``SKILL.md`` 的 sha256 命中 :data:`_LEGACY_OPERATOR_SKILL_DIGESTS`（用户从未
    改动过）时才删除；改动过的副本保留，判定结果与提示路径通过返回值的
    ``legacy_*`` 字段交给调用方展示。

    Args:
        target_skills_root: 本次安装写入的 skills 根目录。
        dry_run: 是否只返回计划、不触碰文件系统。
        force: 是否覆盖改动过的同名 Skill，并直接删除旧名副本。

    Returns:
        PackagedSkillInstallResult: 新名安装决策与旧名副本处理决策。

    Raises:
        RemoteTemplateSkillInstallError: 随包目录缺少 ``SKILL.md``，或新名目标
            是指向别处的符号链接。
    """
    skill_name = product_identity.OPERATOR_SKILL_NAME
    source_path = Path(__file__).with_name("templates") / "skills" / skill_name
    source_contract_path = source_path / _REMOTE_SKILL_PROTECTED_FILENAME
    if not source_contract_path.is_file():
        raise RemoteTemplateSkillInstallError(f"Packaged skill is missing {source_contract_path}")
    target_path = target_skills_root / skill_name
    if target_path.is_symlink():
        raise RemoteTemplateSkillInstallError(
            f"Refusing to write skill through symlink: {target_path}"
        )
    action = "install"
    if target_path.exists():
        if _is_managed_skill_up_to_date(
            target_path=target_path,
            source_path=source_path,
        ):
            action = "up-to-date"
        elif not force:
            action = "preserve-conflict"
        else:
            action = "overwrite"
    if not dry_run and action in {"install", "overwrite"}:
        target_skills_root.mkdir(parents=True, exist_ok=True)
        if action == "overwrite":
            shutil.rmtree(target_path)
        shutil.copytree(source_path, target_path)

    legacy_skill_path = target_skills_root / product_identity.LEGACY_OPERATOR_SKILL_NAME
    legacy_action, legacy_notice = _resolve_legacy_operator_skill(
        legacy_skill_path=legacy_skill_path,
        packaged_relative_paths=_packaged_relative_file_paths(source_path),
        dry_run=dry_run,
        force=force,
    )
    return PackagedSkillInstallResult(
        target_path=target_path,
        action=action,
        dry_run=dry_run,
        legacy_skill_path=legacy_skill_path if legacy_action != _LEGACY_ACTION_ABSENT else None,
        legacy_action=legacy_action,
        legacy_notice=legacy_notice,
    )


def _managed_packaged_skill_relative_paths(source_path: Path) -> tuple[str, ...]:
    """枚举发行包**受管**的 Skill 文件（相对路径，POSIX 分隔，主文件在前）。

    受管集合 = 主文件 ``SKILL.md`` + ``references/`` 子目录下递归的全部 ``.md``。
    目标目录里不属于该集合的文件（用户自加的笔记等）既不参与同步判定，也不触发
    冲突，与 :func:`_packaged_relative_file_paths`（旧名副本清理用的全量枚举）
    是两种口径。
    """
    managed_relative_paths = [_REMOTE_SKILL_PROTECTED_FILENAME]
    references_root = source_path / _SKILL_REFERENCES_SUBDIR
    if references_root.is_dir():
        managed_relative_paths.extend(
            reference_file_path.relative_to(source_path).as_posix()
            for reference_file_path in sorted(references_root.rglob("*.md"))
            if reference_file_path.is_file()
        )
    return tuple(managed_relative_paths)


def _is_managed_skill_up_to_date(*, target_path: Path, source_path: Path) -> bool:
    """目标 Skill 目录的受管文件是否逐一与发行包逐字节一致（任一缺失或不同即否）。"""
    for managed_relative_path in _managed_packaged_skill_relative_paths(source_path):
        packaged_file_bytes = (source_path / managed_relative_path).read_bytes()
        local_file_path = target_path / managed_relative_path
        if not local_file_path.is_file():
            return False
        if local_file_path.read_bytes() != packaged_file_bytes:
            return False
    return True


def _packaged_relative_file_paths(source_path: Path) -> frozenset[str]:
    """枚举随包 skill 目录里的全部受管文件（相对路径，POSIX 分隔）。"""
    return frozenset(
        packaged_file_path.relative_to(source_path).as_posix()
        for packaged_file_path in source_path.rglob("*")
        if packaged_file_path.is_file()
    )


def _is_unmodified_legacy_packaged_copy(
    legacy_skill_path: Path, packaged_relative_paths: frozenset[str]
) -> bool:
    """旧名副本是否为「只有已知随包文件、且主文件等于某个历史随包版本」的原样副本。

    任一条件不满足都判为不可自动删除：路径不是目录、含符号链接、含未知文件、缺少
    ``SKILL.md``，或主文件摘要不在历史摘要集里。
    """
    if legacy_skill_path.is_symlink() or not legacy_skill_path.is_dir():
        return False
    if any(legacy_entry_path.is_symlink() for legacy_entry_path in legacy_skill_path.rglob("*")):
        return False
    observed_relative_paths = frozenset(
        legacy_file_path.relative_to(legacy_skill_path).as_posix()
        for legacy_file_path in legacy_skill_path.rglob("*")
        if legacy_file_path.is_file()
    )
    if not observed_relative_paths <= packaged_relative_paths:
        return False
    legacy_contract_bytes = _read_remote_skill_contract(legacy_skill_path)
    if legacy_contract_bytes is None:
        return False
    return hashlib.sha256(legacy_contract_bytes).hexdigest() in _LEGACY_OPERATOR_SKILL_DIGESTS


def _resolve_legacy_operator_skill(
    *,
    legacy_skill_path: Path,
    packaged_relative_paths: frozenset[str],
    dry_run: bool,
    force: bool,
) -> tuple[str, str | None]:
    """判定并执行旧名 operator Skill 副本的清理，返回 ``(legacy_action, notice)``。

    ``force=True`` 时直接删除（符号链接只解除链接本身）；否则只删除逐字节等于
    历史随包版本的原样副本，其余一律保留并给出可展示的路径提示。
    """
    if not legacy_skill_path.exists():
        return _LEGACY_ACTION_ABSENT, None
    removable = force or _is_unmodified_legacy_packaged_copy(
        legacy_skill_path, packaged_relative_paths
    )
    if not removable:
        keep_prefix = "Would keep" if dry_run else "Kept"
        return _LEGACY_ACTION_PRESERVE, (
            f"{keep_prefix} the modified legacy operator skill copy at "
            f"{legacy_skill_path}; it is not the packaged content, so it was left "
            "untouched. Remove it manually or pass --force to delete it."
        )
    if not dry_run:
        _delete_legacy_skill_path(legacy_skill_path)
    remove_prefix = "Would remove" if dry_run else "Removed"
    return _LEGACY_ACTION_REMOVE, (
        f"{remove_prefix} the legacy operator skill copy at {legacy_skill_path}; "
        "the packaged skill is now installed under its current name."
    )


def _delete_legacy_skill_path(legacy_skill_path: Path) -> None:
    """删除旧名副本：符号链接只解除链接本身，普通文件直接删，目录整体删。"""
    if legacy_skill_path.is_symlink() or legacy_skill_path.is_file():
        legacy_skill_path.unlink()
    else:
        shutil.rmtree(legacy_skill_path)


@dataclass(frozen=True)
class RemoteTemplateSkillInstallOptions:
    """控制远程模板 Skill 同步的选项。

    Attributes:
        process_runner: 执行受限 Git 命令的端口。
        dry_run: 是否只返回用户级目标路径而不访问网络或写入文件。
        user_home_path: 测试或受控运行时指定的用户主目录；为空时使用实际主目录。
        force: 是否允许覆盖用户修改过的同名 Skill。详见
            :func:`install_remote_template_skills`。
    """

    process_runner: IProcessRunner
    dry_run: bool = False
    user_home_path: Path | None = None
    force: bool = False


@dataclass(frozen=True)
class RemoteTemplateSkillInstallResult:
    """远程模板 Skill 同步结果。

    Attributes:
        target_skills_roots: 写入 ``prd`` 与 ``code-reviewer`` 的根目录列表——
            首位是产品自有目录 ``<本机状态目录>/skills``，其后每个检测到的 agent 各一个。
        installed_skill_names: 实际或计划写入的 Skill 白名单名称。
        skipped_skill_names: 与远程模板一致因而无需再次覆盖的 Skill 名称,
            可避免重复写入误触更新 ``mtime``；按目录聚合去重。
        overwritten_skill_names: 本次确实替换为远程模板副本的 Skill 名称,
            含 ``force=True`` 下被覆盖的用户自有副本；按目录聚合去重。
        dry_run: 是否没有执行任何网络或文件系统写入。
    """

    target_skills_roots: tuple[Path, ...]
    installed_skill_names: tuple[str, ...]
    skipped_skill_names: tuple[str, ...] = ()
    overwritten_skill_names: tuple[str, ...] = ()
    dry_run: bool = False


def resolve_user_skill_install_roots(user_home_path: Path | None = None) -> tuple[Path, ...]:
    """解析 ``kc init`` 的 Skill 安装目标（可多于一个）。

    优先尊重 ``SKILLS_DIR`` 产品环境变量（显式覆盖，单目录；CI / 测试用它把安装落到
    临时目录，不再探测任何用户目录）。否则**首位是产品自有目录**
    ``<本机状态目录>/skills``，其后才依次是各 agent 的用户级 skills 目录。

    自有目录排在前面是有意的：PRD 解析是 runner 自身的能力，用户删掉 agent 目录里
    的 prd skill 不应让 runner 失去解析能力（见
    :func:`backend.core.shared.prd_skill_location.resolve_prd_skill_path` 的优先序）。

    Agent 目录按注册表（:data:`BUILTIN_AGENT_SPECS` 的 ``auth_home``）派生，并只
    保留其中**已存在配置目录**的 agent——安装器不为未安装的 agent 凭空创建配置
    目录。一个 agent 目录都没探测到时，回退到注册表首个声明了 ``auth_home`` 的
    agent，避免初始化流程依赖交互式选择。

    Args:
        user_home_path: 可选的用户主目录覆盖，主要供测试使用。

    Returns:
        安装目标根目录元组（产品自有目录在前；注册顺序、去重）。函数本身不创建目录。
    """
    configured_skills_root = product_identity.read_product_env_value(_SKILLS_DIR_ENV_SUFFIX)
    if configured_skills_root:
        return (Path(configured_skills_root).expanduser(),)

    effective_home_path = Path.home() if user_home_path is None else user_home_path
    detected_agent_roots: list[Path] = []
    fallback_skills_root: Path | None = None
    for agent_spec in BUILTIN_AGENT_SPECS.values():
        agent_skills_root = agent_spec.user_skills_dir(effective_home_path)
        if agent_skills_root is None:
            continue
        if agent_skills_root.parent.is_dir():
            if agent_skills_root not in detected_agent_roots:
                detected_agent_roots.append(agent_skills_root)
        elif fallback_skills_root is None:
            fallback_skills_root = agent_skills_root

    candidate_roots: list[Path] = [keda_owned_skills_root(effective_home_path)]
    if detected_agent_roots:
        candidate_roots.extend(detected_agent_roots)
    elif fallback_skills_root is not None:
        candidate_roots.append(fallback_skills_root)
    return tuple(candidate_roots)


def install_remote_template_skills(
    options: RemoteTemplateSkillInstallOptions,
) -> RemoteTemplateSkillInstallResult:
    """从远程模板仓库安装 KedaCode 需要的两个用户级 Skill。

    仅通过 sparse checkout 下载 ``skills/prd`` 与 ``skills/code-reviewer``，
    不执行远程仓库脚本，也不会读取或写入目标项目的 Skill 目录。远程只
    下载一次，随后写入 :func:`resolve_user_skill_install_roots` 返回的全部目录
    —— 产品自有目录 ``<本机状态目录>/skills`` 一份（runner 解析 PRD 时优先取它），
    每个检测到的 agent 再各一份。

    Args:
        options: Git 执行端口、dry-run 标记和可选用户主目录。

    Returns:
        实际或计划写入的用户级目录列表与 Skill 名称。

    Raises:
        RemoteTemplateSkillInstallError: 远程仓库缺少所需目录，目录包含符号链接，
            或任一目标存在 ``SKILL.md`` 与远程不同的同名 Skill 且 ``force`` 为
            False。
    """
    target_skills_roots = resolve_user_skill_install_roots(options.user_home_path)
    if options.dry_run:
        return RemoteTemplateSkillInstallResult(
            target_skills_roots=target_skills_roots,
            installed_skill_names=REMOTE_TEMPLATE_SKILL_NAMES,
            dry_run=True,
        )

    with tempfile.TemporaryDirectory(prefix="kedacode-template-skills-") as temporary_directory:
        temporary_root_path = Path(temporary_directory)
        checkout_path = temporary_root_path / "template"
        options.process_runner.run(
            (
                "git",
                "clone",
                "--depth=1",
                "--branch",
                REMOTE_TEMPLATE_SKILLS_REF,
                "--filter=blob:none",
                "--sparse",
                REMOTE_TEMPLATE_SKILLS_REPOSITORY_URL,
                str(checkout_path),
            ),
            cwd=temporary_root_path,
            timeout=120,
            label="KedaCode remote template skill download",
        )
        options.process_runner.run(
            (
                "git",
                "-C",
                str(checkout_path),
                "sparse-checkout",
                "set",
                *(f"skills/{skill_name}" for skill_name in REMOTE_TEMPLATE_SKILL_NAMES),
            ),
            cwd=checkout_path,
            timeout=60,
            label="KedaCode remote template skill checkout",
        )
        source_skill_paths = tuple(
            checkout_path / "skills" / skill_name for skill_name in REMOTE_TEMPLATE_SKILL_NAMES
        )
        for skill_name, source_skill_path in zip(
            REMOTE_TEMPLATE_SKILL_NAMES, source_skill_paths, strict=True
        ):
            _validate_remote_skill_directory(source_skill_path, skill_name)
        overwritten_skill_names: set[str] = set()
        for target_skills_root in target_skills_roots:
            root_overwritten_names, _root_skipped_names = _write_remote_skills_into_user_root(
                source_skill_paths=source_skill_paths,
                target_skills_root=target_skills_root,
                force=options.force,
            )
            overwritten_skill_names.update(root_overwritten_names)

    # 白名单顺序稳定输出；任一目录发生覆盖的 Skill 记为已覆盖，
    # 其余（含部分目录跳过的）按未覆盖处理。
    ordered_overwritten_names = tuple(
        skill_name
        for skill_name in REMOTE_TEMPLATE_SKILL_NAMES
        if skill_name in overwritten_skill_names
    )
    ordered_skipped_names = tuple(
        skill_name
        for skill_name in REMOTE_TEMPLATE_SKILL_NAMES
        if skill_name not in overwritten_skill_names
    )

    return RemoteTemplateSkillInstallResult(
        target_skills_roots=target_skills_roots,
        installed_skill_names=REMOTE_TEMPLATE_SKILL_NAMES,
        skipped_skill_names=ordered_skipped_names,
        overwritten_skill_names=ordered_overwritten_names,
        dry_run=False,
    )


def _validate_remote_skill_directory(source_skill_path: Path, skill_name: str) -> None:
    if source_skill_path.is_symlink():
        raise RemoteTemplateSkillInstallError(
            f"Remote template skill root must not be a symlink: {source_skill_path}"
        )
    if not (source_skill_path / _REMOTE_SKILL_PROTECTED_FILENAME).is_file():
        raise RemoteTemplateSkillInstallError(
            f"Remote template is missing skills/{skill_name}/{_REMOTE_SKILL_PROTECTED_FILENAME}"
        )
    if any(remote_skill_path.is_symlink() for remote_skill_path in source_skill_path.rglob("*")):
        raise RemoteTemplateSkillInstallError(
            f"Remote template skill contains unsupported symlink: {source_skill_path}"
        )


def _read_remote_skill_contract(target_skill_path: Path) -> bytes | None:
    """读取现有 Skill 的 ``SKILL.md`` 字节;不存在或为目录时返回 ``None``。"""
    contract_path = target_skill_path / _REMOTE_SKILL_PROTECTED_FILENAME
    if not contract_path.is_file():
        return None
    return contract_path.read_bytes()


def _write_remote_skills_into_user_root(
    *,
    source_skill_paths: tuple[Path, ...],
    target_skills_root: Path,
    force: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """将远程 Skill 同步到用户级目录,处理用户同名 Skill 的保护。

    当目标 Skill 的 ``SKILL.md`` 与远程逐字节一致时视为同一份,跳过覆盖
    以避免无意义地刷新 ``mtime``;只有内容被用户改动过且未传 ``force``
    时才抛错拒绝,以防静默丢失用户私有定制。其余附属文件 (``scripts/`` 等)
    与本地 ``.git/`` 生成的索引副产物不参与比对,允许共存。

    Returns:
        ``(overwritten_skill_names, skipped_skill_names)``。

    Raises:
        RemoteTemplateSkillInstallError: 目标已是 symlink,或目标 ``SKILL.md``
            与远程不同但当前调用未声明 ``force``。
    """
    overwritten_skill_names: list[str] = []
    skipped_skill_names: list[str] = []
    for skill_name, source_skill_path in zip(
        REMOTE_TEMPLATE_SKILL_NAMES, source_skill_paths, strict=True
    ):
        target_skill_path = target_skills_root / skill_name
        if target_skill_path.is_symlink():
            raise RemoteTemplateSkillInstallError(
                f"Refusing to write remote skill through symlink: {target_skill_path}"
            )
        if target_skill_path.exists():
            remote_contract_bytes = (
                source_skill_path / _REMOTE_SKILL_PROTECTED_FILENAME
            ).read_bytes()
            local_contract_bytes = _read_remote_skill_contract(target_skill_path)
            if local_contract_bytes == remote_contract_bytes:
                skipped_skill_names.append(skill_name)
                continue
            if not force:
                raise RemoteTemplateSkillInstallError(
                    f"Refusing to overwrite user-owned skill '{skill_name}' at "
                    f"{target_skill_path}; its {_REMOTE_SKILL_PROTECTED_FILENAME} "
                    f"differs from the remote template. Pass force=True to replace "
                    f"it, or back up the existing directory manually."
                )
            shutil.rmtree(target_skill_path)
        target_skills_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_skill_path, target_skill_path, dirs_exist_ok=True)
        overwritten_skill_names.append(skill_name)
    return tuple(overwritten_skill_names), tuple(skipped_skill_names)
