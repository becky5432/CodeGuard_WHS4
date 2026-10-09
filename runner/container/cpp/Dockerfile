FROM gcc:15.2.0 AS helper-build

COPY runner/native/task_tracker/codeguard_init.c /tmp/native/task_tracker/codeguard_init.c
COPY runner/native/filesystem/ /tmp/native/filesystem/
RUN gcc -O2 -Wall -Wextra -Werror \
    -o /tmp/codeguard-init /tmp/native/task_tracker/codeguard_init.c \
    /tmp/native/filesystem/landlock_policy.c /tmp/native/filesystem/policy_reader.c \
    /tmp/native/filesystem/startup_status.c \
    && gcc -O2 -Wall -Wextra -Werror \
    -o /tmp/codeguard-workspace-prepare /tmp/native/filesystem/workspace_prepare.c

FROM gcc:15.2.0

COPY --from=helper-build /tmp/codeguard-init /usr/local/bin/codeguard-init
COPY --from=helper-build /tmp/codeguard-workspace-prepare /usr/local/bin/codeguard-workspace-prepare

RUN apt-get update \
    && apt-get install -y --no-install-recommends strace python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 codeguard \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin codeguard \
    && mkdir -p /run/codeguard-trace \
    && chown 0:0 /run/codeguard-trace \
    && chmod 711 /run/codeguard-trace \
    && mkdir -p /workspace \
    && chown 0:0 /workspace \
    && chmod 0755 /workspace \
    && chmod 0555 /usr/local/bin/codeguard-init /usr/local/bin/codeguard-workspace-prepare

WORKDIR /workspace

COPY runner/container/cpp/build-filesystem-manifest.py /usr/local/libexec/codeguard/build-filesystem-manifest.py
RUN mkdir -p /usr/local/share/codeguard \
    && python3 /usr/local/libexec/codeguard/build-filesystem-manifest.py \
    /usr/local/share/codeguard/filesystem-runtime.json \
    && chown 0:0 /usr/local/share/codeguard/filesystem-runtime.json \
    && chmod 0444 /usr/local/share/codeguard/filesystem-runtime.json

COPY runner/container/cpp/security-preflight.sh /usr/local/bin/codeguard-security-preflight
RUN sed -i 's/\r$//' /usr/local/bin/codeguard-security-preflight \
    && chmod 0555 /usr/local/bin/codeguard-security-preflight
