#!/bin/bash
# Legacy wrapper; the Makefile is the canonical setup entrypoint.

set -e

echo "=== Team Vin Genius setup ==="

command -v make >/dev/null || { echo "make is required"; exit 2; }
make setup

# Create .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env — please edit with your API keys"
fi

echo "Setup complete. Run: make dev"
