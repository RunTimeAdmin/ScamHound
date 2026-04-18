# ScamHound: AI-Powered Rug Pull Early Warning System
## Complete Build Specification — Bags.fm Hackathon 2026

**Project Token:** Launch on Bags.fm before or during submission  
**Hackathon:** The Bags Hackathon — DoraHacks (submissions close June 1, 2026)  
**Prize pool:** $1M, top 100 projects ($10K–$100K each)  
**Twitter brand:** @ScamHoundCrypto  
**Author:** David Cooper, CCIE #14019

---

## 1. Project Overview

ScamHound is a real-time AI-powered rug pull early warning system for the Bags.fm token launchpad ecosystem. It monitors every newly launched token on Bags.fm, aggregates on-chain signals from Helius and Birdeye, scores each token using Claude AI, and delivers risk alerts via:

- A live web dashboard (public-facing, shareable)
- @ScamHoundCrypto Twitter/X bot (automated alerts for high-risk tokens)
- A Bags App Store embeddable widget (risk badge any Bags creator can embed)

The scoring engine analyzes wallet clustering, liquidity manipulation, dev wallet concentration, creator history, and trading pattern anomalies. Each token receives a 0–100 risk score with a plain-English AI-generated explanation.

**Why this wins:** Every other hackathon submission builds trading dashboards. Nobody else in this hackathon has 25 years of cybersecurity experience and an existing scam detection brand. This is the only security-first entry.

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+ |
| Scheduler | APScheduler (polls every 60 seconds) |
| Web framework | FastAPI + Uvicorn |
| Frontend | Vanilla HTML/CSS/JS (no framework, keep it simple) |
| AI scoring | Anthropic Claude API (claude-sonnet-4-6) |
| Primary data | Bags.fm REST API |
| On-chain analysis | Helius API |
| Market data | Birdeye API |
| Twitter alerts | Tweepy (Twitter API v2) |
| Database | SQLite (via Python sqlite3, no ORM needed) |
| Config | python-dotenv |
| Deployment | Any VPS (DigitalOcean, Render, Railway) |

---

## 3. API Keys Required (Gather Before Building)

| Service | Where to get it | Cost |
|---|---|---|
| Bags.fm API | dev.bags.fm | Free |
| Helius API | helius.dev | Free tier available, hackathon credits available |
| Birdeye API | birdeye.so | Free tier available |
| Anthropic API | console.anthropic.com | Pay per use |
| Twitter/X API | developer.twitter.com | Free Basic tier |

---

## 4. Complete Project File Structure

```
scamhound/
├── .env                          # All API keys and config (never commit this)
├── .env.example                  # Example env file for documentation
├── requirements.txt              # Python dependencies
├── README.md                     # Project description for hackathon judges
├── main.py                       # Entry point — starts monitor + web server
│
├── clients/
│   ├── __init__.py
│   ├── bags_client.py            # All Bags.fm API calls
│   ├── helius_client.py          # Helius on-chain analysis
│   └── birdeye_client.py         # Birdeye market data
│
├── engine/
│   ├── __init__.py
│   ├── scorer.py                 # Bundles data + calls Claude API
│   ├── monitor.py                # Poll loop — finds new tokens, runs scoring
│   └── database.py               # SQLite read/write for token scores
│
├── alerts/
│   ├── __init__.py
│   └── twitter_bot.py            # Posts formatted alerts to @ScamHoundCrypto
│
├── dashboard/
│   ├── app.py                    # FastAPI routes
│   └── templates/
│       ├── index.html            # Main live dashboard
│       ├── token_detail.html     # Per-token detail page
│       └── widget.html           # Embeddable badge widget
│
└── static/
    ├── style.css                 # Dashboard styles
    └── scamhound_logo.png        # Brand asset
```

---

## 5. Environment Configuration

