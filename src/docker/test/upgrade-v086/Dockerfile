ARG PYTHON_BASE_IMAGE=python:3.8-slim-bullseye@sha256:e191a71397fd61fbddb6712cd43ef9a2c17df0b5e7ba67607128554cd6bff267
ARG NODE_BASE_IMAGE=node:12.16@sha256:360ac6d2ab708d2d682b70dd4f89e4340d48a5710f8e2acb86993efdbd1c1487

FROM ${NODE_BASE_IMAGE} AS seedsync_build_angular_env
WORKDIR /app/angular
COPY src/angular/package.json src/angular/package-lock.json ./
RUN npm ci --legacy-peer-deps

FROM seedsync_build_angular_env AS seedsync_build_angular
COPY src/angular /app/angular
RUN node -e "const fs=require('fs'); const p='tsconfig.json'; const c=JSON.parse(fs.readFileSync(p, 'utf8')); c.compilerOptions=Object.assign({}, c.compilerOptions, {skipLibCheck:true}); fs.writeFileSync(p, JSON.stringify(c));" \
 && mkdir -p /build \
 && ./node_modules/@angular/cli/bin/ng build --prod --output-path /build/html \
 && (npm ls --all --json > /build/npm-inventory.json || true)

FROM ${PYTHON_BASE_IMAGE}

ARG SOURCE_COMMIT=unknown
ARG SOURCE_TREE=unknown
ARG LAB_HELPER_DIGEST=unknown
ARG ANGULAR_BASE_DIGEST=unknown
ARG ANGULAR_PACKAGE_LOCK_DIGEST=unknown
LABEL org.seedsync.upgrade-v086.source-commit="${SOURCE_COMMIT}" \
      org.seedsync.upgrade-v086.source-tree="${SOURCE_TREE}" \
      org.seedsync.upgrade-v086.lab-helper-digest="${LAB_HELPER_DIGEST}" \
      org.seedsync.upgrade-v086.angular-base-digest="${ANGULAR_BASE_DIGEST}" \
      org.seedsync.upgrade-v086.angular-package-lock-digest="${ANGULAR_PACKAGE_LOCK_DIGEST}" \
      org.seedsync.upgrade-v086.role="legacy"

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bzip2 curl gcc libffi-dev libnss-wrapper libssl-dev \
      libxml2-dev libxslt1-dev lftp openssh-client p7zip-full \
 && rm -rf /var/lib/apt/lists/* \
 && if [ -f /usr/lib/p7zip/Codecs/Rar.so ]; then ln -sf /usr/lib/p7zip/Codecs/Rar.so /usr/lib/p7zip/Codecs/Rar29.so; fi

RUN python -m pip install --no-cache-dir "poetry==1.1.15"

WORKDIR /app/python
COPY src/python/pyproject.toml src/python/poetry.lock ./
RUN poetry install --no-dev --no-interaction --no-ansi

RUN mkdir -p /usr/share/doc/seedsync-upgrade-v086/inventory \
 && dpkg-query -W -f='${Package}\t${Version}\n' | sort > /usr/share/doc/seedsync-upgrade-v086/inventory/dpkg.txt \
 && python -m pip freeze | sort > /usr/share/doc/seedsync-upgrade-v086/inventory/pip.txt

COPY src/python /app/python
COPY --from=seedsync_build_angular /build/html /app/html
COPY --from=seedsync_build_angular /build/npm-inventory.json /usr/share/doc/seedsync-upgrade-v086/inventory/npm.json
COPY .upgrade-v086/entrypoint.sh /usr/local/bin/seedsync-upgrade-v086-entrypoint
RUN chmod 0755 /usr/local/bin/seedsync-upgrade-v086-entrypoint \
 && printf '#!/bin/sh\nif [ -d /opt/seedsync-python ]; then export PYTHONPATH=/opt/seedsync-python; exec python /opt/seedsync-python/scan_fs.py "$@"; fi\nexec python /app/python/scan_fs.py "$@"\n' > /usr/local/bin/scanfs \
 && chmod 0755 /usr/local/bin/scanfs \
 && groupadd --gid 1000 seedsync \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash seedsync \
 && mkdir -p /config /downloads /mounts /logs /home/seedsync/.ssh \
 && chown -R seedsync:seedsync /config /downloads /mounts /logs /home/seedsync \
 && printf 'StrictHostKeyChecking no\nUserKnownHostsFile /dev/null\n' > /home/seedsync/.ssh/config \
 && chown seedsync:seedsync /home/seedsync/.ssh/config \
 && chmod 0600 /home/seedsync/.ssh/config

USER seedsync
EXPOSE 8800
ENTRYPOINT ["/usr/local/bin/seedsync-upgrade-v086-entrypoint"]
