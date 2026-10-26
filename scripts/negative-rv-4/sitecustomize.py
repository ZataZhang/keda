from pathlib import Path
from backend.core.shared.models import repository_paths
repository_paths.resolve_managed_worktree_root = (
    lambda repository_root_path: Path(repository_root_path) / '.kc-worktrees'
)