### .env (never commit — add to .gitignore)
```
# Bags.fm
BAGS_API_KEY=your_key_from_dev.bags.fm

# Helius
HELIUS_API_KEY=your_key_from_helius.dev

# Birdeye
BIRDEYE_API_KEY=your_key_from_birdeye.so

# Anthropic
ANTHROPIC_API_KEY=your_anthropic_key

# Twitter/X (@ScamHoundCrypto)
TWITTER_BEARER_TOKEN=
TWITTER_API_KEY=
TWITTER_API_SECRET=
TWITTER_ACCESS_TOKEN=
TWITTER_ACCESS_SECRET=

# App config
RISK_ALERT_THRESHOLD=65         # Score at or above this triggers a tweet
POLL_INTERVAL_SECONDS=60        # How often to check for new Bags launches
PORT=8000                       # Dashboard web server port
DB_PATH=scamhound.db            # SQLite database path
```

### .env.example (commit this)
Same as above with empty values — documents what's needed for judges.

---

## 6. Dependencies

### requirements.txt
```
requests==2.31.0
anthropic==0.25.0
tweepy==4.14.0
fastapi==0.110.0
uvicorn==0.29.0
python-dotenv==1.0.0
apscheduler==3.10.4
jinja2==3.1.3
```

Install with:
```bash
pip install -r requirements.txt
```

---

## 7. Module Specifications

### 7.1 clients/bags_client.py

**Purpose:** All interactions with the Bags.fm REST API.

**Base URL:** `https://public-api-v2.bags.fm/api/v1`  
**Auth header:** `x-api-key: {BAGS_API_KEY}`  
**Rate limit:** 1,000 requests/hour

**Functions to implement:**

```
get_recent_launches(limit=20) -> list
```
- Endpoint: GET `/token-launch/recent?limit={limit}`
- Returns list of recently launched tokens
- Each item includes: tokenMint, name, symbol, createdAt, description
- Used by monitor.py to find new tokens since last poll

```
get_token_creators(token_mint: str) -> dict
```
- Endpoint: GET `/token-launch/creators?tokenMint={token_mint}`
- Returns primary creator info:
  - wallet (string) — Solana wallet address
  - username (string) — Bags/Twitter username
  - royalty_pct (float) — royalty percentage (royaltyBps / 100)
- The creator wallet is the #1 input for scam detection

```
get_top_holders(token_mint: str) -> dict
```
- Endpoint: GET `/analytics/holders?tokenMint={token_mint}`
- Returns top 100 holders list
- Calculate and return:
  - top_holders: list of {wallet, percentage} for top 10
  - top_10_concentration_pct: float — sum of top 10 holder percentages
  - total_holder_count: int

```
get_lifetime_fees(token_mint: str) -> dict
```
- Endpoint: GET `/token-launch/lifetime-fees?tokenMint={token_mint}`
- Returns totalFeesSol (float)
- Used to gauge trading velocity

```
get_token_claim_stats(token_mint: str) -> dict
```
- Endpoint: GET `/analytics/token-claim-stats?tokenMint={token_mint}`
- Returns claimable amounts and claim history
- Rapid fee claiming is a rug pull signal

```
get_full_token_profile(token_mint: str) -> dict
```
- Calls all above functions and bundles into one dict
- This is the primary payload passed to scorer.py
- Shape:
```python
{
    "token_mint": str,
    "name": str,
    "symbol": str,
    "created_at": str,
    "creator": {
        "wallet": str,
        "username": str,
        "royalty_pct": float
    },
    "holders": {
        "top_holders": list,
        "top_10_concentration_pct": float,
        "total_holder_count": int
    },
    "lifetime_fees_sol": float,
    "claim_stats": dict,
    "source": "bags_api"
}
```

**Error handling:** All functions should catch `requests.exceptions.RequestException`, log the error, and return None. monitor.py skips tokens where profile is None.

---

### 7.2 clients/helius_client.py

**Purpose:** Deep on-chain analysis using the creator wallet address obtained from Bags API.

**Base URL:** `https://api.helius.xyz/v0`  
**Auth:** `?api-key={HELIUS_API_KEY}` as query param

**Functions to implement:**

```
get_wallet_transaction_history(wallet_address: str, limit=50) -> list
```
- Endpoint: GET `/addresses/{wallet_address}/transactions?api-key={key}&limit={limit}`
- Returns recent transactions for the creator wallet
- Used to check if creator has a history of creating and abandoning tokens

