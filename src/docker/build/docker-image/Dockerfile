ARG STAGING_REGISTRY=localhost:5000
ARG STAGING_VERSION=latest

# Creates environment to run Seedsync python code
# Installs all python dependencies
FROM python:3.8-slim AS seedsync_run_python_env

# Install dependencies
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/Components: main/Components: main contrib non-free/g' /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i -e 's/ main$/ main contrib non-free/g' /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        gcc \
        libssl-dev \
        lftp \
        openssh-client \
        p7zip \
        p7zip-full \
        p7zip-rar \
        bzip2 \
        curl \
        libnss-wrapper \
        libxml2-dev libxslt-dev libffi-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
# Fix for patoolib
# See: https://github.com/wummel/patool/issues/90
RUN ln -s /usr/lib/p7zip/Codecs/Rar.so /usr/lib/p7zip/Codecs/Rar29.so

# Keep Poetry on a release line that still supports Python 3.8 and the
# current package-mode-based dependency-only configuration.
RUN pip3 install "poetry==1.8.5"
RUN poetry config virtualenvs.create false

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# Install Python dependencies
RUN mkdir -p /app
COPY src/python/pyproject.toml /app/python/
COPY src/python/poetry.lock /app/python/
RUN cd /app/python && poetry install --only main


# Creates environment for Python dev
FROM seedsync_run_python_env AS seedsync_run_python_devenv
# The main dependency install downgrades requests to the application's pinned
# runtime version, so refresh Poetry before adding dev-only dependencies.
RUN pip3 install --force-reinstall "poetry==1.8.5"
RUN poetry config virtualenvs.create false
RUN cd /app/python && poetry install


# Installs Seedsync python code
FROM seedsync_run_python_env AS seedsync_run_python
RUN mkdir -p /app
COPY src/python /app/python
RUN chmod -R a+rX /app/python


# Full Seedsync docker image
FROM ${STAGING_REGISTRY}/seedsync/build/angular/export:${STAGING_VERSION} AS seedsync_build_angular_export
FROM ${STAGING_REGISTRY}/seedsync/build/scanfs/export:${STAGING_VERSION} AS seedsync_build_scanfs_export
FROM seedsync_run_python AS seedsync_run
LABEL org.opencontainers.image.source="https://github.com/truedarkdev/seedsync"
COPY --from=seedsync_build_angular_export /html /app/html
COPY --from=seedsync_build_scanfs_export /scanfs /app/scanfs
COPY src/docker/build/docker-image/setup_default_config.sh /scripts/

# Disable the known hosts prompt
RUN mkdir -p /root/.ssh && echo "StrictHostKeyChecking no\nUserKnownHostsFile /dev/null" > /root/.ssh/config

# SSH as any user fix
# https://stackoverflow.com/a/57531352
COPY src/docker/build/docker-image/run_as_user /usr/local/bin/
RUN sed -i 's/\r$//' /scripts/setup_default_config.sh /usr/local/bin/run_as_user
RUN chmod 755 /usr/local/bin/run_as_user
COPY src/docker/build/docker-image/ssh /usr/local/sbin
RUN sed -i 's/\r$//' /usr/local/sbin/ssh
RUN chmod 755 /usr/local/sbin/ssh
COPY src/docker/build/docker-image/scp /usr/local/sbin
RUN sed -i 's/\r$//' /usr/local/sbin/scp
RUN chmod 755 /usr/local/sbin/scp
RUN chmod 755 /scripts/setup_default_config.sh


# Create non-root user and directories under that user
RUN groupadd -g 1000 seedsync && \
    useradd -r -u 1000 -g seedsync seedsync
RUN mkdir /config && \
    mkdir /downloads && \
    chown seedsync:seedsync /config && \
    chown seedsync:seedsync /downloads

# Switch to non-root user
USER seedsync

# First time config setup and replacement
RUN /scripts/setup_default_config.sh

# Must run app inside shell
# Otherwise the container crashes as soon as a child process exits
CMD [ \
    "python", \
    "/app/python/seedsync.py", \
    "-c", "/config", \
    "--html", "/app/html", \
    "--scanfs", "/app/scanfs" \
]

EXPOSE 8800
