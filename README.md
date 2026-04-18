# ScamHound

**Real-time AI-powered rug pull detection for the Bags.fm token launchpad**

Website: [scamhoundcrypto.com](https://scamhoundcrypto.com)  
Twitter: [@ScamHoundCrypto](https://twitter.com/ScamHoundCrypto)

---

## Problem Statement

Every day, hundreds of new tokens launch on Bags.fm. While most are legitimate projects, some are designed to rug pull unsuspecting traders. Currently, there is no native security layer in the Bags ecosystem to warn traders before they invest in potentially fraudulent tokens.

ScamHound fills this gap.

---

## What ScamHound Does

ScamHound monitors every new token launch on Bags.fm in real-time, aggregates data from multiple on-chain and market sources, uses Claude AI to analyze risk factors, and delivers actionable alerts through multiple channels.

**The workflow:**
1. **Monitor** — Polls Bags.fm for new token launches every 60 seconds
2. **Aggregate** — Collects data from Bags.fm API, Helius (on-chain), and Birdeye (market data)
3. **Analyze** — Feeds combined data to Claude AI for risk scoring and plain-English verdicts
4. **Alert** — Displays results on a live dashboard, posts high-risk alerts to Twitter, and serves embeddable widgets

---

## Features

- **Real-time monitoring** of new Bags.fm token launches with configurable poll intervals
- **Multi-source data aggregation** from 3 independent APIs:
  - Bags.fm API (token metadata, holders, creator info)
  - Helius API (on-chain wallet history, transaction analysis)
  - Birdeye API (liquidity data, trading patterns, market metrics)
- **AI-powered risk scoring** via Anthropic Claude with structured JSON responses
- **Live web dashboard** showing last 50 scored tokens with auto-refresh
- **Settings page** for configuring API keys through the browser (no manual file editing required)
- **Twitter bot integration** (@ScamHoundCrypto) for automated high-risk alerts
- **Embeddable widget** for Bags creators to display risk badges on their token pages
- **SQLite database** for persistent storage of all scored tokens
- **REST API endpoints** for third-party integrations

---

## Architecture Overview

```
Polling Loop (APScheduler)
    |
    v
Bags.fm API ──> Data Aggregation ──> Helius API
    |                                    |
    v                                    v
Birdeye API <── Bundle Token Data ────>|
    |
    v
Claude AI Scoring Engine
    |
    v
SQLite Database <──> Dashboard / Alerts / Widget
```

**Data flow:**
1. Monitor polls Bags.fm for new launches
2. For each new token, fetch creator wallet, holder distribution, and trading data
3. Query Helius for wallet age, prior launches, and clustering analysis
4. Query Birdeye for liquidity ratios and wash trading signals
5. Bundle all data and send to Claude AI for risk assessment
6. Store results in SQLite database
7. Serve via FastAPI dashboard and trigger Twitter alerts for high-risk tokens

---

## Prerequisites

- Python 3.10 or higher
- API keys from the following services:

| Service | Purpose | Get Key At |
|---------|---------|------------|
| Bags.fm API | Token launches, holders, creator data | [dev.bags.fm](https://dev.bags.fm) |
| Helius API | On-chain wallet analysis | [helius.dev](https://helius.dev) |
| Birdeye API | Market data, liquidity, trading patterns | [birdeye.so](https://birdeye.so) |
| Anthropic API | AI risk scoring | [console.anthropic.com](https://console.anthropic.com) |
| Twitter API (optional) | Automated alerts | [developer.twitter.com](https://developer.twitter.com) |

---

## Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/scamhound.git
cd scamhound

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

ScamHound supports two methods for configuring API keys:

### Method A: Environment File (Traditional)

Copy the example environment file and fill in your API keys:

```bash
cp .env.example scamhound/.env
```

Edit `.env` with your preferred text editor and add your keys.

### Method B: Web Settings Page (Recommended)

1. Start the application (see below)
2. Navigate to `http://localhost:8000/settings`
3. Enter your API keys through the browser interface
4. Click Save — keys are stored securely in `config.json`

The settings page displays masked key values (showing only the last 4 characters) and never exposes full API keys in the UI.

---

## Running

```bash
cd scamhound
python main.py
```

The application will:
1. Initialize the SQLite database
2. Start the monitoring scheduler (polls every 60 seconds by default)
3. Run an initial scan cycle immediately
4. Start the FastAPI web server on port 8000

Access the dashboard at: [http://localhost:8000](http://localhost:8000)

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard page (HTML) |
| `/token/{mint}` | GET | Token detail page (HTML) |
| `/settings` | GET | API key configuration page (HTML) |
| `/widget/{mint}` | GET | Embeddable risk badge (HTML) |
| `/api/tokens` | GET | JSON array of recent scores (alias for `/api/scores`) |
| `/api/token/{mint}` | GET | JSON data for single token (alias for `/api/score/{mint}`) |
| `/api/scores` | GET | JSON array of last N scored tokens |
| `/api/score/{mint}` | GET | JSON data for specific token mint |
| `/api/stats` | GET | Dashboard statistics (total scanned, high risk count, etc.) |
| `/api/settings` | POST | Save API keys and configuration |
| `/health` | GET | Health check for uptime monitoring |

---

## Deployment

For production deployment on a VPS (Ubuntu, Nginx, SSL), see [VPS.txt](VPS.txt) for the complete step-by-step guide covering:

- Ubuntu 22.04 server setup on Hostinger
- Python virtual environment configuration
- Systemd service for automatic startup and crash recovery
- Nginx reverse proxy configuration
- SSL certificate installation via Certbot
- UFW firewall rules
- Database backup automation

---

## Risk Score Levels

| Level | Score Range | Interpretation |
|-------|-------------|----------------|
| LOW | 0-25 | Token shows healthy signals |
| MODERATE | 26-50 | Some concerns, proceed with caution |
| HIGH | 51-75 | Multiple red flags, elevated risk |
| CRITICAL | 76-100 | Strong indicators of imminent rug pull |

The risk score is calculated by Claude AI based on:
- Top 10 holder concentration
- Creator wallet age and history
- Prior abandoned tokens from the same wallet
- Holder wallet clustering (potential coordinated wallets)
- Liquidity to market cap ratio
- Wash trading indicators
- Large sell pressure detection

---

## Disclaimer

**Not Financial Advice.** ScamHound is provided for educational and informational purposes only. The risk scores and AI-generated verdicts are analytical opinions based on on-chain data patterns and should not be construed as investment advice.

Cryptocurrency trading involves substantial risk of loss. Always conduct your own research (DYOR) before investing. ScamHound does not guarantee the accuracy of its assessments and is not responsible for any financial losses incurred through the use of this tool.

Use at your own risk.

---

## Author

**David Cooper** — CCIE #14019  
Twitter: [@ScamHoundCrypto](https://twitter.com/ScamHoundCrypto)

Built with 25+ years of cybersecurity experience applied to the Solana DeFi ecosystem.
