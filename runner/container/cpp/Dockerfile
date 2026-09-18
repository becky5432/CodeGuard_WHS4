FROM gcc:15.2.0 AS helper-build

COPY runner/native/task_tracker/codeguard_init.c /tmp/codeguard_init.c
RUN gcc -O2 -Wall -Wextra -Werror \
    -o /tmp/codeguard-init /tmp/codeguard_init.c

FROM gcc:15.2.0

COPY --from=helper-build /tmp/codeguard-init /usr/local/bin/codeguard-init

WORKDIR /workspace
