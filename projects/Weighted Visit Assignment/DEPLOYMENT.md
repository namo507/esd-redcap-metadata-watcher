# Deploying the Visitboard

Two free pieces: a container running the engine, and a page on GitHub Pages.
Both redeploy on every push to `main`.

## The steps only a human can do

A workflow cannot create accounts or change repository settings. These are
one-time, in order.

### 1. Pick a host for the engine

**Fly.io** keeps a machine running, so the board answers instantly. It usually
asks for a card even on the free allowance.

**Render** needs no card. A free web service sleeps after about 15 minutes of
no traffic and the next request waits 10 to 30 seconds while it wakes. For a
board a coordinator opens a few times a day, that wait lands often.

Both are configured already. Do one of the following.

#### Fly.io

```bash
cd "projects/Weighted Visit Assignment"
fly auth signup                      # or: fly auth login
fly launch --no-deploy --copy-config # claims the app name in fly.toml
fly deploy                           # first deploy, from your machine
fly auth token                       # copy the token it prints
```

Then add the token to the repository:

> Settings, then Secrets and variables, then Actions, then New repository
> secret. Name it exactly `FLY_API_TOKEN` and paste the token as the value.

After that every push to `main` deploys automatically. Without the secret the
workflow still runs the tests and simply does not deploy.

If `esd-visitboard` is taken, change `app` in `fly.toml` and use the new name.

#### Render

1. Sign in at render.com with GitHub.
2. New, then Blueprint, and pick this repository. It reads `render.yaml`.
3. Apply. Render builds the container and gives you a URL.

Render redeploys on every push by itself. No secret, and the GitHub workflow
is only a test gate.

### 2. Point the page at the engine

Take the URL from step 1 and put it in `frontend/config.js`:

```js
window.ESD_CONFIG = { API_BASE: "https://esd-visitboard.fly.dev" };
```

No trailing slash. Commit and push. Until you do this the hosted page cannot
reach the engine and falls back to the frozen demo snapshot, which looks
almost identical and is the single easiest thing to be confused by. If the
hosted board never seems to change, check this file first.

### 3. GitHub Pages

**Change nothing.** Pages is already switched on for this repository, serving
the recruitment dashboard from `main /docs`, and the Visitboard publishes into
`docs/visitboard/` alongside it.

Do not set the Pages source to "GitHub Actions". That would publish one site
and take the other offline.

Your URL is `https://<user>.github.io/<repo>/visitboard/`.

### 4. Keep the audit trail, if you want it

Both hosts start with an empty database on each deploy. That matches the demo
lab, which is regenerated at start anyway. Real assignment history, which is
what the weights are eventually re-checked against, needs a disk:

```bash
fly volumes create esd_data --size 1 --region iad
```

then uncomment the `[mounts]` block at the bottom of `fly.toml` and redeploy.
`ESD_DATA_DIR` already points at `/data`. Render's free plan has no persistent
disk at all, so this needs a paid plan there.

## What runs automatically

| Workflow | Fires on | Does |
|---|---|---|
## Building and checking the image locally

```
docker build -t esd-visitboard:local .
docker run -d --name esd-check -p 8137:8080 esd-visitboard:local
docker inspect --format '{{.State.Health.Status}}' esd-check
```

Verified on 26 August 2026: **733 MB**, healthy about a second after start,
and a real 58-block Outlook print uploads inside it and reads at tier 2 with
nothing left to identify. Python 3.11 in the image, and `esd_scheduler doctor`
runs clean there — every import declared, and the board starting with only the
core packages present.

`python-pptx` and `matplotlib` are deliberately absent: they build the slide
deck, which the container never does. `tesseract`, PyMuPDF, OpenCV, NumPy,
Pillow and pytesseract are all present, so both calendar readers work.

**Nothing sensitive is in the image**, and this is worth re-checking whenever
`.dockerignore` changes, because it is a different list from `.gitignore`:

```
docker run --rm --entrypoint sh esd-visitboard:local -c \
  "find /app -name '*.env' -o -name '*.xlsx' -o -name 'nano-families.json'"
```

That must print nothing. `config/redcap.env` holds the NANO study's API token
and `COPY . .` was copying it before those patterns were added — `data/` was
excluded, so the export stayed out while the credential that fetches it went
in. An image is pushed, pulled, and keeps every layer it was built from, so a
later `rm` does not undo it.

One local gotcha, not a container fault: if a host process already holds the
port you publish on, requests to `127.0.0.1` can reach that instead of the
container while the container's own healthcheck passes from inside. Check with
`lsof -nP -iTCP:<port> -sTCP:LISTEN` before concluding the image is broken.

| `visitboard-backend.yml` | backend, engine, config, Dockerfile changes | runs every test, builds the image, checks the container serves and sends CORS, then deploys to Fly if the token is set |
| `visitboard-frontend.yml` | `frontend/**` changes | builds the offline snapshot, copies the page to `docs/visitboard/`, commits |

The tests gate the deploy on purpose. This engine decides who is sent to a
family visit, and a broken build reaching the lab is worse than no deploy.

## Checking it worked

```bash
curl -s https://<your-app>/api/health
curl -s -D - -o /dev/null https://<your-app>/api/board | grep -i access-control
```

The first should return JSON with `"ok": true`. The second must show
`Access-Control-Allow-Origin: *`; without it the page loads and silently falls
back to the snapshot.

Open the page and check the header clock is moving and the calendars badge
says something recent. A board stuck on old data is the snapshot, not the
engine.

## Local development is unchanged

```bash
make serve
```

Same port, same behaviour, no configuration. `config.js` ships empty, which
means same origin, which is what the local server provides.
