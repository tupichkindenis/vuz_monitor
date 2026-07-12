#!/usr/bin/env bash
# launchd/cron entrypoint: the hourly run, then publish the dashboard.
# Publish is isolated — its failure never changes the run's exit status (data +
# notify are already done by then, and docs/index.html is written regardless).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

"$ROOT/.venv/bin/python" -m vuz_monitor run
RUN_RC=$?

"$ROOT/deploy/publish-dashboard.sh" || true   # sequential, NOT `&&`; never fatal

exit $RUN_RC
