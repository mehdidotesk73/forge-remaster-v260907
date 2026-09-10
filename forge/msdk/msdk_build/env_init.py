# msdk_build/env_init.py
import subprocess
from pathlib import Path


def init_environment(repo_dir: str) -> None:
    repo_path = Path(repo_dir)
    venv_python = repo_path / ".venv" / "bin" / "python"

    subprocess.run(["uv", "venv"], cwd=repo_path, check=True)

    subprocess.run(
        [
            "uv",
            "pip",
            "compile",
            "pyproject.toml",
            "-o",
            "requirements-lock.txt",
            "--python",
            str(venv_python),
        ],
        cwd=repo_path,
        check=True,
    )

    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-r",
            "requirements-lock.txt",
            "--python",
            str(venv_python),
        ],
        cwd=repo_path,
        check=True,
    )
