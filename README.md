# Live Stock Market Dashboard

Real-time stock market dashboard tracking 50+ core stocks across sectors.

## Features
- Live premarket & market data
- Top gainers & losers
- Sector heatmap (clickable)
- Market insights (auto-generated)
- Self-ping keep-alive for Render free tier

## Run Locally
```bash
pip install -r requirements.txt
python server.py
```
Open http://localhost:5000

## Deploy to Render
1. Push to GitHub
2. Go to https://render.com
3. New > Web Service > Connect your repo
4. Render auto-detects settings from render.yaml
5. Deploy!

## Environment Variable (auto-set by Render)
- `RENDER_EXTERNAL_URL` — used for self-ping keep-alive
- `PORT` — server port
