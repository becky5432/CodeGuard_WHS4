FROM gcc:15.2.0 AS helper-build

COPY runner/native/task_tracker/codeguard_init.c /tmp/codeguard_init.c
RUN gcc -O2 -Wall -Wextra -Werror \
    -o /tmp/codeguard-init /tmp/codeguard_init.c

FROM gcc:15.2.0

COPY --from=helper-build /tmp/codeguard-init /usr/local/bin/codeguard-init

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

COPY runner/container/cpp/security-preflight.sh /usr/local/bin/codeguard-security-preflight
RUN chmod 0555 /usr/local/bin/codeguard-security-preflight
