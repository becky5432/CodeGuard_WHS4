#!/bin/sh

uid=$(id -u)
gid=$(id -g)
cap_inh=$(awk '/^CapInh:/ { print $2 }' /proc/self/status)
cap_prm=$(awk '/^CapPrm:/ { print $2 }' /proc/self/status)
cap_eff=$(awk '/^CapEff:/ { print $2 }' /proc/self/status)
cap_bnd=$(awk '/^CapBnd:/ { print $2 }' /proc/self/status)
cap_amb=$(awk '/^CapAmb:/ { print $2 }' /proc/self/status)
no_new_privileges=$(awk '/^NoNewPrivs:/ { print $2 }' /proc/self/status)
zero_capability=0000000000000000
status=verified

if [ "$uid" != "$CODEGUARD_EXPECTED_UID" ] || \
   [ "$gid" != "$CODEGUARD_EXPECTED_GID" ] || \
   [ "$cap_inh" != "$zero_capability" ] || \
   [ "$cap_prm" != "$zero_capability" ] || \
   [ "$cap_eff" != "$zero_capability" ] || \
   [ "$cap_bnd" != "$zero_capability" ] || \
   [ "$cap_amb" != "$zero_capability" ] || \
   [ "$no_new_privileges" != "1" ]; then
    status=failed
fi

printf '%s status=%s uid=%s gid=%s cap_inh=%s cap_prm=%s cap_eff=%s cap_bnd=%s cap_amb=%s no_new_privileges=%s\n' \
    CODEGUARD_SECURITY_CHECK "$status" "$uid" "$gid" "$cap_inh" "$cap_prm" \
    "$cap_eff" "$cap_bnd" "$cap_amb" "$no_new_privileges" >&2

if [ "$status" != "verified" ]; then
    exit 200
fi

exec "$@"
