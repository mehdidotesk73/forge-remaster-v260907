from pathlib import Path

from fastapi.testclient import TestClient

from forge.api.main import app

client = TestClient(app)


def _write_product_declaration(repo_path: Path):
    declaration = """
from forge.manifest import ManifestObjectDef, ManifestFieldDef, STRING, INT

ManifestObjectDef(
    display_name="Product", api_name="Product",
    fields={
        "product_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
        "name": ManifestFieldDef(type=STRING, nullable=True),
        "cost": ManifestFieldDef(type=INT, nullable=True, index=True),
    },
)
"""
    (repo_path / "src" / "declarations" / "example.py").write_text(declaration)


def test_spinup_creates_repo(tmp_path):
    repo_path = tmp_path / "repo"
    response = client.post("/manifest/spinup", json={"target_dir": str(repo_path)})
    assert response.status_code == 200
    assert response.json()["repo_path"] == str(repo_path)
    assert (repo_path / "pyproject.toml").exists()


def test_spinup_conflict_on_existing_repo(tmp_path):
    repo_path = tmp_path / "repo"
    client.post("/manifest/spinup", json={"target_dir": str(repo_path)})
    response = client.post("/manifest/spinup", json={"target_dir": str(repo_path)})
    assert response.status_code == 409


def test_open_sets_up_environment_without_launching_editor(tmp_path):
    repo_path = tmp_path / "repo"
    client.post("/manifest/spinup", json={"target_dir": str(repo_path)})

    response = client.post(
        "/manifest/open",
        json={"repo_dir": str(repo_path), "open_editor": False},
    )
    assert response.status_code == 200
    assert (repo_path / ".venv").exists()
    assert (repo_path / ".vscode" / "settings.json").exists()


def test_open_nonexistent_repo_returns_404(tmp_path):
    repo_path = tmp_path / "repo"
    response = client.post(
        "/manifest/open",
        json={"repo_dir": str(repo_path), "open_editor": False},
    )
    assert response.status_code == 404


def test_build_generates_expected_files(tmp_path):
    repo_path = tmp_path / "repo"
    client.post("/manifest/spinup", json={"target_dir": str(repo_path)})
    _write_product_declaration(repo_path)

    response = client.post("/manifest/build", json={"repo_dir": str(repo_path)})
    assert response.status_code == 200

    generated_file = Path(response.json()["generated_file"])
    assert generated_file.exists()
    assert "Product" in generated_file.read_text()

    init_file = repo_path / "_build" / "__init__.py"
    assert init_file.exists()
    assert "Product" in init_file.read_text()
    assert "ProductSet" in init_file.read_text()


def test_build_missing_declarations_dir_returns_404(tmp_path):
    repo_path = tmp_path / "repo"
    response = client.post("/manifest/build", json={"repo_dir": str(repo_path)})
    assert response.status_code == 404


def test_get_registry_returns_built_repo_shape(tmp_path):
    repo_path = tmp_path / "repo"
    client.post("/manifest/spinup", json={"target_dir": str(repo_path)})
    _write_product_declaration(repo_path)
    client.post("/manifest/build", json={"repo_dir": str(repo_path)})

    response = client.get("/manifest/registry", params={"repo_dir": str(repo_path)})
    assert response.status_code == 200
    registry = response.json()["registry"]
    assert "Product" in registry["objects"]
    assert registry["objects"]["Product"]["pk_field"] == "product_id"


def test_get_registry_missing_returns_404(tmp_path):
    repo_path = tmp_path / "repo"
    client.post("/manifest/spinup", json={"target_dir": str(repo_path)})
    response = client.get("/manifest/registry", params={"repo_dir": str(repo_path)})
    assert response.status_code == 404
