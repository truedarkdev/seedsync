FROM ubuntu:20.04@sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214

RUN useradd --create-home -s /bin/bash remoteuser \
 && echo "remoteuser:remotepass" | chpasswd

USER remoteuser
ADD --chown=remoteuser:remoteuser src/docker/test/e2e/remote/id_rsa.pub /home/remoteuser/user_id_rsa.pub
RUN mkdir -p /home/remoteuser/.ssh \
 && cat /home/remoteuser/user_id_rsa.pub >> /home/remoteuser/.ssh/authorized_keys
USER root

ADD --chown=remoteuser:remoteuser src/docker/test/e2e/remote/files /home/remoteuser/files

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server python3 python3-distutils python3-pip python-is-python3 \
 && pip3 install --no-cache-dir "tblib==1.7.0" \
 && rm -rf /var/lib/apt/lists/* \
 && sed -i '/Port 22/c\Port 1234' /etc/ssh/sshd_config \
 && mkdir /var/run/sshd

EXPOSE 1234
CMD ["/usr/sbin/sshd", "-D"]
