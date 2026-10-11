"""随包 operator Skill 的安全识别、升级与文件同步。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

_REMOTE_SKILL_PROTECTED_FILENAME = "SKILL.md"
_SKILL_REFERENCES_SUBDIR = "references"
_OPERATOR_SKILL_METADATA_FILENAME = ".kedacode-managed.json"
_OPERATOR_SKILL_METADATA_FORMAT_VERSION = 1

# 写入安装元数据前没有来源标记的旧发行版，只自动刷新受管文件树与历史版本完全相同
# 的副本。这三组路径涵盖主文件单发、references 拆分后以及 lifecycle 文档加入后。
_KNOWN_OPERATOR_SKILL_SNAPSHOTS: dict[tuple[str, ...], frozenset[str]] = {
    ("SKILL.md",): frozenset(
        {
            "41017753a1c6ef4c8283b8be04e44f8a21307a1a3cb11c84673316a4119742b1",
            "e718d70715a63555a0c7e8ef586fae29937a5b5e63596cc79453e7e4e0174858",
        }
    ),
    (
        "SKILL.md",
        "references/ci.md",
        "references/create-issue.md",
        "references/daemon.md",
        "references/issue-inspect.md",
        "references/run-once.md",
        "references/setup-and-config.md",
        "references/triage.md",
        "references/watch.md",
    ): frozenset(
        {
            "0521299eeb4fd591e22e65f1c2d728f1076a905b694da1f34b275b2e6d8418f1",
            "09db469b0c8393726228aa58aa0353092ab12f8eaab9ea2ae15af8efd350b0e0",
            "0d07142a6ca64d91a1e29bf5b36bca85c0a75b3c01a8e7dd4c910072e3730c96",
            "0db0457cb92a38421c135aa7f8ae15c12cee1c242874121715a13a40f15f2bb6",
            "0e6718512961a95137405b8e5502de5732b8a52b7efb6697c35aabe578c1bd63",
            "2666123f5c20bf21cad4ff60060af537fceb669c8aef5b23e1dc2b79018ba705",
            "2fa456b81b811cad4ece29372c9027c561ca0f4d41316aa4a9e05b6e8a208d58",
            "343d2dd99479383014c1004ff3bbf2b1f52b456169fa6ec5aec157eb98b210ed",
            "38fab90eb68307ed018fdeb9e335254a99d2cf97aee82b19f1ad2fb10d095245",
            "3b9a9fd6979900f34cfeba7d8313174d629276fac8ba82807847eeba47c962fc",
            "3d8186d3b9b081e7ce2b28013447232a25a97f3188f15b2c931d75b9e15abfee",
            "462b614ad38b7cebd5f4a11d667de6c7832173c1cd7c1ad55dfc2fd8d3af054d",
            "48c39afc51b8af78de3f20eea4ba1d92b73254aa9e4b57deb738ab3e536a431d",
            "4e9a66ae252b7d3d61e3cb874698a772ac0cf69d2f5964a113b46ac735151d42",
            "53179c595a72b1bc113b1723553b743f38f6c3083ebf3c8e5d9a5e4b3069f95c",
            "58c6b1e67f8d26dc47eb92ecea4068a09961a6674e88a22b1213f6ad63bd8fec",
            "883578f659a979b88bb98bbe52865d9e44705fbe947fb92228a53bd3aa9c05f9",
            "9479506aa57ae9caefaed51fa5a4967b571270dea721f44ca678b733f8ffd194",
            "b15724e3bf52017aef74dc99ed5a2d70f5ffad089ed6505753339e28348e66a7",
            "b285b34fa1f32b7bfe5ac75024390ef367cf59c0edf8c8668fc1f63c3905ffe2",
            "bdd4b7ce777f8440bddc4a06f9ecf775e4e8627fabddcea9d6cd976bd6e7e3ab",
            "ca1b40760cd924c47ba746503a29b28a6c6300cea09c6cfb3c833ff0faa77fb5",
            "e3c0f97d8afeefc809162a1992acda408a9df976260af57fd4b7211d6ecd6685",
            "f672d5d930cb531701f543f9c7ffaf88b36ff6225fda25fe0ea43a0ea213111f",
            "fb70430ca38378a42c5d168ba374fd89f72c8120ccd348dfce21cb4dc6ce13a9",
            "fd569360cc842af34008adf458cbbe7c3b6742cc31967ec1c56d445b162a512d",
            "fe2d3b79274d69c4a2a427192ced7e159896c119d5595095cd36f2e8eee48657",
        }
    ),
    (
        "SKILL.md",
        "references/ci.md",
        "references/create-issue.md",
        "references/daemon.md",
        "references/issue-inspect.md",
        "references/lifecycle.md",
        "references/run-once.md",
        "references/setup-and-config.md",
        "references/triage.md",
        "references/watch.md",
    ): frozenset(
        {
            "1912973115c6ae43713a37d60b4f5c65fdc9064873025a0e2d0812c8e29893fb",
            "19c92e6cc7c26b2d29a484cc21c7c8a3df3ccdcef20ab4677b2002454ae23fe7",
            "1d7e21980776b60c3464446e7406176cc06caf2e5954874978d04182ea7df62d",
            "5110733a3aa7bbb8429d78bc56e55e68c64e0e5e56ba1e9d45c7b5973118d0cb",
            "6dddb2bfd982ae23362b48aa52cf897d9409ca8f5ecbb1ba5f01b88b8147fc9a",
            "7b14fbfcd44159d96aa53a89d7761f3f7f8f69f69300368544774ba38c1c7eb7",
            "83850559f72f2084ebb2a1b15cee86e535506589883d03809db72192744154a1",
            "913ddd2690e78eed6c23baa186fa0688f0ff380ce15688c55721d1935c9ef456",
            "9365c830d1ae93b6194cf33d0f7f00a7fb92395c7b7e675705e4a4c567dcdc5e",
            "b80ec13afe49112c335443bdb980c9fb7770f9f9542b87b641b3297271131868",
            "d68c7dd225a56e4f009a54e3e2b9bd2615f686ea1af7be75aa1eb9e0402e7687",
            "f669db2fa11421676bfb7af7c4b7fc691c9db37eaf63e612309c4a9a14863ced",
        }
    ),
}


class _OperatorSkillSyncError(RuntimeError):
    """随包 operator Skill 受管文件同步失败。"""


def _is_managed_operator_skill_up_to_date(*, target_path: Path, source_path: Path) -> bool:
    """目标 Skill 的全部受管文件是否与当前发行包逐字节一致。"""
    for relative_path in _managed_operator_skill_relative_paths(source_path):
        packaged_file_bytes = (source_path / relative_path).read_bytes()
        local_file_path = target_path / relative_path
        if _has_symlink_between(target_path, local_file_path) or not local_file_path.is_file():
            return False
        if local_file_path.read_bytes() != packaged_file_bytes:
            return False
    return True


def _managed_operator_skill_relative_paths(source_path: Path) -> tuple[str, ...]:
    """枚举受管主文件与 references 下的 Markdown 文件。"""
    managed_paths = [_REMOTE_SKILL_PROTECTED_FILENAME]
    references_root = source_path / _SKILL_REFERENCES_SUBDIR
    if references_root.is_dir():
        managed_paths.extend(
            reference_path.relative_to(source_path).as_posix()
            for reference_path in sorted(references_root.rglob("*.md"))
            if reference_path.is_file()
        )
    return tuple(managed_paths)


def _read_operator_skill_metadata_paths(skill_path: Path) -> tuple[str, ...] | None:
    """读取旧版本元数据中的受管文件路径。"""
    metadata = _read_operator_skill_metadata(skill_path)
    return metadata[0] if metadata is not None else None


def _recognize_unmodified_operator_skill(skill_path: Path) -> tuple[str, ...] | None:
    """识别元数据证明未修改的副本，或精确匹配历史发行树的迁移副本。"""
    metadata = _read_operator_skill_metadata(skill_path)
    if metadata is not None:
        managed_paths, expected_digest = metadata
        if (
            _managed_skill_digest(skill_path=skill_path, relative_paths=managed_paths)
            == expected_digest
        ):
            return managed_paths

    reference_root = skill_path / _SKILL_REFERENCES_SUBDIR
    if reference_root.is_symlink():
        return None
    relative_paths = [_REMOTE_SKILL_PROTECTED_FILENAME]
    if reference_root.is_dir():
        for reference_path in sorted(reference_root.rglob("*.md")):
            if _has_symlink_between(skill_path, reference_path):
                return None
            if reference_path.is_file():
                relative_paths.append(reference_path.relative_to(skill_path).as_posix())
    managed_paths = tuple(relative_paths)
    known_digests = _KNOWN_OPERATOR_SKILL_SNAPSHOTS.get(managed_paths)
    if known_digests is None:
        return None
    observed_digest = _managed_skill_digest(skill_path=skill_path, relative_paths=managed_paths)
    return managed_paths if observed_digest in known_digests else None


def _sync_packaged_operator_skill(
    *,
    target_path: Path,
    source_path: Path,
    previous_managed_paths: tuple[str, ...],
    managed_relative_paths: tuple[str, ...],
) -> None:
    """同步受管文件并删除旧发行版拥有的过期文件，保留用户自加内容。"""
    if not target_path.is_dir():
        raise _OperatorSkillSyncError(
            f"Refusing to replace non-directory operator skill path: {target_path}"
        )
    current_path_set = set(managed_relative_paths)
    obsolete_file_paths: list[Path] = []
    for obsolete_relative_path in previous_managed_paths:
        if obsolete_relative_path in current_path_set:
            continue
        obsolete_file_path = target_path.joinpath(*PurePosixPath(obsolete_relative_path).parts)
        if _has_symlink_between(target_path, obsolete_file_path):
            raise _OperatorSkillSyncError(
                f"Refusing to remove managed skill file through symlink: {obsolete_file_path}"
            )
        if obsolete_file_path.exists() and not obsolete_file_path.is_file():
            raise _OperatorSkillSyncError(
                f"Refusing to remove non-file managed skill path: {obsolete_file_path}"
            )
        obsolete_file_paths.append(obsolete_file_path)

    local_file_paths: dict[str, Path] = {}
    for relative_path in managed_relative_paths:
        local_file_path = target_path.joinpath(*PurePosixPath(relative_path).parts)
        if _has_symlink_between(target_path, local_file_path):
            raise _OperatorSkillSyncError(
                f"Refusing to write managed skill file through symlink: {local_file_path}"
            )
        if local_file_path.exists() and not local_file_path.is_file():
            raise _OperatorSkillSyncError(
                f"Refusing to replace non-file managed skill path: {local_file_path}"
            )
        local_file_paths[relative_path] = local_file_path

    for obsolete_file_path in obsolete_file_paths:
        if obsolete_file_path.is_file():
            obsolete_file_path.unlink()

    for relative_path in managed_relative_paths:
        packaged_file_path = source_path.joinpath(*PurePosixPath(relative_path).parts)
        local_file_path = local_file_paths[relative_path]
        local_file_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_file_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=local_file_path.parent, delete=False
            ) as temporary_file:
                temporary_file_path = Path(temporary_file.name)
                temporary_file.write(packaged_file_path.read_bytes())
            os.chmod(temporary_file_path, stat.S_IMODE(packaged_file_path.stat().st_mode))
            os.replace(temporary_file_path, local_file_path)
        finally:
            if temporary_file_path is not None and temporary_file_path.exists():
                temporary_file_path.unlink()


def _operator_skill_metadata_matches_current(
    *, skill_path: Path, source_path: Path, managed_relative_paths: tuple[str, ...]
) -> bool:
    """元数据是否精确记录当前随包文件集合与摘要。"""
    metadata = _read_operator_skill_metadata(skill_path)
    if metadata is None:
        return False
    packaged_digest = _managed_skill_digest(
        skill_path=source_path,
        relative_paths=managed_relative_paths,
    )
    return metadata == (managed_relative_paths, packaged_digest)


def _write_operator_skill_metadata(
    *, target_path: Path, source_path: Path, managed_relative_paths: tuple[str, ...]
) -> None:
    """原子写入当前发行包的受管路径与内容摘要。"""
    metadata_path = target_path / _OPERATOR_SKILL_METADATA_FILENAME
    if metadata_path.is_symlink():
        raise _OperatorSkillSyncError(
            f"Refusing to write operator skill metadata through symlink: {metadata_path}"
        )
    managed_digest = _managed_skill_digest(
        skill_path=source_path,
        relative_paths=managed_relative_paths,
    )
    if managed_digest is None:
        raise _OperatorSkillSyncError(
            f"Unable to compute packaged operator skill digest: {source_path}"
        )
    metadata_content = (
        json.dumps(
            {
                "format_version": _OPERATOR_SKILL_METADATA_FORMAT_VERSION,
                "managed_digest": managed_digest,
                "managed_files": list(managed_relative_paths),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    temporary_metadata_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target_path, delete=False
        ) as temporary_metadata_file:
            temporary_metadata_path = Path(temporary_metadata_file.name)
            temporary_metadata_file.write(metadata_content)
        os.replace(temporary_metadata_path, metadata_path)
    finally:
        if temporary_metadata_path is not None and temporary_metadata_path.exists():
            temporary_metadata_path.unlink()


def _managed_skill_digest(*, skill_path: Path, relative_paths: tuple[str, ...]) -> str | None:
    """计算受管文件摘要；缺文件或路径含符号链接时返回 ``None``。"""
    digest = hashlib.sha256()
    for relative_path in relative_paths:
        if not _is_safe_operator_skill_relative_path(relative_path):
            return None
        local_file_path = skill_path.joinpath(*PurePosixPath(relative_path).parts)
        if _has_symlink_between(skill_path, local_file_path) or not local_file_path.is_file():
            return None
        digest.update(relative_path.encode("utf-8") + b"\0")
        digest.update(local_file_path.read_bytes() + b"\0")
    return digest.hexdigest()


def _read_operator_skill_metadata(skill_path: Path) -> tuple[tuple[str, ...], str] | None:
    """读取并校验安装元数据的格式与受管路径。"""
    metadata_path = skill_path / _OPERATOR_SKILL_METADATA_FILENAME
    if metadata_path.is_symlink() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("format_version") != _OPERATOR_SKILL_METADATA_FORMAT_VERSION:
        return None
    managed_paths = metadata.get("managed_files")
    managed_digest = metadata.get("managed_digest")
    if (
        not isinstance(managed_paths, list)
        or not all(isinstance(relative_path, str) for relative_path in managed_paths)
        or not isinstance(managed_digest, str)
        or len(managed_digest) != 64
    ):
        return None
    relative_paths = tuple(managed_paths)
    if (
        not relative_paths
        or relative_paths[0] != _REMOTE_SKILL_PROTECTED_FILENAME
        or len(set(relative_paths)) != len(relative_paths)
        or any(not _is_safe_operator_skill_relative_path(path) for path in relative_paths)
    ):
        return None
    return relative_paths, managed_digest


def _is_safe_operator_skill_relative_path(relative_path: str) -> bool:
    """只允许受管主文件及 references 下的 Markdown 相对路径。"""
    parsed_path = PurePosixPath(relative_path)
    if parsed_path.is_absolute() or parsed_path.as_posix() != relative_path:
        return False
    if any(path_part in {"", ".", ".."} for path_part in parsed_path.parts):
        return False
    return relative_path == _REMOTE_SKILL_PROTECTED_FILENAME or (
        parsed_path.parts[0] == _SKILL_REFERENCES_SUBDIR
        and len(parsed_path.parts) >= 2
        and parsed_path.suffix == ".md"
    )


def _has_symlink_between(skill_path: Path, file_path: Path) -> bool:
    """检查受管文件从 Skill 根到文件自身的路径是否包含符号链接。"""
    try:
        relative_path = file_path.relative_to(skill_path)
    except ValueError:
        return True
    return any(
        (skill_path / Path(*relative_path.parts[:part_count])).is_symlink()
        for part_count in range(1, len(relative_path.parts) + 1)
    )
