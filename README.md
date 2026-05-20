# ScamHound 🐕

AI-powered rug pull detection system for Solana tokens. Aggregates on-chain data from multiple sources, scores risk using AI (Claude or DeepSeek), and delivers results through a real-time dashboard.

## Features

- **Multi-LLM Scoring** — Choose between Anthropic Claude or DeepSeek for AI risk analysis
- **Multi-Platform Feeds** — Monitor tokens from Bags.fm, pump.fun, LetsBonk, Moonshot
- **Real-Time Dashboard** — WebSocket-powered live scoring with search, filter, and pagination
- **Tiered API Access** — Free/Builder/Pro tiers with rate limiting and daily call caps
- **Score History & Re-Scoring** — 24-hour automatic re-scoring with visual score timeline
- **Creator Leaderboard** — Reputation ranking of token creators by average risk score
- **Pro Watchlist** — Custom token watchlists for Pro-tier API key users
- **Batch Scanning** — Scan up to 50 tokens in a single API call (Builder+ tier)
- **Embeddable Widget** — Drop-in risk badge for external sites
- **External Verification Links** — Quick links to Solscan, Birdeye, DexScreener, BubbleMaps
- **Twitter Alerts** — Automated high-risk token alerts (when auto-scan enabled)

## Quick Start

### Prerequisites
- Python 3.10+
- API keys: Helius, Birdeye, and one LLM provider (Anthropic or DeepSeek)

### Setup
```bash
git clone https://github.com/RunTimeAdmin/ScamHound.git
cd ScamHound/scamhound
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows
pip install -r ../requirements.txt
cp .env.example .env
# Edit .env with your API keys
python main.py
```

Dashboard available at http://localhost:8000

## Configuration

All configuration is via environment variables (`.env` file). See `.env.example` for the complete reference.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | AI provider: "anthropic" or "deepseek" |
| `AUTO_SCAN_ENABLED` | `false` | Enable background auto-polling for new tokens |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between auto-scan cycles |
| `PUMPFUN_ENABLED` | `false` | Enable pump.fun token feed (`ENABLE_PUMPFUN` also accepted) |
| `RISK_ALERT_THRESHOLD` | `65` | Minimum risk score to trigger alerts |
| `PORT` | `8000` | Dashboard server port |

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 Platform Router                   │
│  (Bags.fm │ pump.fun │ LetsBonk │ Moonshot)     │
└─────────────────┬───────────────────────────────┘
                  │ new tokens
                  ▼
┌─────────────────────────────────────────────────┐
│              Enrichment Layer                     │
│  Helius (on-chain) │ Birdeye (market) │ BMaps   │
└─────────────────┬───────────────────────────────┘
                  │ enriched data
                  ▼
┌─────────────────────────────────────────────────┐
│            AI Scoring Engine                      │
│     Claude / DeepSeek → Risk Score 0-100         │
└─────────────────┬───────────────────────────────┘
                  │ scored tokens
                  ▼
┌─────────────────────────────────────────────────┐
│           Delivery Layer                         │
│  Dashboard │ API │ WebSocket │ Twitter │ Widget  │
└─────────────────────────────────────────────────┘
```

## API Tiers

| Tier | Daily Calls | Features |
|------|-------------|----------|
| Free | 50 | Manual scan, dashboard access |
| Builder | 500 | + Batch scan, search/filter API |
| Pro | 5000 | + Watchlist, score history, priority support |

## Production Deployment

See `deploy/README.md` for full instructions. Quick summary:

```bash
# On VPS (Ubuntu 22.04)
cd /opt/scamhound
git pull
systemctl restart scamhound
journalctl -u scamhound --no-pager -n 10
```

## FAQ

**Why isn't auto-scanning running?**
Set `AUTO_SCAN_ENABLED=true` in your `.env`. It defaults to false to prevent accidental API credit usage.

**How do I switch to DeepSeek (cheaper)?**
Set `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY=your_key` in `.env`.

**Why did a token's score change?**
ScamHound re-scores tokens every 24 hours. Check the score history chart on the token detail page.

## License

MIT
