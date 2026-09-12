from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from forge.manifest.manifest_build.spinup import (
    spinup_manifest_repo,
    git_spinup_manifest_repo,
)
from forge.manifest.manifest_build.builder import (
    build_msdk_within_session,
    git_build_manifest_repo,
)
from forge.manifest.manifest_build.open import (
    open_manifest_repo,
    git_open_manifest_repo,
)
from forge.manifest.manifest_build.git_ops import (
    clone_repo,
    commit_and_push,
    tag_repo,
    GitOperationError,
)

DB_URL = "postgresql://forge:forge_dev_password@localhost:5432/forge_dev"

router = APIRouter(prefix="/manifest", tags=["manifest"])


# ---- local, filesystem-only operations ----


class SpinupRequest(BaseModel):
    target_dir: str


class SpinupResponse(BaseModel):
    repo_path: str


@router.post("/spinup", response_model=SpinupResponse)
def spinup(req: SpinupRequest) -> SpinupResponse:
    try:
        repo_path = spinup_manifest_repo(req.target_dir)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return SpinupResponse(repo_path=str(repo_path))


class BuildRequest(BaseModel):
    repo_dir: str


class BuildResponse(BaseModel):
    generated_file: str


@router.post("/build", response_model=BuildResponse)
def build(req: BuildRequest) -> BuildResponse:
    repo_path = Path(req.repo_dir)
    declarations_dir = repo_path / "src" / "declarations"
    output_dir = repo_path / "_build"

    if not declarations_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No src/declarations found at {repo_path} — is this a valid manifest repo?",
        )

    engine = create_engine(DB_URL)
    try:
        with Session(engine) as session:
            with session.begin():
                generated_file = build_msdk_within_session(
                    str(declarations_dir), str(output_dir), session
                )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return BuildResponse(generated_file=str(generated_file))


class OpenRequest(BaseModel):
    repo_dir: str
    open_editor: bool = True


class OpenResponse(BaseModel):
    repo_path: str


@router.post("/open", response_model=OpenResponse)
def open_repo(req: OpenRequest) -> OpenResponse:
    try:
        repo_path = open_manifest_repo(req.repo_dir, open_editor=req.open_editor)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return OpenResponse(repo_path=str(repo_path))


class RegistryResponse(BaseModel):
    registry: dict


@router.get("/registry", response_model=RegistryResponse)
def get_registry(repo_dir: str) -> RegistryResponse:
    repo_path = Path(repo_dir)
    registry_file = repo_path / "_build" / "registry.json"

    if not registry_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No registry.json found at {registry_file} — has this repo been built yet?",
        )

    registry_data = json.loads(registry_file.read_text())
    return RegistryResponse(registry=registry_data)


# ---- generic git primitives ----


class CloneRequest(BaseModel):
    git_url: str
    target_dir: str


class CloneResponse(BaseModel):
    repo_path: str


@router.post("/clone", response_model=CloneResponse)
def clone(req: CloneRequest) -> CloneResponse:
    try:
        repo_path = clone_repo(req.git_url, req.target_dir)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GitOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return CloneResponse(repo_path=str(repo_path))


class GitCommitRequest(BaseModel):
    repo_dir: str


class GitCommitResponse(BaseModel):
    committed: bool
    pushed: bool


@router.post("/git-commit", response_model=GitCommitResponse)
def git_commit(req: GitCommitRequest) -> GitCommitResponse:
    try:
        result = commit_and_push(req.repo_dir)
    except GitOperationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return GitCommitResponse(**result)


class TagRequest(BaseModel):
    repo_dir: str
    tag: str


class TagResponse(BaseModel):
    tag: str
    pushed: bool


@router.post("/tag", response_model=TagResponse)
def tag(req: TagRequest) -> TagResponse:
    try:
        result = tag_repo(req.repo_dir, req.tag)
    except GitOperationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return TagResponse(**result)


# ---- composed git-aware operations ----


class GitSpinupRequest(BaseModel):
    git_url: str


class GitSpinupResponse(BaseModel):
    repo_path: str
    committed: bool
    pushed: bool


@router.post("/git-spinup", response_model=GitSpinupResponse)
def git_spinup(req: GitSpinupRequest) -> GitSpinupResponse:
    try:
        result = git_spinup_manifest_repo(req.git_url)
    except GitOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return GitSpinupResponse(**result)


class GitOpenRequest(BaseModel):
    git_url: str
    branch: str = "main"
    open_editor: bool = True


class GitOpenResponse(BaseModel):
    repo_path: str


@router.post("/git-open", response_model=GitOpenResponse)
def git_open(req: GitOpenRequest) -> GitOpenResponse:
    try:
        result = git_open_manifest_repo(
            req.git_url, branch=req.branch, open_editor=req.open_editor
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GitOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return GitOpenResponse(**result)


class GitBuildRequest(BaseModel):
    git_url: str
    tag: str


class GitBuildResponse(BaseModel):
    repo_path: str
    generated_file: str
    tag: str
    committed: bool
    pushed: bool


@router.post("/git-build", response_model=GitBuildResponse)
def git_build(req: GitBuildRequest) -> GitBuildResponse:
    engine = create_engine(DB_URL)
    try:
        result = git_build_manifest_repo(req.git_url, req.tag, engine)
    except GitOperationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return GitBuildResponse(**result)
