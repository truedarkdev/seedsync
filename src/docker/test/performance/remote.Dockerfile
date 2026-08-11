FROM ubuntu:22.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 remoteuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash remoteuser \
    && echo 'remoteuser:remotepass' | chpasswd \
    && mkdir -p /var/run/sshd /home/remoteuser/.ssh /home/remoteuser/files \
    && sed -i 's/^#Port 22/Port 1234/' /etc/ssh/sshd_config \
    && printf 'StrictHostKeyChecking no\n' > /home/remoteuser/.ssh/config \
    && chown -R remoteuser:remoteuser /home/remoteuser \
    && chmod 700 /home/remoteuser/.ssh \
    && chmod 600 /home/remoteuser/.ssh/config

# The application persists scanner state across restarts and can therefore
# legitimately skip re-uploading this helper. Keep the retained test seedbox
# equivalent to a persistent remote host even when Compose recreates it.
COPY src/python/scan_fs.py /tmp/scan_fs.py
RUN chmod 755 /tmp/scan_fs.py

EXPOSE 1234
VOLUME ["/home/remoteuser/files"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
