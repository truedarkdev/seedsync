FROM seedsync/run/python/devenv as seedsync_test_python

RUN ls -l /app/python

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
        openssh-server \
        p7zip \
        p7zip-full \
        p7zip-rar \
        rar \
        unrar

RUN ln -sf /usr/lib/p7zip/Codecs/Rar.so /usr/lib/p7zip/Codecs/Rar29.so

ADD src/docker/test/python/entrypoint.sh /app/
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 755 /app/entrypoint.sh

# setup sshd
RUN mkdir /var/run/sshd
RUN ssh-keygen -t rsa -N "" -f /root/.ssh/id_rsa && \
    cat /root/.ssh/id_rsa.pub >> /root/.ssh/authorized_keys
# Disable the known hosts prompt
RUN echo "StrictHostKeyChecking no\nUserKnownHostsFile /dev/null\nLogLevel=quiet" > /root/.ssh/config
# Enable password auth in sshd for newer OpenSSH test containers.
RUN echo "PasswordAuthentication yes" >> /etc/ssh/sshd_config && \
    echo "KbdInteractiveAuthentication yes" >> /etc/ssh/sshd_config && \
    echo "UsePAM yes" >> /etc/ssh/sshd_config

# create the seedsynctest user, add root's public key to seedsynctest
RUN useradd --create-home -s /bin/bash seedsynctest && \
    echo "seedsynctest:seedsyncpass" | chpasswd
RUN usermod -a -G root seedsynctest
RUN cp /root/.ssh/id_rsa.pub /home/seedsynctest/ && \
    chown seedsynctest:seedsynctest /home/seedsynctest/id_rsa.pub
USER seedsynctest
RUN  mkdir -p /home/seedsynctest/.ssh && \
    cat /home/seedsynctest/id_rsa.pub >> /home/seedsynctest/.ssh/authorized_keys
USER root

EXPOSE 22

# src needs to be mounted on /src/
WORKDIR /src/
ENV PYTHONPATH=/src

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["pytest", "-v"]
