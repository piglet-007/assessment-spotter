# Deploying to a VPS

One Ubuntu server runs everything:

```
browser ──► nginx :80/:443 ──► /            React build (frontend/dist)
                           └─► /api/...    gunicorn :8000 (Django)
```

The frontend calls `/api` on its own origin, so there is no CORS setup.
`deploy.sh` installs and updates everything; run it again after every code change.

## Requirements

- Ubuntu 22.04 or 24.04, at least 1 GB RAM (the frontend build needs it; see Troubleshooting).
- Ports 80 and 443 open in the provider's firewall (on GCP/AWS this is a separate "firewall rule" / "security group").
- A HERE API key.
- Optional: a domain name, for HTTPS.

## 1. Put the code on the server

The service runs as a dedicated user that cannot read home directories, so use `/srv/truck-routing`.

**From GitHub:**

```bash
sudo git clone https://github.com/<you>/<repo>.git /srv/truck-routing
```

**Or copy from your machine** (run locally, in the project folder):

```bash
ssh user@SERVER_IP 'sudo mkdir -p /srv/truck-routing && sudo chown $USER /srv/truck-routing'
rsync -av --exclude .git --exclude node_modules --exclude .venv --exclude dist --exclude .env \
  ./ user@SERVER_IP:/srv/truck-routing/
```

## 2. Deploy

```bash
cd /srv/truck-routing
sudo HERE_API_KEY=your-here-key ./deploy/deploy.sh
```

It installs Python, Node 22 and nginx, creates `backend/.env` with a random secret key,
builds the frontend, starts the `truck-routing` service and configures nginx.
When it finishes it prints the URL, e.g. `http://203.0.113.10`.

## 3. Optional: domain and HTTPS

1. At your DNS provider, add an **A record** for the domain pointing to the server's IP.
2. Once it resolves (`ping routes.example.com`), run:

   ```bash
   sudo nano backend/.env    # add the domain to DJANGO_ALLOWED_HOSTS, comma separated
   sudo DOMAIN=routes.example.com EMAIL=you@example.com ./deploy/deploy.sh
   ```

certbot gets a free certificate, redirects HTTP to HTTPS and renews automatically.

## Updating

```bash
cd /srv/truck-routing
sudo git pull            # or rsync again from your machine
sudo ./deploy/deploy.sh  # add DOMAIN=... if you use one
```

## Operating

| Task | Command |
| --- | --- |
| API logs | `sudo journalctl -u truck-routing -f` |
| Restart API | `sudo systemctl restart truck-routing` |
| nginx errors | `sudo tail -f /var/log/nginx/error.log` |
| Settings / API key | `sudo nano /srv/truck-routing/backend/.env`, then restart the API |
| Clear cached forecasts | `sudo rm -rf /var/cache/truck-routing/*` |

## Troubleshooting

- **Page doesn't load at all:** open ports 80/443 in the cloud provider's firewall.
- **`Bad Request (400)`:** the address in the browser is not in `DJANGO_ALLOWED_HOSTS` in `backend/.env`.
  Add it and run `sudo systemctl restart truck-routing`.
- **`npm run build` is killed on a 1 GB server:** add swap, then re-run the script:
  `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
- **"The weather service is busy":** Open-Meteo's free per-minute limit; it clears within a minute.
  Forecasts are cached for an hour in `/var/cache/truck-routing`, shared by all workers.
- **Map shows "Map unavailable":** the visitor's browser has WebGL2 turned off (enable hardware acceleration).
