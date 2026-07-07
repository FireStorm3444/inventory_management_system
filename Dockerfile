# ==========================================
# STAGE 1: Dependency & Bytecode Builder
# ==========================================
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder

# Force uv to pre-compile bytecode (.pyc files) for sub-millisecond container starts
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies only (Docker caches this layer until uv.lock or pyproject.toml changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy the actual application code and finalize sync
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ==========================================
# STAGE 2: Minimal Secure Runtime
# ==========================================
FROM python:3.14-slim-trixie AS runtime

# Prevent Python from buffering logs (forces instant output to your terminal/logs)
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Security baseline: Run as an unprivileged system user, not root
RUN useradd -m -u 1000 imsuser

WORKDIR /app

# Pull the compiled Rust uv binary directly into our runtime stage for dev syncing
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy the compiled virtual environment and source code from the builder stage
COPY --from=builder --chown=imsuser:imsuser /app/.venv /app/.venv
COPY --from=builder --chown=imsuser:imsuser /app /app

USER imsuser

EXPOSE 8000

# Invoke uvicorn directly from the path pointing to our source entrypoint
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]