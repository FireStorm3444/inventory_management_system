# ==========================================
# STAGE 1: Dependency & Bytecode Builder
# ==========================================
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder

# ---> FIX: Move the venv outside of /app to prevent host bleed <---
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT="/opt/venv"

WORKDIR /app

# Install the C-linker required by the Rust compiler
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Use COPY to ensure files are writable by Cargo
COPY uv.lock pyproject.toml ./
COPY src/rust_engine src/rust_engine/

# Execute sync using ONLY the cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Copy the rest of the application code
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen


# ==========================================
# STAGE 2: Minimal Secure Runtime
# ==========================================
FROM python:3.14-slim-trixie AS runtime

# Prevent Python from buffering logs (forces instant output to your terminal/logs)
# ---> FIX: Point PATH and UV to the isolated /opt/venv <---
ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT="/opt/venv"

# Security baseline: Run as an unprivileged system user, not root
RUN useradd -m -u 1000 imsuser

WORKDIR /app

# Pull the compiled Rust uv binary directly into our runtime stage for dev syncing
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# ---> FIX: Copy the isolated virtual environment from the builder <---
COPY --from=builder --chown=imsuser:imsuser /opt/venv /opt/venv
COPY --from=builder --chown=imsuser:imsuser /app /app

USER imsuser

EXPOSE 8000

# Invoke uvicorn directly from the path pointing to our source entrypoint
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]