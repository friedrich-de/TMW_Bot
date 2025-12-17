FROM mcr.microsoft.com/devcontainers/python:3.12

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

USER vscode
RUN pipx install uv \
    && pipx install ruff
