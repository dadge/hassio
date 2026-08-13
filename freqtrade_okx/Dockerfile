# Base image choice: the official freqtradeorg/freqtrade image instead of the
# Home Assistant add-on base image. Rationale:
#   * It ships Freqtrade + all native deps (TA-Lib, numpy, pandas) pre-built
#     for amd64/arm64/armv7 — rebuilding those on the Alpine HA base image on
#     an end-user's Raspberry Pi would take hours and is fragile.
#   * The HA base image mostly buys us bashio/s6, which a single-process bot
#     does not need; the Supervisor talks to the container via its API and
#     /data/options.json either way.
ARG BUILD_FROM=freqtradeorg/freqtrade:2026.7
FROM ${BUILD_FROM}

# The freqtrade image runs as ftuser; the add-on needs root for nginx and /data.
USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The base image installs Freqtrade with `pip install -e . --user` as ftuser,
# i.e. into /home/ftuser/.local with an *editable* install: importing it needs
# the .pth hooks that Python only evaluates for the user site directory. As
# root that directory would be /root/.local, so `freqtrade` would be on PATH
# but `import freqtrade` would fail with ModuleNotFoundError.
#
# PYTHONUSERBASE (not PYTHONPATH) is the right lever: it moves the user site
# itself, so site.py runs addsitedir() on it and the editable hooks register.
# It applies to run.sh, the ft-* helpers and any `docker exec` alike.
ENV PYTHONUSERBASE=/home/ftuser/.local

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx jq curl ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# GodStra, Heracles and Zeus (bundled community strategies) import `ta`, which
# Freqtrade does not depend on. technical / pandas_ta / scipy / scikit-learn
# already ship with the base image.
RUN pip install --no-cache-dir "ta==0.11.0" \
    && python3 -c "import ta; print('ta', ta.__name__, 'importable')"

# Note: the official image already bundles the FreqUI build matching the
# release (freqtrade/rpc/api_server/ui/installed), so no install-ui step.

COPY rootfs/ /
RUN chmod a+x /run.sh /usr/local/bin/ft-* \
    && python3 -m py_compile /opt/ha-panel/control.py

# Fail the BUILD if Freqtrade is not importable as root, rather than shipping
# an image whose add-on dies at startup. Also pins down that the CLI works.
RUN whoami | grep -qx root \
    && python3 -c "import freqtrade; print('freqtrade importable as root:', freqtrade.__file__)" \
    && freqtrade --version

ARG BUILD_ARCH
ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION}

LABEL \
    io.hass.name="Freqtrade OKX Rebound Bot" \
    io.hass.description="Freqtrade bot for OKX spot with dip-rebound strategy" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    io.hass.arch=${BUILD_ARCH}

WORKDIR /
# The freqtrade image sets ENTRYPOINT ["freqtrade"]; reset it.
ENTRYPOINT []
CMD ["/run.sh"]