```
get_previous_token_launches(wallet_address: str) -> dict
```
- Uses transaction history to identify prior token launches by same wallet
- Returns:
  - prior_launch_count: int
  - abandoned_tokens: list of token mints where liquidity was removed
  - days_since_last_launch: int (None if first launch)
- HIGH RISK if: prior_launch_count > 2 AND any abandoned_tokens exist

```
check_wallet_clustering(holder_wallets: list) -> dict
```
- Takes the top holder wallet list from Bags API
- Calls get_wallet_transaction_history for each wallet
- Checks if multiple holder wallets transacted with the same source wallet (funded from same place)
- Returns:
  - clustered_wallets: int — number of wallets that appear connected
  - clustering_score: float 0.0–1.0 (1.0 = all top holders are connected)
- HIGH RISK if: clustering_score > 0.4

```
get_wallet_age_days(wallet_address: str) -> int
```
- Gets the date of first transaction for the wallet
- Returns age in days
- HIGH RISK if creator wallet is less than 7 days old

---

### 7.3 clients/birdeye_client.py

**Purpose:** Market-side analysis — liquidity, trading patterns, price action.

**Base URL:** `https://public-api.birdeye.so`  
**Auth header:** `X-API-KEY: {BIRDEYE_API_KEY}`

**Functions to implement:**

```
get_token_overview(token_mint: str) -> dict
```
- Endpoint: GET `/defi/token_overview?address={token_mint}`
- Returns: price, marketcap, liquidity, volume24h, priceChange24h

```
get_liquidity_data(token_mint: str) -> dict
```
- Endpoint: GET `/defi/liquidity?address={token_mint}`
- Returns liquidity pool data
- Calculate and return:
  - liquidity_usd: float
  - liquidity_to_mcap_ratio: float (low ratio = danger)
  - pool_count: int

```
get_trade_history(token_mint: str, limit=100) -> dict
```
- Endpoint: GET `/defi/txs/token?address={token_mint}&limit={limit}`
- Analyzes trading patterns for manipulation signals
- Returns:
  - wash_trading_score: float 0.0–1.0
    (same wallets repeatedly buying/selling = wash trading)
  - large_sell_pressure: bool (large sells from top holders in last hour)
  - avg_trade_size_usd: float
  - unique_trader_count: int

```
get_price_history(token_mint: str, time_from: int, time_to: int) -> list
```
- Endpoint: GET `/defi/history_price?address={token_mint}&time_from={time_from}&time_to={time_to}&type=15m`
- Returns OHLCV candles
- Used to detect pump-and-dump price pattern

---

### 7.4 engine/database.py

**Purpose:** Lightweight SQLite persistence so we don't re-score the same token repeatedly and can serve the dashboard.

**Database file:** scamhound.db (path from .env)

**Tables:**

```sql
CREATE TABLE IF NOT EXISTS scored_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT UNIQUE NOT NULL,
    name TEXT,
    symbol TEXT,
    risk_score INTEGER,           -- 0-100
    risk_level TEXT,              -- LOW / MEDIUM / HIGH / CRITICAL
    ai_verdict TEXT,              -- Claude's plain English explanation (2-3 sentences)
    top_10_concentration REAL,
    creator_wallet TEXT,
    creator_username TEXT,
    prior_launches INTEGER,
    wallet_age_days INTEGER,
    clustering_score REAL,
    liquidity_usd REAL,
    lifetime_fees_sol REAL,
    tweet_sent BOOLEAN DEFAULT FALSE,
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT
);
```

**Functions to implement:**

```
init_db() -> None
```
Creates the table if it doesn't exist. Called once at startup.

```
token_already_scored(token_mint: str) -> bool
```
Returns True if token_mint exists in scored_tokens. Used by monitor.py to skip already-processed tokens.

```
save_score(score_data: dict) -> None
```
Inserts a new row. score_data shape matches the scored_tokens columns.

```
get_recent_scores(limit=50) -> list
```
Returns last N scored tokens ordered by scored_at DESC. Used by dashboard.

```
get_token_score(token_mint: str) -> dict | None
```
Returns single token row by mint address. Used by token detail page and widget endpoint.

```
get_high_risk_unnotified() -> list
```
Returns tokens where risk_score >= RISK_ALERT_THRESHOLD and tweet_sent = FALSE. Used by twitter_bot.py.

