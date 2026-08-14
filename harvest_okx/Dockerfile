# Base image choice: a plain Python image rather than the freqtrade one used by
# the sibling add-on. This bot is a portfolio rebalancer, not a signal engine —
# it needs ccxt and nothing else, so pulling in Freqtrade, TA-Lib, pandas and
# numpy would cost hundreds of megabytes on a Raspberry Pi for no benefit.
# python:3.12-slim is multi-arch, so one tag covers amd64/aarch64/armv7.
ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ccxt is the only runtime dependency; the panel and HTTP API are stdlib.
# Pinned so an upstream change cannot alter live order behaviour on a restart.
RUN pip install --no-cache-dir "ccxt==4.4.*"

COPY rootfs/ /
RUN chmod a+x /run.sh /opt/harvest/bot.py

ARG BUILD_ARCH
ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION}
ENV PYTHONUNBUFFERED=1

LABEL \
    io.hass.name="OKX Volatility Harvester" \
    io.hass.description="Constant-weight rebalancing bot for OKX spot" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    io.hass.arch=${BUILD_ARCH}

WORKDIR /
ENTRYPOINT []
CMD ["/run.sh"]
