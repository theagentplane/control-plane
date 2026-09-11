# syntax=docker/dockerfile:1
FROM python:3.12-slim

LABEL org.opencontainers.image.title="agentplane-control-plane"
LABEL org.opencontainers.image.description="Shared AgentPlane control plane: Chronicle envelopes + TokenOps governance over HTTP"
LABEL org.opencontainers.image.source="https://github.com/theagentplane/control-plane"

WORKDIR /app

# Only what pyproject.toml + hatchling need to build the wheel (see
# [tool.hatch.build.targets.wheel] / force-include) — keeps the build context and
# image small, and cache-friendly (dependency layer doesn't invalidate on doc edits).
COPY pyproject.toml README.md LICENSE ./
COPY config ./config
COPY control_plane ./control_plane

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# SQLite lives on a volume so it survives `docker compose down` / image upgrades.
ENV CONTROL_PLANE_DB=/data/control_plane.db
VOLUME ["/data"]

# Least privilege: the process only ever needs to read/write /data.
RUN useradd --create-home --uid 1000 controlplane \
    && mkdir -p /data \
    && chown -R controlplane:controlplane /data
USER controlplane

EXPOSE 8800

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/health', timeout=2)" || exit 1

# Foreground, PID 1 — the container is the process supervisor. `control-plane start`
# (the background/detached mode for local dev) must not be used here: if PID 1 exits
# right after spawning a detached child, the container exits with it.
CMD ["control-plane", "serve", "--host", "0.0.0.0", "--port", "8800"]
