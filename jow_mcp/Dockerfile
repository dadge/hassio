# Base image: a plain Python image, pinned, following the sibling harvest_okx
# add-on. The Supervisor always injects --build-arg BUILD_FROM, but it only
# honours a build.yaml value matching a namespaced-image regex; an official
# image like `python:3.12-slim` fails that regex and is silently swapped for
# the Alpine HA base, where apt-get/jq differ. So the base is fixed here and
# the injected BUILD_FROM ignored on purpose.
FROM docker.io/library/python:3.12-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Runtime dependencies for the MCP server. Pinned majors so a restart can't
# pull a breaking change unattended.
RUN pip install --no-cache-dir \
      "mcp>=1.6,<2" \
      "httpx>=0.27,<1" \
      "uvicorn>=0.30,<1" \
      "starlette>=0.37,<1"

COPY rootfs/ /
RUN chmod a+x /run.sh

ARG BUILD_ARCH
ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/jow-mcp

LABEL \
    io.hass.name="Jow MCP" \
    io.hass.description="Read Jow recipes and meal plans over MCP, linked to your account" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    io.hass.arch=${BUILD_ARCH}

WORKDIR /opt/jow-mcp
ENTRYPOINT []
CMD ["/run.sh"]
