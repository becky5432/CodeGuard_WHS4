FROM gcc:15.2.0

WORKDIR /workspace

RUN chown 10001:10001 /workspace

COPY security-preflight.sh /usr/local/bin/codeguard-security-preflight
RUN chmod 0555 /usr/local/bin/codeguard-security-preflight
