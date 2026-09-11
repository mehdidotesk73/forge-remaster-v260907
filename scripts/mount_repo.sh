#!/usr/bin/env bash
set -e

if [ -z "$1" ]; then
    echo "Usage: mount-repo.sh <path-to-repo>"
    exit 1
fi

REPO_PATH="$(cd "$1" && pwd)"

echo "Mounting repo at: $REPO_PATH"

if [ ! -f "$REPO_PATH/pyproject.toml" ]; then
    echo "Error: no pyproject.toml found at $REPO_PATH"
    exit 1
fi

cd "$REPO_PATH"

if [ -d ".venv" ]; then
    echo "Removing existing .venv..."
    rm -rf .venv
fi

echo "Creating new venv..."
uv venv
VENV_PYTHON="$REPO_PATH/.venv/bin/python"

echo "Compiling dependencies from pyproject.toml..."
uv pip compile pyproject.toml -o requirements-lock.txt --python "$VENV_PYTHON"

echo "Installing dependencies..."
uv pip install -r requirements-lock.txt --python "$VENV_PYTHON" --config-settings editable_mode=compat

echo "Writing .vscode/settings.json..."
mkdir -p .vscode
cat > .vscode/settings.json << SETTINGS
{
    "python.defaultInterpreterPath": "$VENV_PYTHON"
}
SETTINGS

echo "Opening in VS Code..."
code "$REPO_PATH"

echo "Done. VS Code should auto-activate the venv and resolve imports correctly."
echo "If you're working in this terminal instead, run: source .venv/bin/activate"