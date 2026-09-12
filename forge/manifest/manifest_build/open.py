from __future__ import annotations
import subprocess
from pathlib import Path
from .env_init import init_environment


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
