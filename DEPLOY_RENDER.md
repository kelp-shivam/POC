# Deploy DocExtract on Render

## Prerequisites
- GitHub account (push code to repo)
- Render account (https://render.com)
- MinerU API key
- Kimi HPC-AI API keys (api_key_1, api_key_2, etc.)

## Steps

### 1. Prepare GitHub repo
```bash
git add render.yaml requirements.txt requirements-streamlit.txt streamlit_cloud_app.py mineru_server.py heuristics.py
git commit -m "Prepare for Render deployment"
git push origin main
```

### 2. Create Render.yaml Blueprint
Already created: `render.yaml` in repo root defines both services

### 3. Deploy on Render Dashboard
1. Go to https://render.com
2. Click **"New +"** → **"Blueprint"**
3. Select your GitHub repo
4. Select branch (main)
5. Review services:
   - `docextract-backend` (FastAPI)
   - `docextract-frontend` (Streamlit)
6. Click **"Deploy Blueprint"**

### 4. Set Environment Variables
After deployment, set these in Render dashboard:

**Backend service (docextract-backend):**
- `MINERU_API_KEY`: your-mineru-token
- `api_key_1`: your-kimi-key-1
- `api_key_2`: your-kimi-key-2
- `api_key_3`: your-kimi-key-3
- `api_key_4`: your-kimi-key-4

**Frontend service (docextract-frontend):**
- `DOCEXTRACT_BACKEND_URL`: auto-filled (points to backend service)

### 5. Wait for deployment
- Backend deploys first (~5 min)
- Frontend auto-connects to backend via env var
- Monitor logs in Render dashboard

### 6. Access
- **Backend:** https://docextract-backend-xxxxx.onrender.com/health
- **Frontend:** https://docextract-frontend-xxxxx.onrender.com

## Troubleshooting

**Frontend can't reach backend:**
- Check `DOCEXTRACT_BACKEND_URL` env var
- Verify backend is healthy (check logs)

**Backend timeouts:**
- Free plan has 15-min timeout limit
- Upgrade to paid plan for longer runs

**Missing API keys:**
- Add to Render environment (not in code)
- Restart services after setting

## Local development (before pushing)

Test locally first:
```bash
# Terminal 1
PORT=8000 python3 -m mineru_server

# Terminal 2
streamlit run streamlit_cloud_app.py
```

Visit http://localhost:8501

## Scaling (after initial deployment)

If you hit limits:
- Upgrade Render plan (free → standard)
- Add more API keys for Kimi rate limiting
- Use Redis cache for image hashes (optional)
