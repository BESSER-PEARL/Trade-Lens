# Deployment: https://trade-lens.besser-pearl.org

One small EC2 server (Amazon Linux 2023, 1 GB RAM + 2 GB swap, eu-north-1, 13.51.72.176). The name
`trade-lens.besser-pearl.org` is an A record on Cloudflare, DNS only. The certificate is Let's Encrypt
(`certbot --nginx`, renewed by `certbot-renew.timer`); it also covers `statec.besser-pearl.org`, the first name.

| Part | How it runs | Port |
|---|---|---|
| Page | static build in `/opt/statec/www`, served by nginx | 443 (80 redirects) |
| API | `statec-backend.service`: uvicorn, `DATABASE_URL=/opt/statec/data/lux_trade.db`, `VIEW_MAX_AGENT_PANELS=8` (the page holds up to 8 agent charts) | 127.0.0.1:8000, nginx `/api/` |
| Agents | `statec-agent.service`: BAF + OpenAI (key in `/opt/statec/.env`, mode 600); registers the four agents on the backend; every question is answered live | 127.0.0.1:8766, nginx `/ws` |

Files here are copies of what is on the server: `nginx-statec.conf` (`/etc/nginx/conf.d/statec.conf`, with the
lines Certbot added) and the two units (`/etc/systemd/system/`). nginx also needs
`server_names_hash_bucket_size 128;` (`/etc/nginx/conf.d/00-server-names.conf`) for the long EC2 host name.

**What nginx lets through.** The page uses only the analysis and page endpoints, so nginx closes the rest to the
public: the raw tables (`/api/observation`, `/api/product`, `/api/country`, `/api/statistics`) answer 403, except a
GET on `/api/product/search/` (the search box); `/api/docs` and `/api/openapi.json` answer 404;
`/api/agents/register` (used on the server only) answers 403. `/api` is limited to 20 requests a second per visitor
(burst 100), `/ws` to 3 connections per visitor; `server_tokens off`.

## Redeploy

```bash
KEY=~/Downloads/statec.pem ./deploy/deploy.sh          # code and page, then the units and a restart
KEY=~/Downloads/statec.pem ./deploy/deploy.sh --data   # also the database (data/lux_trade.db, 514 MB, sent gzipped)
```

The script builds the page locally (Node.js needed; the server has none) with `VITE_API_URL=/api` and
`VITE_AGENT_URL=/ws`, packs this folder without `.env`, the virtual environments, `vendor/` and `data/`, copies both
to the server, installs the requirements, installs the two units and restarts them. A server set up with the
hackathon's demo player (`statec-player.service`) has that unit stopped and removed. The script does **not** install
`nginx-statec.conf`: after a change to it, copy it to `/etc/nginx/conf.d/statec.conf`, then
`sudo nginx -t && sudo systemctl reload nginx`.

**On the server:** `ssh -i statec.pem ec2-user@ec2-13-51-72-176.eu-north-1.compute.amazonaws.com`, then
`sudo journalctl -u statec-agent -f` (or `statec-backend`), `sudo systemctl restart statec-agent`.

**One page for everyone.** The backend keeps one shared page: a reader picked in one tab changes it for every open
tab. A page per visitor is the next step.

## First setup

Swap; `dnf install python3.11 python3.11-pip python3.11-devel gcc gcc-c++ nginx git certbot python3-certbot-nginx`;
`PYTHON=python3.11 bash scripts/setup.sh`; a `.venv-agent` with `requirements-agent.txt`; `/opt/statec/.env` with
the OpenAI key (mode 600); the units and nginx file above; then `certbot --nginx -d trade-lens.besser-pearl.org`.
