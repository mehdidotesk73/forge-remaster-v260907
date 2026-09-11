#!/usr/bin/env bash
set -e

if [ -z "$1" ]; then
    echo "Usage: mount-repo.sh <path-to-repo>"
    exit 1
fi

REPO_PATH="$(cd "$1" && pwd)"  # resolve to absolute path

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

echo "Compiling dependencies from pyproject.toml..."
uv pip compile pyproject.toml -o requirements-lock.txt --python .venv/bin/python

echo "Installing dependencies..."
uv pip install -r requirements-lock.txt --python .venv/bin/python

echo "Opening in VS Code..."
code "$REPO_PATH"

echo "Done. VS Code should auto-activate the venv in its integrated terminal."
echo "If you're working in this terminal instead, run: source .venv/bin/activate"