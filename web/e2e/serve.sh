#!/usr/bin/env bash
# One throwaway Postgres + the real ASGI app serving the built SPA. The E2E run
# needs a *fresh* database: the first account to register becomes the tenant's
# owner, and "fresh install" is exactly what the first scenario asserts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${E2E_PORT:-8100}"
NAME="omg-e2e-$$"

cleanup() {
  # `-v`: a `docker rm` without it leaves the anonymous data dir behind, and
  # fifty aborted runs leave fifty Postgres volumes nobody looks at again.
  docker rm -fv "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# A run killed with SIGKILL never reaches the trap, so the container it started
# holds its volume forever. `workers: 1` and `reuseExistingServer: false` mean a
# second run cannot be alive on this host, so sweeping them all is safe.
# ponytail: sweep-all assumes one e2e at a time; give the name a lock file if
# that ever stops being true.
docker ps -aq --filter "name=omg-e2e-" | xargs -r docker rm -fv >/dev/null 2>&1 || true

# A random host port: a fixed one collides with a container left behind by an
# aborted run, and "port already allocated" is not a test failure anyone should
# have to diagnose.
docker run --rm -d --name "$NAME" \
  -e POSTGRES_USER=omg -e POSTGRES_PASSWORD=omg -e POSTGRES_DB=omg \
  -p 127.0.0.1::5432 postgres:17-alpine >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$NAME" pg_isready -U omg -d omg >/dev/null 2>&1; then break; fi
  sleep 0.5
done

PG_PORT="$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"
[ -n "$PG_PORT" ] || { echo "postgres did not publish a port" >&2; exit 1; }

# The SPA must be built: the app serves web/dist, and a stale dist would test
# the previous UI.
(cd "$ROOT/web" && npm run build >/dev/null)

export OMG_DATABASE_URL="postgresql://omg:omg@127.0.0.1:${PG_PORT}/omg"
export OMG_SESSION_SECRET="e2e-session-secret-at-least-32-bytes!!"
export OMG_BASE_URL="http://127.0.0.1:${PORT}"
export OMG_WEB_DIST="$ROOT/web/dist"

# Not `exec`: the trap must still own the container's lifetime when the server
# stops, otherwise every aborted run leaves a Postgres behind.
# Local runs use the project venv; CI installs the package into the job's Python.
if [ -x "$ROOT/backend/.venv/bin/omg" ]; then
  OMG="$ROOT/backend/.venv/bin/omg"
else
  OMG="$(command -v omg || echo "python -m gateway.cli")"
fi

$OMG serve --host 127.0.0.1 --port "$PORT"