```
mark_tweet_sent(token_mint: str) -> None
```
Sets tweet_sent = TRUE for given token_mint.

---

### 7.5 engine/scorer.py

**Purpose:** The brain of the system. Takes the bundled token data from all three APIs and asks Claude to score it.

**Model to use:** claude-sonnet-4-6

**Functions to implement:**

```
calculate_risk_score(token_profile: dict) -> dict
```

This function:
1. Receives the full token profile dict (Bags + Helius + Birdeye data combined)
2. Builds a structured prompt for Claude
3. Calls Claude API with the prompt
4. Parses Claude's JSON response
5. Returns a score dict

**Claude prompt structure:**

```python
SYSTEM_PROMPT = """
You are ScamHound, an expert crypto security analyst specializing in rug pull detection 
on the Solana blockchain. You analyze token data and return a structured risk assessment.

You MUST respond with valid JSON only. No preamble, no explanation outside the JSON.

JSON format:
{
    "risk_score": <integer 0-100>,
    "risk_level": "<LOW|MEDIUM|HIGH|CRITICAL>",
    "verdict": "<2-3 sentence plain English explanation of the key risks or why the token looks clean>",
    "top_risk_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
    "top_safe_signals": ["<signal 1>", "<signal 2>"]
}

Risk score guide:
0-30 = LOW (token shows healthy signals)
31-60 = MEDIUM (some concerns, proceed with caution)
61-80 = HIGH (multiple red flags, high risk of rug pull)
81-100 = CRITICAL (strong indicators of imminent rug pull)
"""
```

**User prompt — inject the token data:**

```python
USER_PROMPT = f"""
Analyze this Solana token launched on Bags.fm for rug pull risk:

TOKEN DETAILS:
- Name: {name}
- Symbol: {symbol}
- Token Mint: {token_mint}
- Launched: {created_at}

BAGS.FM DATA:
- Creator username: {creator_username}
- Creator wallet: {creator_wallet}
- Creator royalty: {royalty_pct}%
- Top 10 holder concentration: {top_10_concentration_pct}%
- Total holders: {total_holder_count}
- Lifetime trading fees collected: {lifetime_fees_sol} SOL
- Top holders: {json.dumps(top_holders)}

ON-CHAIN CREATOR HISTORY (Helius):
- Creator wallet age: {wallet_age_days} days
- Prior token launches from this wallet: {prior_launch_count}
- Previously abandoned tokens (liquidity removed): {abandoned_tokens}
- Days since last launch: {days_since_last_launch}

HOLDER CLUSTERING ANALYSIS (Helius):
- Clustered wallets detected: {clustered_wallets}
- Clustering score (0.0-1.0): {clustering_score}

MARKET DATA (Birdeye):
- Liquidity (USD): ${liquidity_usd}
- Liquidity to market cap ratio: {liquidity_to_mcap_ratio}
- Unique traders (24h): {unique_trader_count}
- Wash trading score (0.0-1.0): {wash_trading_score}
- Large sell pressure detected: {large_sell_pressure}

Respond with JSON only.
"""
```

**Return shape from calculate_risk_score():**

```python
{
    "token_mint": str,
    "name": str,
    "symbol": str,
    "risk_score": int,          # 0-100
    "risk_level": str,          # LOW/MEDIUM/HIGH/CRITICAL
    "verdict": str,             # Claude's 2-3 sentence explanation
    "top_risk_factors": list,
    "top_safe_signals": list,
    "creator_wallet": str,
    "creator_username": str,
    "top_10_concentration": float,
    "prior_launches": int,
    "wallet_age_days": int,
    "clustering_score": float,
    "liquidity_usd": float,
    "lifetime_fees_sol": float,
    "scored_at": str,           # ISO timestamp
    "created_at": str
}
```

**Error handling:** If Claude API call fails, log error and return risk_score=50 with verdict="Analysis unavailable - treat with caution" so monitoring continues.

---

### 7.6 engine/monitor.py

**Purpose:** The main poll loop. Runs every POLL_INTERVAL_SECONDS seconds.

**Functions to implement:**

```
run_monitor_cycle() -> None
```

