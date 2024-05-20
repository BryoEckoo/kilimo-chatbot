#!/bin/bash

set -euxo pipefail

echo "Installing dependencies ..."
# Used for initializing & running a user's project
pip install poetry==1.6.1
# Used to extract the project name & main file from the `pyproject.toml` so we
# can invoke it inside `entrypoint.sh`, and to modify users' pyproject.toml
# files to point to the cached version of the Seaplane SDK 
pip install toml-cli
echo "Dependencies installed."

# Build a wheel of the SDK 
pip install seaplane==<VERSION>

# Cache the seaplane-sdk dependencies
echo "Building cache..."
cd <PROJECT>
poetry config --local virtualenvs.in-project true
poetry config --local virtualenvs.path .venv 
poetry config --local virtualenvs.create true 
poetry install 
echo "Built cache."