# Deploying to a VPS

One Ubuntu server runs everything, alongside any apps already on it:

```
browser ──► nginx (:8080, or :80/:443 with a domain) ──► /          React build (frontend/dist)
                                                     └─► /api/...  gunicorn on 127.0.0.1:8765 (Django)
```

The frontend calls `/api` on its own origin, so there is no CORS setup.
`deploy.sh` installs and updates everything; run it again after every code change.

It is written for servers that already host other apps: it only adds its own nginx site
(`/etc/nginx/sites-available/truck-routing`), never edits or removes other sites, never replaces
the system Node.js (it downloads a private copy into `.node/` if the system one is too old),
and stops before changing anything if a port it needs is taken.

## Requirements

- Ubuntu 22.04 or 24.04, at least 1 GB RAM (the frontend build needs it; see Troubleshooting).
- The site's port reachable from outside: 8080 by default, or 80/443 with a domain.
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
rsync -av --exclude .git --exclude node_modules --exclude .venv --exclude .node --exclude dist --exclude .env \
  ./ user@SERVER_IP:/srv/truck-routing/
```

## 2. Preflight (changes nothing)

```bash
cd /srv/truck-routing
sudo ./deploy/deploy.sh --check
```

Shows the OS, free memory, nginx/Node/Python versions, which ports are taken and by what,
and the enabled nginx sites. Pick other ports if it reports a conflict:
`PUBLIC_PORT=8081` (the site) or `APP_PORT=8766` (internal gunicorn).

## 3. Deploy

**Without a domain** (site on port 8080):

```bash
sudo HERE_API_KEY=your-here-key ./deploy/deploy.sh
```

**With a (sub)domain** (shares ports 80/443 with your other sites, gets HTTPS):

1. At your DNS provider, add an **A record** for e.g. `routes.example.com` pointing to the server's IP.
2. Once it resolves (`getent hosts routes.example.com`), run:

   ```bash
   sudo HERE_API_KEY=your-here-key DOMAIN=routes.example.com EMAIL=you@example.com ./deploy/deploy.sh
   ```

It installs what's missing (Python venv, nginx), creates `backend/.env` with a random secret key,
builds the frontend, starts the `truck-routing` service, adds the nginx site and, with a domain,
gets a free certificate that renews automatically. It ends by printing the URL.

## Updating

```bash
cd /srv/truck-routing
sudo git pull            # or rsync again from your machine
sudo ./deploy/deploy.sh  # pass the same DOMAIN / PUBLIC_PORT / APP_PORT as the first time
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

- **Page doesn't load at all:** open the port (8080, or 80/443 with a domain) in any firewall in front of the server.
- **Remove the app:** `sudo systemctl disable --now truck-routing && sudo rm /etc/systemd/system/truck-routing.service /etc/nginx/sites-enabled/truck-routing /etc/nginx/sites-available/truck-routing && sudo systemctl reload nginx`
- **`Bad Request (400)`:** the address in the browser is not in `DJANGO_ALLOWED_HOSTS` in `backend/.env`.
  Add it and run `sudo systemctl restart truck-routing`.
- **`npm run build` is killed on a 1 GB server:** add swap, then re-run the script:
  `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
- **"The weather service is busy":** Open-Meteo's free per-minute limit; it clears within a minute.
  Forecasts are cached for an hour in `/var/cache/truck-routing`, shared by all workers.
- **Map shows "Map unavailable":** the visitor's browser has WebGL2 turned off (enable hardware acceleration).
