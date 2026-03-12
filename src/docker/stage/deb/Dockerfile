ARG BASE_IMAGE=ubuntu:16.04

FROM $BASE_IMAGE

# Install dependencies
RUN apt-get update && apt-get install -y \
    sudo \
    libssl-dev \
    libexpat1 \
    expect \
    lftp \
    openssh-client

# Create non-root user
RUN useradd --create-home -s /bin/bash user && \
    echo "user:user" | chpasswd && adduser user sudo

# Create directory for downloaded files
RUN mkdir /downloads && \
    chown user:user /downloads

USER user

# Generate a fresh SSH keypair at build time instead of copying a committed
# private key into the stage image.
RUN mkdir -p /home/user/.ssh && \
    ssh-keygen -t rsa -b 4096 -N "" -f /home/user/.ssh/id_rsa && \
    chmod 600 /home/user/.ssh/id_rsa

USER root


# Let user run sudo without password
RUN echo "user ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers

WORKDIR /scripts

ADD src/docker/stage/deb/install_seedsync.sh /scripts/
ADD src/docker/stage/deb/expect_seedsync.exp /scripts/
ADD src/docker/stage/deb/entrypoint.sh /scripts/

# Normalize scripts copied from the Windows-backed checkout before executing
# them inside the Linux container.
RUN sed -i 's/\r$//' /scripts/install_seedsync.sh /scripts/expect_seedsync.exp /scripts/entrypoint.sh && \
    chmod +x /scripts/install_seedsync.sh /scripts/entrypoint.sh

ENTRYPOINT ["/scripts/entrypoint.sh"]
CMD ["/bin/bash", "-c", "exec /lib/systemd/systemd --log-target=journal 3>&1"]

EXPOSE 8800
