# Creates environment for angular
FROM node:24-bookworm-slim AS seedsync_build_angular_env
RUN apt-get update && apt-get install -y \
    build-essential \
    python3 \
    && rm -rf /var/lib/apt/lists/*
# Copy only the tracked manifest; the repo-local Angular lockfile is ignored.
COPY src/angular/package.json /app/
WORKDIR /app
# Install from the tracked manifest only; the repo-local Angular lockfile is ignored.
RUN npm install --legacy-peer-deps --no-package-lock


# Builds angular app into html
FROM seedsync_build_angular_env AS seedsync_build_angular
COPY src/angular /app
WORKDIR /app
RUN mkdir -p /build/dist
RUN npx ng build --configuration production --output-path /build/dist/

# Creates environment to run Seedsync python code
# Installs all python dependencies
FROM python:3.12-slim-bullseye AS seedsync_run_python_env

# Install the runtime toolchain in a single layer. The package set stays
# intentionally unpinned so rebuilds pick up security refreshes.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/Components: main/Components: main contrib non-free/g' /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i -e 's/ main$/ main contrib non-free/g' /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        lftp \
        rclone \
        openssh-client \
        p7zip \
        p7zip-full \
        p7zip-rar \
        bzip2 \
        curl \
        libnss-wrapper \
        libxml2-dev libxslt-dev libffi-dev \
    && ln -s /usr/lib/p7zip/Codecs/Rar.so /usr/lib/p7zip/Codecs/Rar29.so \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Keep Poetry pinned so the lockfile generation and image installs stay aligned.
RUN pip3 install "poetry==2.3.2" \
    && poetry config virtualenvs.create false

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV UMASK=""

# Install Python dependencies
RUN mkdir -p /app
COPY src/python/pyproject.toml /app/python/
COPY src/python/poetry.lock /app/python/
RUN cd /app/python && poetry install --only main


# Creates environment for Python dev
FROM seedsync_run_python_env AS seedsync_run_python_devenv
RUN pip3 install --force-reinstall "poetry==2.3.2" \
    && poetry config virtualenvs.create false \
    && cd /app/python \
    && poetry install


# Installs Seedsync python code
FROM seedsync_run_python_env AS seedsync_run_python
RUN mkdir -p /app
COPY src/python /app/python
RUN chmod -R a+rX /app/python


# Full Seedsync docker image
FROM seedsync_run_python AS seedsync_run
LABEL org.opencontainers.image.source="https://github.com/truedarkdev/seedsync"
COPY --from=seedsync_build_angular /build/dist /app/html
COPY src/docker/build/docker-image/entrypoint.sh /scripts/
RUN set -eux; \
    python3 -m pip uninstall -y pip setuptools

# Accept new host keys on first connection while still rejecting changed keys.
RUN mkdir -p /root/.ssh && echo "StrictHostKeyChecking accept-new" > /root/.ssh/config

RUN chmod 755 /scripts/entrypoint.sh


# Create non-root user and directories under that user
RUN groupadd -g 1000 seedsync && \
    useradd -r -u 1000 -g seedsync seedsync
RUN mkdir /config && \
    mkdir /downloads && \
    mkdir /mounts && \
    mkdir /staging && \
    mkdir -p /home/seedsync/.ssh && \
    echo "StrictHostKeyChecking accept-new" > /home/seedsync/.ssh/config && \
    chown seedsync:seedsync /config && \
    chown seedsync:seedsync /downloads && \
    chown seedsync:seedsync /mounts && \
    chown seedsync:seedsync /staging && \
    chown -R seedsync:seedsync /home/seedsync && \
    chmod 700 /home/seedsync/.ssh && \
    chmod 600 /home/seedsync/.ssh/config

# Switch to non-root user
USER seedsync

# First time config setup and replacement
# Seed the default config during image build; runtime bootstrap uses the same tracked code path.
RUN /scripts/entrypoint.sh --bootstrap-default-config

# Switch back to root so the runtime entrypoint can validate IDs, fix ownership,
# and drop privileges with setpriv before launching the app.
USER root

ENTRYPOINT [ "/scripts/entrypoint.sh" ]

# Must run app inside shell
# Otherwise the container crashes as soon as a child process exits
CMD [ \
    "/bin/bash", \
    "-lc", \
    "exec python /app/python/seedsync.py -c /config --html /app/html --scanfs /app/python/scan_fs.py --web-bind-host \"${SEEDSYNC_WEB_BIND_HOST:-0.0.0.0}\"" \
]

EXPOSE 8800
VOLUME [ "/config", "/downloads" ]
