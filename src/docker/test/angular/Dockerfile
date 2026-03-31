FROM seedsync/build/angular/env AS seedsync_test_angular

RUN apt-get update && \
    apt-get install -y --no-install-recommends chromium chromium-driver fonts-liberation && \
    ln -sf /usr/bin/chromium /usr/bin/google-chrome-stable && \
    ln -sf /usr/bin/chromium /usr/bin/chrome && \
    test -x /usr/bin/chromium && \
    test -x /usr/bin/google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome-stable

WORKDIR /app

CMD ["/app/node_modules/@angular/cli/bin/ng", "test", \
     "--browsers", "ChromeHeadlessCI", \
     "--single-run"]
