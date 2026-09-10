#!/usr/bin/env bash
set -e

echo "Forge setup: checking external dependencies..."
echo

# --- uv ---
if command -v uv >/dev/null 2>&1; then
    echo "✓ uv is already installed ($(uv --version))"
else
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || true
    # The installer may fail to auto-configure shell PATH (e.g. due to
    # permission issues on rarely-used config files like .bash_profile) —
    # this is a known quirk, not a fatal error. Ensure PATH ourselves,
    # for both this script's own remaining steps and future sessions.
    export PATH="$HOME/.local/bin:$PATH"

    SHELL_RC="$HOME/.zshrc"
    if [ -n "$BASH_VERSION" ]; then
        SHELL_RC="$HOME/.bashrc"
    fi
    if ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
        echo "Added ~/.local/bin to PATH in $SHELL_RC — restart your terminal"
        echo "or run 'source $SHELL_RC' for this to take effect in new sessions."
    fi
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv installation appears to have failed. Please install manually:"
    echo "  https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi
echo

# --- Docker ---
if command -v docker >/dev/null 2>&1; then
    echo "✓ Docker is already installed ($(docker --version))"
else
    echo "Docker is not installed."
    echo "Forge requires Docker to run its local/test Postgres databases."
    echo "Please install it manually: https://www.docker.com/products/docker-desktop"
fi
echo

# --- Python venv + Forge's own dependencies ---
if [ ! -d ".venv" ]; then
    echo "Creating Forge's own virtual environment (.venv)..."
    uv venv
else
    echo "✓ .venv already exists"
fi

echo "Compiling Forge's own dependency lock file..."
uv pip compile pyproject.toml -o requirements-lock.txt --python .venv/bin/python

echo "Installing Forge's own dependencies..."
uv pip install -r requirements-lock.txt --python .venv/bin/python
echo

# --- .env files ---
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it with your local database credentials."
else
    echo "✓ .env already exists"
fi

if [ ! -f "tests/.env" ]; then
    cp tests/.env.example tests/.env
    echo "Created tests/.env from tests/.env.example."
else
    echo "✓ tests/.env already exists"
fi

echo
echo "Setup complete. Next steps:"
echo "  1. Activate the venv:  source .venv/bin/activate"
echo "  2. Start the dev database:  docker compose up -d"
echo "  3. Run the test suite:  pytest -v"