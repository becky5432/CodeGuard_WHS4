# Execution result notice priority

Run `npm ci` and `npm test` from `frontend` (Node.js compatible with Vite 8).
Tests use Node's built-in test runner and the existing Vite SSR loader to render
the real React result panel; no extra test dependencies are required.

For completed execution results, the presentation helper preserves explicit
internal/security/compile errors and resource limits as the primary notice.
When the API reports SUCCESS or ERROR/RUNTIME_ERROR, a recognized access
violation becomes the primary notice: FILESYSTEM_LIMIT before NETWORK_BLOCKED.
This is a display order, not a ranking of security impact or an inference that
the denied access caused termination. Other violations remain visible.

For example, ERROR/RUNTIME_ERROR/exit 1 plus FILESYSTEM_LIMIT displays one file
denial notice and the file-restriction badge. The original exit 1, program exit
reason RUNTIME_ERROR and failed execution stage remain visible. A handled denial
with exit 0 keeps its successful execution stages while prominently showing
the denied access. Detection does not imply that the program was killed.

The shared helper drives the main page and history detail panel. History list
rows, filters and counts continue to use the stored execution outcome because
the list API does not expose policy_violations. Runner/backend contracts and
saved status/reason/exit/stage data are unchanged.
