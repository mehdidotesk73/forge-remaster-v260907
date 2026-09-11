import pytest
from forge.manifest.manifest_build.spinup import spinup_manifest_repo


def test_spinup_creates_expected_structure(tmp_path):
    target = tmp_path / "my_manifest_repo"
    result = spinup_manifest_repo(str(target))

    assert result == target
    assert (target / "pyproject.toml").is_file()
    assert (target / "README.md").is_file()
    assert (target / "__init__.py").is_file()
    assert (target / "src" / "declarations" / "example.py").is_file()
    assert (target / "_build" / "__init__.py").is_file()


def test_spinup_auto_converts_invalid_name(tmp_path):
    requested = tmp_path / "my-manifest-repo"
    result = spinup_manifest_repo(str(requested))

    assert result != requested
    assert result == tmp_path / "my_manifest_repo"
    assert result.name.isidentifier()
    assert (result / "pyproject.toml").is_file()
    assert (result / "src" / "declarations").is_dir()
    assert (result / "_build").is_dir()


def test_spinup_raises_if_already_exists(tmp_path):
    target = tmp_path / "my_manifest_repo"
    spinup_manifest_repo(str(target))

    with pytest.raises(FileExistsError):
        spinup_manifest_repo(str(target))
