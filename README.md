# noVNC LAN Gateway

noVNC LAN Gateway runs a small web application and a noVNC frontend inside one container. Add VNC servers that are reachable from the container, then open a browser-based VNC session from the target list.

## Quick start

```bash
docker compose up -d --build
```

Open the portal from another machine on the LAN:

```text
http://<docker-host-ip>:6080
```

Add each server with its LAN host or IP, VNC port, and optional VNC password. Port `5900` is the default for display `:0`. If you do not save a password for a target, noVNC will prompt for it in the browser.

## Configuration

The Compose file stores targets in the `novnc-lan-gateway-data` Docker volume at `/data/targets.json`.

Optional environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `VNC_TARGETS_FILE` | `/data/targets.json` in Docker | JSON file used to persist target machines. |
| `NOVNC_ROOT` | `/usr/share/novnc` | Directory containing noVNC static files. |
| `APP_USERNAME` | unset | Enables HTTP Basic auth when set together with `APP_PASSWORD`. |
| `APP_PASSWORD` | unset | Password for HTTP Basic auth. |

## Networking notes

- The container must be able to open TCP connections to every configured VNC server.
- Docker bridge networking can usually reach LAN IP addresses. If your LAN DNS or routing is only available from the host network, use host networking on Linux and remove the `ports` mapping from Compose.
- Saved VNC passwords are stored in the targets JSON file and are sent to the browser as a noVNC URL fragment only when opening a viewer. Use this on trusted LANs and protect the portal with `APP_USERNAME` and `APP_PASSWORD` if more than one person can reach it.
- Without `APP_USERNAME`/`APP_PASSWORD` the portal runs unauthenticated and logs a warning at startup: anyone who can reach it can add targets and have the container open TCP connections to any host it can reach. Set both variables to close this.
- Do not expose this portal directly to the internet.

## Local development

```bash
uv venv
uv pip install -r requirements-dev.txt
. .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 6080
```

Local development needs noVNC static files at `NOVNC_ROOT` for the viewer page. The Docker image installs Debian's `novnc` package automatically.

Run tests:

```bash
pytest
```
