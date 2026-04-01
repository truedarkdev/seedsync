FROM alpine:3.11.6

RUN apk add --no-cache curl bash

WORKDIR /
ADD src/docker/wait-for-it.sh /
ADD src/docker/test/e2e/configure/setup_seedsync.sh /
RUN sed -i 's/\r$//' /wait-for-it.sh /setup_seedsync.sh && chmod +x /wait-for-it.sh /setup_seedsync.sh
CMD ["/setup_seedsync.sh"]
