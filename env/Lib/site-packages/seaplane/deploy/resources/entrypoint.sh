#!/bin/bash

set -euxo pipefail

chmod go+w /data

PROJECT_FOLDER=<PROJECT>

cd "${PROJECT_FOLDER}"

# unzip -o "project.zip"
# rm project.zip

project_name=$(toml get --toml-path pyproject.toml tool.poetry.name)
main_file=$(toml get --toml-path pyproject.toml tool.seaplane.main)

# cd ..
# cp -a "${PROJECT_FOLDER}" "${project_name}" #rename folder to project name
# cd "${project_name}"

poetry lock
poetry install

export DEBUG_MODE=1
export SEAPLANE_PROXY_ADDRESS="endpoints:4195"
export PYTHONUNBUFFERED=1
poetry run python3 "${project_name}/${main_file}"