Executes one full monitoring cycle:

1. Call `bags_client.get_recent_launches(limit=30)`
2. For each token in results:
   a. Check `database.token_already_scored(token_mint)` — skip if True
   b. Call `bags_client.get_full_token_profile(token_mint)`
   c. Call `helius_client.get_previous_token_launches(creator_wallet)`
   d. Call `helius_client.check_wallet_clustering(top_holder_wallets)`
   e. Call `helius_client.get_wallet_age_days(creator_wallet)`
   f. Call `birdeye_client.get_liquidity_data(token_mint)`
   g. Call `birdeye_client.get_trade_history(token_mint)`
   h. Bundle all data into one dict
   i. Call `scorer.calculate_risk_score(bundled_data)`
   j. Call `database.save_score(score_result)`
   k. Log result to console: `[SCAMHOUND] TOKEN_SYMBOL | Score: 72 | HIGH | reason...`
3. After saving all scores, call `twitter_bot.send_pending_alerts()`

```
start_scheduler() -> None
```
Uses APScheduler IntervalTrigger to run run_monitor_cycle() every POLL_INTERVAL_SECONDS.
Runs the first cycle immediately on startup.

---

### 7.7 alerts/twitter_bot.py

**Purpose:** Posts formatted risk alerts from @ScamHoundCrypto.

**Twitter API:** Uses Tweepy with OAuth 1.0a (required for posting tweets).

**Functions to implement:**

```
send_pending_alerts() -> None
```
1. Gets `database.get_high_risk_unnotified()` 
2. For each unnotified high-risk token, calls `format_tweet(token)` then `post_tweet(text)`
3. Calls `database.mark_tweet_sent(token_mint)` after successful post
4. Sleeps 5 seconds between tweets to avoid rate limits

```
format_tweet(token: dict) -> str
```

Tweet format for HIGH risk (score 61-80):
```
🚨 HIGH RISK ALERT — $SYMBOL on @BagsApp

⚠️ Risk Score: {score}/100
📊 Top 10 holders: {concentration}% of supply
👛 Creator wallet age: {wallet_age} days
🔗 Prior rugpulls: {prior_launches}

{first_sentence_of_verdict}

🐕 ScamHound by @DeFiAuditCCIE | bags.fm/{token_mint}
#Solana #CryptoSecurity #ScamHound
```

Tweet format for CRITICAL risk (score 81-100):
```
🚨🚨 CRITICAL RUG PULL WARNING — $SYMBOL on @BagsApp

💀 Risk Score: {score}/100 — CRITICAL
🔴 {top_risk_factors[0]}
🔴 {top_risk_factors[1]}
🔴 {top_risk_factors[2]}

{verdict}

🐕 ScamHound | @DeFiAuditCCIE | bags.fm/{token_mint}
#RugPull #Solana #ScamHound #DeFiSecurity
```

**Important:** Keep all tweets under 280 characters. Truncate verdict if needed.

```
post_tweet(text: str) -> bool
```
Posts the tweet via Tweepy. Returns True on success, False on failure. Logs all errors.

---

### 7.8 dashboard/app.py

**Purpose:** FastAPI web server serving the live dashboard and widget.

**Routes to implement:**

```
GET /
```
Renders `index.html` with last 50 scored tokens from database.
Tokens color-coded: green (LOW), yellow (MEDIUM), orange (HIGH), red (CRITICAL).
Auto-refreshes every 60 seconds via JavaScript.

```
GET /token/{token_mint}
```
Renders `token_detail.html` with full score data for one token.
Shows all risk factors, safe signals, Claude's verdict, and on-chain data.

```
GET /widget/{token_mint}
```
Renders `widget.html` — minimal embeddable badge.
Shows: token symbol, risk score, risk level colored badge.
Designed to be embedded as an iframe on any Bags token page.

```
GET /api/scores
```
Returns JSON array of last 50 scores. Allows third parties to consume the data.

```
GET /api/score/{token_mint}
```
Returns JSON for a single token. Used by the Bags App Store widget integration.

```
GET /health
```
Returns `{"status": "ok", "tokens_scored": N}`. Used for uptime monitoring.

---

### 7.9 dashboard/templates/index.html

