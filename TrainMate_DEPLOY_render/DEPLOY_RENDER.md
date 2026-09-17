# Deploy TrainMate on Render (shared app + logins)

Everyone opens ONE web link and signs in. No installs, no keys on their side.
Your Anthropic key lives only on the server as a secret.

## What you'll set (env vars — secrets)
- `ANTHROPIC_API_KEY`  your key (make a FRESH one; revoke any key you've shared)
- `SECRET_KEY`         a long random string (Render can generate it)
- `TRAINMATE_USERS`    your people, e.g.  `alice:Str0ng1,bob:Str0ng2,carol:Str0ng3`
- `MODEL`              `claude-sonnet-5` (or your current model id)
- `COOKIE_SECURE`      `1`

## Steps
1. **Put this `kit` folder in a GitHub repo** (private is fine).
   - Create a repo on github.com, then upload these files (or use GitHub Desktop / git).
2. **Render** → sign up → **New +** → **Web Service** → connect your repo.
   - Render detects the `Dockerfile` automatically. Instance type: **Starter** (or Free to try).
3. **Add the environment variables** above under the service's **Environment** tab.
   - `TRAINMATE_USERS` is how you create accounts — one `user:password` per person,
     comma-separated. Change it anytime to add/remove people (then redeploy).
4. **Create Web Service.** Render builds and gives you a URL like
   `https://trainmate.onrender.com`.
5. Open that URL → sign in with one of the accounts → use it. Share the link with your team.

### Alternative (no Dockerfile thinking): Blueprint
Render → **New +** → **Blueprint** → pick the repo. It reads `render.yaml`, creates the
service, and prompts you for the secret values.

## Costs & safety
- Small Render instance: a few $/month (Free tier sleeps when idle and wakes on first hit).
- Anthropic usage is per question — set a **monthly spend cap** in the Anthropic Console.
- Only people in `TRAINMATE_USERS` can log in, so strangers can't spend your credits.

## Adding a machine later
Add its data under `data/`, update the code to serve it, commit & push — Render redeploys
automatically and everyone gets it. (Ask me and I'll wire multi-machine when you're there.)

## Local test before deploying (optional)
```powershell
cd kit
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:SECRET_KEY = "any-long-random-text"
$env:TRAINMATE_USERS = "test:test123"
$env:COOKIE_SECURE = "0"        # allow cookie over http locally
python -m uvicorn server:app --port 8000
```
Open http://localhost:8000  → sign in with test / test123.
