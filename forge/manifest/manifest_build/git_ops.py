from __future__ import annotations
import os
import subprocess
from pathlib import Path

FORGE_BUILD_COMMIT_MESSAGE = "Forge build commit"


class GitOperationError(Exception):
    pass


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def clone_repo(git_url: str, target_dir: str, branch: str | None = None) -> Path:
    target_path = Path(target_dir)
    if target_path.exists():
        raise FileExistsError(
            f"{target_path} already exists — refusing to clone over it"
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)

    args = ["clone"]
    if branch:
        args += ["--branch", branch]
    args += [git_url, str(target_path)]

    result = _run_git(args, cwd=str(target_path.parent))
    if result.returncode != 0:
        raise GitOperationError(
            f"git clone failed for {git_url}.\n\n{result.stderr.strip()}"
        )

    return target_path


def commit_repo(repo_dir: str) -> dict:
    """
    Stages all current changes and commits them with a fixed message.
    Does NOT push. Returns committed=False if there was nothing to commit.
    """
    add_result = _run_git(["add", "-A"], cwd=repo_dir)
    if add_result.returncode != 0:
        raise GitOperationError(f"git add failed.\n\n{add_result.stderr.strip()}")

    diff_check = _run_git(["diff", "--cached", "--quiet"], cwd=repo_dir)
    if diff_check.returncode == 0:
        return {"committed": False}

    commit_result = _run_git(["commit", "-m", FORGE_BUILD_COMMIT_MESSAGE], cwd=repo_dir)
    if commit_result.returncode != 0:
        raise GitOperationError(f"git commit failed.\n\n{commit_result.stderr.strip()}")

    return {"committed": True}


def push_repo(repo_dir: str) -> dict:
    """Pushes the current branch to its already-configured remote."""
    push_result = _run_git(["push"], cwd=repo_dir)
    if push_result.returncode != 0:
        raise GitOperationError(
            "git push failed — this usually means git isn't authenticated with "
            "your remote yet (no SSH key registered, or no cached credentials), "
            "or the remote has changes not present locally.\n\n"
            f"Git error: {push_result.stderr.strip()}"
        )
    return {"pushed": True}


def tag_repo(repo_dir: str, tag: str) -> dict:
    tag_result = _run_git(["tag", tag], cwd=repo_dir)
    if tag_result.returncode != 0:
        raise GitOperationError(f"git tag failed.\n\n{tag_result.stderr.strip()}")

    push_result = _run_git(["push", "origin", tag], cwd=repo_dir)
    if push_result.returncode != 0:
        raise GitOperationError(
            f"git push (tag) failed.\n\n{push_result.stderr.strip()}"
        )

    return {"tag": tag, "pushed": True}


def commit_and_push(repo_dir: str) -> dict:
    commit_result = commit_repo(repo_dir)
    if not commit_result["committed"]:
        return {"committed": False, "pushed": False}
    push_result = push_repo(repo_dir)
    return {"committed": True, "pushed": push_result["pushed"]}
