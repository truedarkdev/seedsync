# Build scanfs for the remote seedbox architecture, not the local build host.
ARG SCANFS_PLATFORM=linux/amd64

# Creates environment to build python binaries
FROM python:3.12-slim-bullseye AS seedsync_build_pyinstaller_env
RUN apt-get update && apt-get install -y \
    binutils \
    build-essential \
    curl \
    libffi-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
# Install Poetry
RUN pip3 install "poetry==2.3.2"
RUN poetry config virtualenvs.create false
COPY src/python/pyproject.toml /app/python/
COPY src/python/poetry.lock /app/python/
WORKDIR /app/python
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
RUN poetry install


# Builds seedsync with pyinstaller
# Output is in /build/dist/seedsync/
FROM seedsync_build_pyinstaller_env AS seedsync_build_pyinstaller
COPY src/python /python
COPY src/pyinstaller_hooks /pyinstaller_hooks
RUN mkdir -p /build
RUN pyinstaller /python/seedsync.py \
    -y \
    -p /python \
    --distpath /build/dist \
    --workpath /build/work \
    --specpath /build \
    --additional-hooks-dir /pyinstaller_hooks/ \
    --hidden-import="six" \
    --hidden-import="six.moves" \
    --name seedsync


# Creates environment to build scanfs binaries
FROM --platform=${SCANFS_PLATFORM} python:3.12-slim-bullseye AS seedsync_build_scanfs_env
RUN apt-get update && apt-get install -y \
    binutils \
    build-essential \
    curl \
    libffi-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
# Install Poetry
RUN pip3 install "poetry==2.3.2"
RUN poetry config virtualenvs.create false
COPY src/python/pyproject.toml /app/python/
COPY src/python/poetry.lock /app/python/
WORKDIR /app/python
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
RUN poetry install


# Builds scanfs with pyinstaller
# Output is in /build/dist/
FROM seedsync_build_scanfs_env AS seedsync_build_scanfs
COPY src/python /python
RUN mkdir -p /build
RUN	pyinstaller /python/scan_fs.py \
    -y \
    --onefile \
    -p /python \
    --distpath /build/dist \
    --workpath /build/work \
    --specpath /build \
    --name scanfs


# Creates environment for angular
FROM node:24-bookworm-slim AS seedsync_build_angular_env
RUN apt-get update && apt-get install -y \
    build-essential \
    python3 \
    && rm -rf /var/lib/apt/lists/*
COPY src/angular/package*.json /app/
WORKDIR /app
RUN npm ci --legacy-peer-deps

# Builds angular app into html
# Output is in /build/dist/
FROM seedsync_build_angular_env AS seedsync_build_angular
COPY src/angular /app
WORKDIR /app
RUN mkdir -p /build/dist
RUN npx ng build --configuration production --output-path /build/dist/


# Creates environment to build deb packages
FROM ubuntu:22.04 AS seedsync_build_deb_env
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    build-essential \
    debhelper \
    devscripts \
    && rm -rf /var/lib/apt/lists/*


# Builds debian package
# Output is in /build/dist/
FROM seedsync_build_deb_env AS seedsync_build_deb
RUN mkdir -p /build/work
COPY src/debian /build/work/debian
COPY --from=seedsync_build_pyinstaller /build/dist/seedsync /build/work/seedsync
# PyInstaller resolves bundled support files from its _internal directory.
COPY --from=seedsync_build_scanfs /build/dist/scanfs /build/work/seedsync/_internal/
COPY --from=seedsync_build_angular /build/dist /build/work/seedsync/_internal/html
WORKDIR /build/work
RUN dpkg-buildpackage -B -uc -us
RUN ls /build/*.deb && echo "----" && ls /build/work


# Exports deb package to host
FROM scratch AS seedsync_build_deb_export
COPY --from=seedsync_build_deb /build/*.deb .

# Exports scanfs to host
FROM scratch AS seedsync_build_scanfs_export
COPY --from=seedsync_build_scanfs /build/dist/scanfs .

# Exports html to host
FROM scratch AS seedsync_build_angular_export
COPY --from=seedsync_build_angular /build/dist ./html
