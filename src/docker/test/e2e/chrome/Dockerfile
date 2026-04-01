FROM yukinying/chrome-headless-browser-selenium@sha256:a1776e4de701e45d58a4651cc381e1fe1adf32f708f332b877673a0e151dd8e7

USER root
RUN apt-get update && apt-get install -y curl libxi6 unzip \
    && curl -fsSL -o /tmp/chromedriver.zip https://storage.googleapis.com/chrome-for-testing-public/140.0.7327.6/linux64/chromedriver-linux64.zip \
    && unzip -oj /tmp/chromedriver.zip chromedriver-linux64/chromedriver -d /usr/bin \
    && chmod +x /usr/bin/chromedriver \
    && rm -f /tmp/chromedriver.zip \
    && rm -rf /var/lib/apt/lists/*
USER headless
CMD ["standalone", "--port", "4444"]
