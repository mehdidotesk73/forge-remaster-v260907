from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from forge.manifest.manifest_build.spinup import spinup_manifest_repo
from forge.manifest.manifest_build.builder import build_msdk_within_session
from forge.manifest.manifest_build.open import open_manifest_repo

DB_URL = "postgresql://forge:forge_dev_password@localhost:5432/forge_dev"

router = APIRouter(prefix="/manifest", tags=["manifest"])


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
