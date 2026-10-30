# syntax=docker/dockerfile:1
# nutricore render: chrome-headless-shell turns picture jobs into PNGs (stage 5, slice D). Built from its own context
# (render/), so nothing of the bot's tree reaches this image. The watcher needs Python.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

# Chrome for Testing's headless shell for linux-arm64 (the mini's VM), pinned by version and by the zip's sha256.
ARG CHROME_VERSION=155.0.8059.39
ARG CHROME_SHA256=9fb86f7c0b2734c5febc0bbb4e85f37da43553f3f8a7970949828c5713e87e94
ARG CHROME_DIR=/opt/chrome-headless-shell-linux-arm64
# Nunito (OFL, render/OFL.txt) from google/fonts, pinned by commit and sha256. It is fetched here, not kept in
# the repo: a binary cannot be scanned for personal data. A local preview fetches the same file into render/fonts/.
ARG NUNITO_COMMIT=604936664fd62c14271209b51f98e7f495dd1a3e
ARG NUNITO_SHA256=bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207

# The runtime libraries are the package's own deb.deps list. curl and unzip are only for the download and leave in
# the same layer. The build fails when a library is missing (ldd) or when Chrome cannot start.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libexpat1 libgbm1 \
        libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libudev1 libvulkan1 libx11-6 libxcb1 libxcomposite1 \
        libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 fonts-liberation fontconfig fonts-noto-color-emoji \
    && curl -fsSL -o /tmp/chs.zip \
        "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux-arm64/chrome-headless-shell-linux-arm64.zip" \
    && echo "${CHROME_SHA256}  /tmp/chs.zip" | sha256sum -c - \
    && mkdir -p /opt/render/fonts \
    && curl -fsSL -o /opt/render/fonts/Nunito.ttf \
        "https://raw.githubusercontent.com/google/fonts/${NUNITO_COMMIT}/ofl/nunito/Nunito%5Bwght%5D.ttf" \
    && echo "${NUNITO_SHA256}  /opt/render/fonts/Nunito.ttf" | sha256sum -c - \
    && unzip -q /tmp/chs.zip -d /opt \
    && rm /tmp/chs.zip \
    && test -x "${CHROME_DIR}/chrome-headless-shell" \
    && ! ldd "${CHROME_DIR}/chrome-headless-shell" | grep "not found" \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && "${CHROME_DIR}/chrome-headless-shell" --version

# Chrome and fontconfig write their caches under HOME; the root filesystem is read-only, /tmp is a tmpfs.
ENV CHROME=${CHROME_DIR}/chrome-headless-shell \
    RENDER_DIR=/render \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/render
COPY watcher.py ./
COPY templates ./templates
COPY samples ./samples
# Readable whatever the host's umask was: COPY keeps the context's modes, and the files are owned by root.
RUN chmod -R a+rX /opt/render

# Non-root. /render is owned by it here, and a new named volume takes that owner from the first container to mount it:
# compose starts this service before the bot for that reason. The bot runs as root and can write here anyway.
ARG APP_UID=10001
RUN groupadd -g ${APP_UID} app \
    && useradd -u ${APP_UID} -g app -M -s /usr/sbin/nologin app \
    && mkdir -p /render \
    && chown ${APP_UID}:${APP_UID} /render
USER ${APP_UID}:${APP_UID}

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 CMD ["python", "watcher.py", "health"]
CMD ["python", "watcher.py", "serve"]
