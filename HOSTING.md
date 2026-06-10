# DocExtract — Hosting Guide

This project has **two components** that need to be running:

| Component | File | Purpose |
|-----------|------|---------|
| **FastAPI Backend** | `mineru_server.py` | MinerU + Kimi K2.5 pipeline, REST API |
| **Streamlit UI** | `streamlit_app.py` | Browser-based document viewer |

---

## Option A — Local + ngrok Tunnel (Fastest, Zero Cost)

Your `.env` already has `NGROK_AUTHTOKEN`. This exposes your local server to the internet with a public HTTPS URL.

### Step 1 — Install ngrok

```powershell
# Windows (winget)
winget install ngrok

# OR download directly: https://ngrok.com/download
```

### Step 2 — Authenticate ngrok (one-time)

```powershell
ngrok config add-authtoken 3EnRmOUHjKRJcsBbFiHfM6NpB4A_MYige33H2M6ZHBkFZvGs
```

### Step 3 — Start the FastAPI backend

```powershell
cd f:\POC
python mineru_server.py
# → Listening on http://127.0.0.1:8000
```

### Step 4 — Expose via ngrok (new terminal)

```powershell
ngrok http 8000
# → Forwarding: https://xxxx-xx-xx-xxx-xx.ngrok-free.app → http://127.0.0.1:8000
```

Copy the `https://xxxx...ngrok-free.app` URL.

### Step 5 — Start Streamlit (new terminal)

```powershell
cd f:\POC
streamlit run streamlit_app.py
# → Opens at http://localhost:8501
```

In the Streamlit sidebar, paste your ngrok URL as the **Bridge Endpoint**.

> **Note:** Free ngrok URLs change on each restart. ngrok Pro ($10/mo) gives fixed subdomains.

---

## Option B — Streamlit Community Cloud (UI Only — Free, Permanent)

Hosts `streamlit_app.py` permanently for free. Requires a public GitHub repo.

### Prerequisites
- Public GitHub repo with your code
- [share.streamlit.io](https://share.streamlit.io) account

### Steps

1. Push code to GitHub:
   ```powershell
   git init
   git add streamlit_app.py heuristics.py requirements.txt
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USER/docextract.git
   git push -u origin main
   ```

2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**

3. Select your repo, branch `main`, main file `streamlit_app.py`

4. Under **Advanced settings → Secrets**, add:
   ```toml
   DOCEXTRACT_BRIDGE_URL = "https://your-backend-url.com"
   ```

5. Deploy → Get a permanent `https://your-app.streamlit.app` URL

> The FastAPI backend still needs to run separately (Option A ngrok or Option C Railway).

---

## Option C — Railway.app (Full Stack — Free Tier Available)

Hosts both FastAPI + Streamlit on Railway's cloud. Best for a permanent deployment.

### Prerequisites
- [railway.app](https://railway.app) account (GitHub login)
- `Procfile` in your project root

### Step 1 — Create Procfile

```
# f:\POC\Procfile
web: uvicorn mineru_server:app --host 0.0.0.0 --port $PORT
```

### Step 2 — Create `railway.toml` (optional, sets build config)

```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn mineru_server:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
```

### Step 3 — Deploy

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Select your repo
3. Under **Variables**, add all your `.env` values:
   - `api_key_1` through `api_key_4`
   - `MINERU_API_KEY`
   - `NVIDIA_API_KEY` (optional)
4. Railway gives you a public URL like `https://docextract.up.railway.app`

### Step 4 — Point Streamlit at Railway

In Streamlit sidebar, set Bridge Endpoint to your Railway URL.

> **Free tier**: Railway Hobby plan is $5/mo; Starter plan has $5 free credit monthly.

---

## Option D — Render.com (Free Tier, Sleeps After 15min Idle)

Similar to Railway but has a permanently free tier (service sleeps after 15 minutes of inactivity).

### `render.yaml`

```yaml
services:
  - type: web
    name: docextract-api
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn mineru_server:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: api_key_1
        sync: false   # set in Render dashboard
      - key: MINERU_API_KEY
        sync: false
```

---

## Environment Variables Reference

All hosting platforms need these secrets (from your `.env`):

| Variable | Required | Description |
|----------|----------|-------------|
| `api_key_1` … `api_key_4` | ✅ Yes | Kimi K2.5 HPC-AI API keys |
| `MINERU_API_KEY` | ✅ Yes | MinerU cloud extraction token |
| `NVIDIA_API_KEY` | Optional | NVIDIA NIM fallback (Model Lab) |
| `NGROK_AUTHTOKEN` | Option A only | ngrok tunnel authentication |

---

## Quick Reference: Start Locally

```powershell
# Terminal 1 — Backend
cd f:\POC
python mineru_server.py

# Terminal 2 — Frontend
cd f:\POC
streamlit run streamlit_app.py
```

Then open http://localhost:8501 in your browser.
