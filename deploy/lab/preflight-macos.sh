#!/bin/sh
# READ-ONLY PRE-FLIGHT for a privately operated macOS Sonoma Docker LAB.
# Does not start containers, install software, read secrets or change host settings.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
cd "$repo_root"

echo "=== HORIZONT LAB / READ-ONLY PRE-FLIGHT ==="
if [ "$(uname -s)" != "Darwin" ]; then
    echo "STOP: this host is not macOS. No changes made."
    exit 2
fi

echo "OS: $(sw_vers -productVersion)"
echo "Machine architecture: $(uname -m)"
printf 'Physical RAM (bytes): '
sysctl -n hw.memsize
echo "Available disk (KiB, current filesystem):"
df -Pk . | awk 'NR==2 {print $4}'
echo "No service names, credentials, .env or Docker volumes will be listed."

if ! command -v docker >/dev/null 2>&1; then
    echo "STOP: Docker CLI not found; no installation will be attempted."
    exit 2
fi
echo "Docker CLI:"
case "${DOCKER_HOST:-}" in
    ""|unix://*) : ;;
    *) echo "STOP: Docker HOST is remote. Choose a local Docker Desktop context."; exit 2 ;;
esac
context_host=$(docker context inspect --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null || true)
case "$context_host" in
    unix://*) echo "Docker endpoint type: local Unix socket" ;;
    *) echo "STOP: Docker context is not a local Unix socket."; exit 2 ;;
esac
docker version --format 'Client {{.Client.Version}}' 2>/dev/null || true
if ! docker info --format 'Engine {{.ServerVersion}} / {{.OSType}}/{{.Architecture}} / CPUs {{.NCPU}}' 2>/dev/null; then
    echo "STOP: Docker engine unavailable."
    exit 2
fi
if ! docker compose version; then
    echo "STOP: Docker Compose unavailable."
    exit 2
fi

port=${HORIZONT_LAB_PORT:-15480}
case "$port" in
    *[!0-9]*|'') echo "STOP: invalid local port"; exit 2 ;;
esac
if [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ]; then
    echo "STOP: port outside nonprivileged range."
    exit 2
fi
if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | grep -q .; then
    echo "STOP: requested localhost port is already in use."
    exit 2
fi
echo "Local port $port: available at inspection time (recheck on start)"

if docker ps --filter label=com.docker.compose.project=horizont-lab --format '{{.ID}}' | grep -q .; then
    echo "STOP: an existing Docker Compose project already uses horizont-lab."
    exit 2
fi
echo "Unique Docker project: no currently running containers detected"

if ! docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml config --quiet; then
    echo "STOP: Docker Compose configuration rejected."
    exit 2
fi
echo "Compose parse: PASS"
echo "PRE-FLIGHT: READ-ONLY CHECKS COMPLETED"
echo "NEXT (manual/private): verify health and backup of other local services,"
echo "Docker Desktop VM memory/storage capacity, and rollback before any 'up'."
