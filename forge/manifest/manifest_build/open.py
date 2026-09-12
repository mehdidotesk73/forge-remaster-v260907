from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path
import tempfile

from .env_init import init_environment
from .git_ops import clone_repo


def open_manifest_repo(repo_dir: str, open_editor: bool = True) -> Path:
    repo_path = Path(repo_dir)
    if not (repo_path / "pyproject.toml").exists():
        raise FileNotFoundError(f"No pyproject.toml found at {repo_path}")

    init_environment(str(repo_path))

    venv_python = repo_path / ".venv" / "bin" / "python"
    vscode_dir = repo_path / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    (vscode_dir / "settings.json").write_text(
        f'{{\n    "python.defaultInterpreterPath": "{venv_python}"\n}}\n'
    )

    if open_editor:
        readme_file = repo_path / "README.md"
        example_file = repo_path / "src" / "declarations" / "example.py"

        args = ["code", str(repo_path)]
        if example_file.exists():
            args.append(str(example_file))
        if readme_file.exists():
            args += ["--goto", str(readme_file)]

        subprocess.run(args, check=True)

    return repo_path


def _repo_name_from_url(git_url: str) -> str:
    # https://github.com/user/test_manifest_repo_git.git -> test_manifest_repo_git
    name = git_url.rstrip("/").split("/")[-1]
    return re.sub(r"\.git$", "", name)


def git_open_manifest_repo(
    git_url: str, branch: str = "main", open_editor: bool = True
) -> dict:
    repo_name = _repo_name_from_url(git_url)
    base_temp = Path(tempfile.gettempdir()) / "forge_git_open" / repo_name / branch

    if not base_temp.exists():
        clone_repo(git_url, str(base_temp), branch=branch)

    opened_path = open_manifest_repo(str(base_temp), open_editor=open_editor)
    return {"repo_path": str(opened_path)}
