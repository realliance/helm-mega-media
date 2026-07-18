# Multi-stage: build deps against a glibc slim base, then ship them on top of
# distroless (no shell, no package manager, non-root by default). The builder's
# CPython MUST match the distroless interpreter ABI — debian12 distroless ships
# CPython 3.11, so the builder is pinned to 3.11-slim-bookworm (also debian12).
# Bump both together if you move to a newer distroless.
FROM python:3.11-slim-bookworm AS build

WORKDIR /build
COPY requirements.txt .
# --target installs into a plain directory we can copy wholesale onto PYTHONPATH.
# pyyaml's compiled _yaml.so is built here against cp311 and loads unchanged in
# the cp311 distroless runtime.
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

FROM gcr.io/distroless/python3-debian12:nonroot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Cap glibc per-thread arenas — the biggest cheap idle-RSS win for a
    # long-running threaded Python process (health server + reconcile loop).
    MALLOC_ARENA_MAX=2 \
    PYTHONPATH=/deps \
    METRICS_PORT=8000

COPY --from=build /deps /deps
COPY reconciler.py /app/reconciler.py

WORKDIR /app
# Health/metrics surface (see METRICS_PORT). Informational only.
EXPOSE 8000

# distroless :nonroot already runs as uid 65532; no USER needed.
ENTRYPOINT ["python3", "/app/reconciler.py"]
