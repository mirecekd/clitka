FROM python:3.13-slim

LABEL org.opencontainers.image.title="clitka" \
      org.opencontainers.image.description="CLITKA - CLI ToolKit for AWS" \
      org.opencontainers.image.source="https://github.com/mirecekd/clitka" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TERM=xterm-256color

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

# ponytail: no session-manager-plugin / sam / cdk in the image. Ceiling: EC2 SSM,
# ECS exec and IaC wrappers are unavailable in the container. Upgrade path: a
# separate "full" image variant that installs those binaries.

ENTRYPOINT ["clitka"]
CMD ["--help"]