**Purpose:** Live public dashboard at the root URL.

**Design requirements:**
- Dark theme (crypto-native look)
- ScamHound logo + tagline: "Real-time rug pull detection for Bags.fm"
- Powered by @ScamHoundCrypto branding
- Stats bar at top: Total Scanned | High Risk Found | Critical Alerts | Last Updated
- Token feed table with columns: Token, Score, Risk Level, Creator, Concentration, Wallet Age, Verdict (truncated), Scored At
- Risk level badges: color-coded pills (green/yellow/orange/red)
- Each row links to /token/{token_mint} for full detail
- Auto-refresh meta tag every 60 seconds
- Mobile responsive

---

### 7.10 dashboard/templates/token_detail.html

**Purpose:** Full detail page for a single scored token.

**Sections:**
- Token header: name, symbol, mint address, risk score badge
- Claude's full verdict in a highlighted box
- Risk factors list (red X icons)
- Safe signals list (green check icons)
- Raw on-chain data table (all numerical signals)
- Link to view on Bags.fm
- Link to view creator wallet on Solscan
- "Embed this badge" section with iframe code snippet

---

### 7.11 dashboard/templates/widget.html

**Purpose:** Minimal embeddable iframe badge for Bags App Store.

**Design:**
- Width: 300px, Height: 80px
- Background: dark with color-coded border based on risk level
- Shows: ScamHound logo (small), $SYMBOL, risk score, risk level text
- "Powered by ScamHound | @ScamHoundCrypto" small text
- Links to full token detail page on click
- Should render cleanly when embedded via iframe on any Bags token page

**Embed code for creators to copy:**
```html
<iframe src="https://scamhound.app/widget/{token_mint}" 
        width="300" height="80" frameborder="0">
</iframe>
```

---

### 7.12 main.py

**Purpose:** Entry point — starts both the web server and the monitor.

```python
import asyncio
import threading
from engine.database import init_db
from engine.monitor import start_scheduler
from dashboard.app import app
import uvicorn
import os

def run_dashboard():
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

def run_monitor():
    start_scheduler()

if __name__ == "__main__":
    init_db()
    
    # Run monitor in background thread
    monitor_thread = threading.Thread(target=run_monitor, daemon=True)
    monitor_thread.start()
    
    # Run web server in main thread
    run_dashboard()
```

Start the entire system with:
```bash
python main.py
```

---

## 8. Build Order (IDE Prompt Sequence)

Build these modules in this exact order. Each one can be handed to your IDE as a separate task.

**Prompt 1 — Setup:**
"Create the project structure, requirements.txt, .env.example, and .gitignore for a Python project called scamhound. Include all directories and empty __init__.py files as specified."

**Prompt 2 — Database:**
"Build engine/database.py as specified. Use Python's built-in sqlite3 module. Implement all 6 functions: init_db, token_already_scored, save_score, get_recent_scores, get_token_score, get_high_risk_unnotified, mark_tweet_sent."

**Prompt 3 — Bags client:**
"Build clients/bags_client.py. Use the requests library. Base URL is https://public-api-v2.bags.fm/api/v1. Auth via x-api-key header from BAGS_API_KEY env var. Implement all 5 functions plus get_full_token_profile. Handle all HTTP errors gracefully."

**Prompt 4 — Helius client:**
"Build clients/helius_client.py. Use the requests library. Base URL is https://api.helius.xyz/v0. Auth via ?api-key query param. Implement get_wallet_transaction_history, get_previous_token_launches, check_wallet_clustering, get_wallet_age_days."

**Prompt 5 — Birdeye client:**
"Build clients/birdeye_client.py. Use the requests library. Base URL is https://public-api.birdeye.so. Auth via X-API-KEY header. Implement get_token_overview, get_liquidity_data, get_trade_history, get_price_history."

**Prompt 6 — Scorer:**
"Build engine/scorer.py using the Anthropic Python SDK. Model is claude-sonnet-4-6. Implement calculate_risk_score() with the system prompt and user prompt exactly as specified. Parse Claude's JSON response. Handle API errors by returning risk_score=50 with a caution message."

