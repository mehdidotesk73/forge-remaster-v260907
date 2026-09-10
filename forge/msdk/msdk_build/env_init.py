# forge/msdk/msdk_build/env_init.py
from __future__ import annotations
import subprocess
from pathlib import Path


def init_environment(repo_dir: str) -> None:
    """
    Sets up an isolated environment for a spun-up manifest repo:
    creates a venv, compiles pyproject.toml's dependencies into
    requirements-lock.txt, then installs from that lock file.
    Requires `uv` to be installed and on PATH.
    """
    repo_path = Path(repo_dir)

    subprocess.run(["uv", "venv"], cwd=repo_path, check=True)
    subprocess.run(
        ["uv", "pip", "compile", "pyproject.toml", "-o", "requirements-lock.txt"],
        cwd=repo_path,
        check=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "-r", "requirements-lock.txt"],
        cwd=repo_path,
        check=True,
    )
