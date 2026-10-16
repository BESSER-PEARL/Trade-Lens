#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Redeploy the app to the EC2 server behind https://trade-lens.besser-pearl.org (see deploy/README.md).
#
#   KEY=~/Downloads/statec.pem ./deploy/deploy.sh            # code + page, then restart the services
#   KEY=~/Downloads/statec.pem ./deploy/deploy.sh --data     # also the database (data/lux_trade.db, 514 MB, sent gzipped)
#
# The page is built here (the server has no Node): VITE_API_URL=/api and VITE_AGENT_URL=/ws, which nginx
# routes to the backend and to the agents. On Windows (Git Bash) MSYS must not turn "/api" into a path.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-ec2-user@ec2-13-51-72-176.eu-north-1.compute.amazonaws.com}"
KEY="${KEY:?set KEY to the .pem file of the server}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Node on Windows does not read an MSYS path such as /tmp/...: give it the Windows form.
TMPW="$TMP"; if command -v cygpath >/dev/null 2>&1; then TMPW="$(cygpath -m "$TMP")"; fi
# The code: this folder without local secrets, environments, data, builds and caches.
tar czf "$TMP/code.tar.gz" --exclude=./.env --exclude='./.venv*' --exclude=./vendor --exclude=./data   --exclude=./build --exclude=node_modules --exclude=__pycache__ --exclude=.pytest_cache --exclude=./.git .
(cd app/frontend && MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" VITE_API_URL=/api VITE_AGENT_URL=/ws \
  npx vite build --outDir "$TMPW/dist" --emptyOutDir >/dev/null)
if grep -q "Program Files" "$TMP"/dist/assets/*.js; then echo "The build holds a Windows path for /api or /ws." >&2; exit 1; fi
tar czf "$TMP/dist.tar.gz" -C "$TMP" dist
FILES=("$TMP/code.tar.gz" "$TMP/dist.tar.gz")
if [ "${1:-}" = "--data" ]; then
  python -c "import sqlite3; s=sqlite3.connect('data/lux_trade.db'); d=sqlite3.connect(r'$TMPW/lux_trade.db'); s.backup(d)"
  gzip -1 "$TMP/lux_trade.db"; FILES+=("$TMP/lux_trade.db.gz")
fi
scp -q -i "$KEY" "${FILES[@]}" "$HOST:/tmp/"

ssh -i "$KEY" "$HOST" 'set -e
cd /opt/statec
tar xzf /tmp/code.tar.gz                         # code only: .env, .venv*, data/ and www/ stay
rm -rf www.new && mkdir www.new && tar xzf /tmp/dist.tar.gz -C www.new --strip-components=1
rm -rf www.old && { [ -d www ] && mv www www.old || true; } && mv www.new www
if [ -f /tmp/lux_trade.db.gz ]; then sudo systemctl stop statec-agent statec-backend
  gunzip -c /tmp/lux_trade.db.gz > data/lux_trade.db && rm -f /tmp/lux_trade.db.gz; fi
.venv/bin/python -m pip install -q -r requirements.txt
.venv-agent/bin/python -m pip install -q -r requirements-agent.txt
sudo cp deploy/statec-backend.service deploy/statec-agent.service /etc/systemd/system/ && sudo systemctl daemon-reload
# A server set up by an earlier version may still run a third unit (statec-player): stop and remove it.
if [ -f /etc/systemd/system/statec-player.service ]; then sudo systemctl disable --now statec-player || true
  sudo rm -f /etc/systemd/system/statec-player.service && sudo systemctl daemon-reload; fi
sudo systemctl enable -q statec-backend statec-agent
sudo systemctl restart statec-backend statec-agent
sleep 15; systemctl is-active statec-backend statec-agent nginx'
echo "Deployed: https://trade-lens.besser-pearl.org/"