**Prompt 7 — Monitor:**
"Build engine/monitor.py using APScheduler. Implement run_monitor_cycle() and start_scheduler() as specified. run_monitor_cycle calls the clients in order, bundles data, scores, saves, then triggers twitter alerts."

**Prompt 8 — Twitter bot:**
"Build alerts/twitter_bot.py using Tweepy 4.x with OAuth 1.0a. Implement send_pending_alerts, format_tweet, and post_tweet. Use the exact tweet formats specified. Keep tweets under 280 chars."

**Prompt 9 — Dashboard backend:**
"Build dashboard/app.py as a FastAPI application. Implement all 6 routes. Use Jinja2 for HTML templates. Templates are in dashboard/templates/."

**Prompt 10 — Dashboard frontend:**
"Build dashboard/templates/index.html, token_detail.html, and widget.html. Dark theme, mobile responsive, color-coded risk levels. Index auto-refreshes every 60 seconds."

**Prompt 11 — Entry point:**
"Build main.py. Starts init_db(), runs the monitor loop in a background thread, and starts the FastAPI server on the main thread."

**Prompt 12 — Integration test:**
"Write a test script test_integration.py that tests each client with a known Bags.fm token mint, prints the results, and confirms the scorer returns a valid response."

---

## 9. Risk Scoring Logic Reference

The following signals inform Claude's scoring. Include this context when fine-tuning prompts.

| Signal | Threshold | Weight |
|---|---|---|
| Top 10 holder concentration | >80% = critical, >60% = high | High |
| Creator wallet age | <7 days = critical, <30 days = high | High |
| Prior rug pulls from wallet | Any = critical | Critical |
| Holder wallet clustering score | >0.6 = critical, >0.4 = high | High |
| Liquidity/MCap ratio | <0.05 = critical, <0.10 = high | Medium |
| Wash trading score | >0.7 = critical, >0.5 = high | Medium |
| Large sell pressure | True = high | Medium |
| Creator royalty % | >5% = medium concern | Low |
| Unique trader count | <20 in 24h = medium | Low |

---

## 10. Hackathon Submission Checklist

Before submitting to DoraHacks:

- [ ] Project token launched on Bags.fm
- [ ] Bags API integrated meaningfully (not just superficial call)
- [ ] Live dashboard deployed and accessible via public URL
- [ ] @ScamHoundCrypto Twitter bot actively posting alerts
- [ ] Bags App Store widget tested and embeddable
- [ ] Open-source GitHub repository (public)
- [ ] README.md explains the problem, solution, and how Bags API is used
- [ ] Demo video (3-5 minutes) showing live token being scored
- [ ] DoraHacks submission includes: description, GitHub link, demo video, X handle (@ScamHoundCrypto or @DeFiAuditCCIE)
- [ ] Project is linked to your Bags token

---

## 11. Deployment (Quick)

### Option A — Railway (easiest, free tier)
1. Push to GitHub
2. Connect Railway to the repo
3. Set all environment variables in Railway dashboard
4. Deploy — Railway auto-detects Python and runs main.py

### Option B — DigitalOcean Droplet ($6/month)
```bash
# On the server
git clone your_repo
cd scamhound
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with real keys
python main.py
```

Use screen or systemd to keep it running after SSH disconnect.

### Option C — Render (free tier)
Same as Railway. Set start command to `python main.py`.

---

## 12. Project Narrative (For Judges)

Every day, hundreds of new tokens launch on Bags.fm. Most are legitimate. Some aren't. Right now, there's no native security layer in the Bags ecosystem to warn traders before they get rugged.

ScamHound fills that gap.

We pull every new Bags.fm launch via the Bags API, cross-reference the creator wallet against on-chain history via Helius, analyze market manipulation signals via Birdeye, and feed the combined data to Claude AI for a risk score and plain-English verdict. Traders get real-time alerts on the dashboard, on Twitter, and via an embeddable badge any Bags creator can add to their token page to prove legitimacy.

This is the tool that was missing from the Bags ecosystem. Built by a CCIE with 25 years of cybersecurity experience who spent years identifying exactly these patterns in the wild.

ScamHound doesn't predict the future. It reads the on-chain evidence that's already there — and translates it into something any trader can understand in five seconds.
