FROM seedsync/build/angular/env AS seedsync_test_angular

# The legacy Angular test image still uses Debian stretch, so install the
# browser from the archived snapshot repos instead of fetching a newer Chrome
# package that no longer matches stretch's libc baseline.
RUN sed -i \
        -e 's|^# deb http://snapshot.debian.org/archive/debian/20200514T145000Z stretch main|deb [check-valid-until=no trusted=yes] http://snapshot.debian.org/archive/debian/20200514T145000Z stretch main|' \
        -e 's|^deb http://deb.debian.org/debian stretch main|# deb http://deb.debian.org/debian stretch main|' \
        -e 's|^# deb http://snapshot.debian.org/archive/debian-security/20200514T145000Z stretch/updates main|deb [check-valid-until=no trusted=yes] http://snapshot.debian.org/archive/debian-security/20200514T145000Z stretch/updates main|' \
        -e 's|^deb http://security.debian.org/debian-security stretch/updates main|# deb http://security.debian.org/debian-security stretch/updates main|' \
        -e 's|^# deb http://snapshot.debian.org/archive/debian/20200514T145000Z stretch-updates main|deb [check-valid-until=no trusted=yes] http://snapshot.debian.org/archive/debian/20200514T145000Z stretch-updates main|' \
        -e 's|^deb http://deb.debian.org/debian stretch-updates main|# deb http://deb.debian.org/debian stretch-updates main|' \
        /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends chromium chromium-driver fonts-liberation && \
    ln -sf /usr/bin/chromium /usr/bin/google-chrome-stable && \
    ln -sf /usr/bin/chromium /usr/bin/chrome && \
    test -x /usr/bin/chromium && \
    test -x /usr/bin/google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome-stable

COPY \
    src/angular/tsconfig.json \
    src/angular/tslint.json \
    src/angular/karma.conf.js \
    src/angular/.angular-cli.json \
    /app/

RUN ls -l /app/

# ng src needs to be mounted on /app/src
WORKDIR /app/src

CMD ["/app/node_modules/@angular/cli/bin/ng", "test", \
     "--browsers", "ChromeHeadlessCI", \
     "--single-run"]
