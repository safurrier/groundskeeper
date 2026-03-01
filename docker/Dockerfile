FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    make \
    curl \
    git \
    vim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Copy essential files for dependency resolution
COPY pyproject.toml uv.lock /tmp/
COPY README.md /tmp/

RUN pip install -U pip uv \
    && cd /tmp \
    && uv sync --system --all-extras \
    && rm -rf /tmp/pyproject.toml /tmp/uv.lock /tmp/README.md

CMD ["/bin/bash"]
