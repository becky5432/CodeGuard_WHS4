FROM gcc:15.2.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends strace \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 codeguard \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin codeguard \
    && mkdir -p /run/codeguard-trace \
    && chown 0:0 /run/codeguard-trace \
    && chmod 700 /run/codeguard-trace

WORKDIR /workspace

RUN chown 10001:10001 /workspace

COPY security-preflight.sh /usr/local/bin/codeguard-security-preflight
RUN chmod 0555 /usr/local/bin/codeguard-security-preflight
