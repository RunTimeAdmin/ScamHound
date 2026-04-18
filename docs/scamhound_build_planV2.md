# ScamHound: AI-Powered Rug Pull Early Warning System
## Complete Build Specification — Bags.fm Hackathon 2026

**Project Token:** Launch on Bags.fm before or during submission  
**Hackathon:** The Bags Hackathon — DoraHacks (submissions close June 1, 2026)  
**Prize pool:** $1M, top 100 projects ($10K–$100K each)  
**Twitter brand:** @ScamHoundCrypto  
**Author:** David Cooper, CCIE #14019

---

## 1. Project Overview

ScamHound is a real-time AI-powered rug pull early warning system built first for the Bags.fm ecosystem and architected to cover every major Solana token launchpad. It monitors every newly launched token, aggregates on-chain signals from Helius and Birdeye, scores each token using Claude AI, and delivers risk alerts via:

- A live web dashboard (public-facing, filterable by platform)
- @ScamHoundCrypto Twitter/X bot (automated alerts tagged by platform)
- A Bags App Store embeddable widget (risk badge any Bags creator can embed)

The scoring engine analyzes wallet clustering, liquidity manipulation, dev wallet concentration, creator history, and trading pattern anomalies. Each token receives a 0–100 risk score with a plain-English AI-generated explanation.

**Platform priority:**
- **Primary (Bags.fm):** Deep integration via official REST API. This is the hackathon target and receives the most complete data profile including creator royalties, fee velocity, and claim stats — signals unique to the Bags ecosystem.
- **Secondary (pump.fun):** Integrated via PumpPortal, a free third-party data API covering pump.fun's bonding curve.
- **Tertiary (LetsBonk, Moonshot, Raydium LaunchLab):** Detected on-chain via Helius program ID monitoring. No dedicated client needed — Helius already covers these.

The multi-platform architecture uses a single shared scoring engine. Platform detection is abstracted into `platform_router.py` so new launchpads can be added by registering a program ID or a new client — nothing else changes.

**Why this wins the hackathon:** Every other submission builds trading dashboards. Nobody else has 25 years of cybersecurity experience and an existing scam detection brand. This is the only security-first entry. The multi-platform angle — built on top of Bags-first infrastructure — is the "and it scales beyond Bags" story that makes it fundable from the $3M continuation fund.

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+ |
| Scheduler | APScheduler (polls every 60 seconds) |
| Web framework | FastAPI + Uvicorn |
| Frontend | Vanilla HTML/CSS/JS (no framework, keep it simple) |
| AI scoring | Anthropic Claude API (claude-sonnet-4-6) |
| Primary data (Bags) | Bags.fm REST API — official, deepest integration |
| Secondary data (pump.fun) | PumpPortal API — free third-party pump.fun data |
| On-chain (all platforms) | Helius API — program ID monitoring for LetsBonk, Moonshot, LaunchLab |
| Market data (all platforms) | Birdeye API — covers all Solana tokens regardless of launchpad |
| Platform routing | platform_router.py — dispatches to correct client per platform |
| Twitter alerts | Tweepy (Twitter API v2) |
| Database | SQLite (via Python sqlite3, no ORM needed) |
| Config | python-dotenv |
| Deployment | Hostinger VPS (Ubuntu 22.04) |

---

## 3. API Keys Required (Gather Before Building)

| Service | Where to get it | Cost | Used for |
|---|---|---|---|
| Bags.fm API | dev.bags.fm | Free | Primary platform — Bags tokens |
| PumpPortal API | pumpportal.fun | Free (data), 0.5% fee (trading) | pump.fun token data |
| Helius API | helius.dev | Free tier, hackathon credits available | On-chain analysis + LetsBonk/Moonshot detection |
| Birdeye API | birdeye.so | Free tier available | Market data for all platforms |
| Anthropic API | console.anthropic.com | Pay per use | Claude scoring engine |
| Twitter/X API | developer.twitter.com | Free Basic tier | @ScamHoundCrypto alerts |

**Note on PumpPortal:** No API key required for the free data endpoints. Just start making requests. The trading API requires a key, but ScamHound only needs the data endpoints.

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
│   ├── platform_router.py        # Dispatches to correct client per platform
│   ├── bags_client.py            # PRIMARY — Bags.fm official REST API
│   ├── pumpfun_client.py         # SECONDARY — PumpPortal free data API
│   ├── helius_client.py          # ALL PLATFORMS — on-chain analysis + program ID detection
│   └── birdeye_client.py         # ALL PLATFORMS — market data
│
├── engine/
│   ├── __init__.py
│   ├── scorer.py                 # Bundles data + calls Claude API (platform-agnostic)
│   ├── monitor.py                # Poll loop — finds new tokens across all platforms
│   └── database.py               # SQLite read/write (platform field included)
│
├── alerts/
│   ├── __init__.py
│   └── twitter_bot.py            # Posts formatted alerts to @ScamHoundCrypto
│
├── dashboard/
│   ├── app.py                    # FastAPI routes
│   └── templates/
│       ├── index.html            # Main live dashboard (platform filter tabs)
│       ├── token_detail.html     # Per-token detail page
│       └── widget.html           # Embeddable badge widget (Bags App Store)
│
└── static/
    ├── style.css                 # Dashboard styles
    └── scamhound_logo.png        # Brand asset
```

---

## 5. Environment Configuration

### .env (never commit — add to .gitignore)
```
# Bags.fm (PRIMARY — required)
BAGS_API_KEY=your_key_from_dev.bags.fm

# PumpPortal — pump.fun data (no key needed for free data endpoints)
# Set to "true" to enable pump.fun monitoring
ENABLE_PUMPFUN=true

# Helius (required — covers on-chain analysis + LetsBonk/Moonshot/LaunchLab)
HELIUS_API_KEY=your_key_from_helius.dev

# Birdeye (required — market data for all platforms)
BIRDEYE_API_KEY=your_key_from_birdeye.so

# Anthropic
ANTHROPIC_API_KEY=your_anthropic_key

# Twitter/X (@ScamHoundCrypto)
TWITTER_BEARER_TOKEN=
TWITTER_API_KEY=
TWITTER_API_SECRET=
TWITTER_ACCESS_TOKEN=
TWITTER_ACCESS_SECRET=

# Platform toggles (set to "true" to enable each platform)
ENABLE_BAGS=true
ENABLE_PUMPFUN=true
ENABLE_LETSBONK=true
ENABLE_MOONSHOT=true
ENABLE_LAUNCHLAB=false     # Enable when ready — same Helius client, different program ID

# App config
RISK_ALERT_THRESHOLD=65         # Score at or above this triggers a tweet
POLL_INTERVAL_SECONDS=60        # How often to check for new launches (all platforms)
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

### 7.0 Platform Architecture Overview

All platform clients return the same normalized token profile shape. The scorer, monitor, database, and dashboard never need to know which platform a token came from — they just see the standardized dict with a `platform` field attached.

**Solana Program IDs for on-chain detection (used by Helius):**

| Platform | Program ID | Detection method |
|---|---|---|
| Bags.fm | N/A | Official REST API |
| pump.fun | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | PumpPortal API + on-chain fallback |
| LetsBonk/Bonk.fun | `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj` | Helius program monitoring |
| Moonshot | `MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG` | Helius program monitoring |
| Raydium LaunchLab | `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj` | Helius program monitoring |

**Normalized token profile shape (all platforms return this):**
```python
{
    "token_mint": str,
    "name": str,
    "symbol": str,
    "created_at": str,
    "platform": str,           # "bags" | "pumpfun" | "letsbonk" | "moonshot"
    "creator": {
        "wallet": str,
        "username": str | None,   # Only Bags provides this natively
        "royalty_pct": float      # Only Bags; None for other platforms
    },
    "holders": {
        "top_holders": list,
        "top_10_concentration_pct": float,
        "total_holder_count": int
    },
    "lifetime_fees_sol": float,
    "claim_stats": dict | None,  # Bags only
    "source": str                # Which API provided the data
}
```

---

### 7.1 clients/platform_router.py

**Purpose:** Single entry point for the monitor. Checks which platforms are enabled, calls the right client for each, and returns a unified list of new token profiles with the `platform` field set.

**Functions to implement:**

```
get_enabled_platforms() -> list
```
Reads the ENABLE_* env vars and returns a list of enabled platform names.
Example return: `["bags", "pumpfun", "letsbonk"]`

```
get_all_new_launches(seen_mints: set) -> list
```
- Calls each enabled platform's `get_recent_launches()` function
- Filters out mints already in `seen_mints` (passed in from monitor.py)
- Tags each token dict with its `platform` value
- Returns flat list of all new token dicts across all platforms

**Platform client mapping:**
```python
PLATFORM_CLIENTS = {
    "bags":      bags_client,
    "pumpfun":   pumpfun_client,
    "letsbonk":  helius_client,   # uses program ID monitoring
    "moonshot":  helius_client,   # uses program ID monitoring
}

PLATFORM_PROGRAM_IDS = {
    "letsbonk": "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj",
    "moonshot":  "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG",
}
```

When calling `helius_client` for LetsBonk or Moonshot, pass the relevant program ID so it knows which launches to fetch.

---

### 7.2 clients/bags_client.py

**Purpose:** All interactions with the Bags.fm REST API. This is the deepest integration and the primary data source for the hackathon. Bags provides signals no other platform offers natively: creator royalty percentage, fee claim velocity, and the social username tied to the creator wallet.

**Base URL:** `https://public-api-v2.bags.fm/api/v1`  
**Auth header:** `x-api-key: {BAGS_API_KEY}`  
**Rate limit:** 1,000 requests/hour  
**Platform tag:** all returned dicts include `"platform": "bags"` and `"source": "bags_api"`

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

### 7.3 clients/pumpfun_client.py

**Purpose:** Fetches newly launched tokens from pump.fun using the PumpPortal free data API.

**Base URL:** `https://pumpportal.fun/api`  
**Auth:** None required for data endpoints  
**Rate limit:** Subject to PumpPortal's fair use limits — add a 1-second sleep between bulk calls  
**Platform tag:** all returned dicts include `"platform": "pumpfun"` and `"source": "pumpportal_api"`

**Functions to implement:**

```
get_recent_launches(limit=20) -> list
```
- Endpoint: GET `/data/new-tokens?limit={limit}`
- Returns list of recently created pump.fun tokens
- Each item includes: mint, name, symbol, createdTimestamp, creator (wallet address)
- Normalize field names to match the standard profile shape before returning

```
get_token_metadata(token_mint: str) -> dict
```
- Endpoint: GET `/data/token-info?mint={token_mint}`
- Returns: name, symbol, description, creator wallet, created timestamp
- Note: pump.fun does not expose royalty/fee data — set `royalty_pct: None` in the normalized profile

```
get_bonding_curve_data(token_mint: str) -> dict
```
- Endpoint: GET `/data/bonding-curve?mint={token_mint}`
- Returns: virtual sol reserves, virtual token reserves, complete (bool — True = graduated to PumpSwap)
- Graduated tokens have different liquidity dynamics — note this in the profile

```
get_full_token_profile(token_mint: str) -> dict
```
- Calls get_token_metadata and get_bonding_curve_data
- Returns normalized profile shape with `platform: "pumpfun"`
- Sets `claim_stats: None` (not available on pump.fun)
- Sets `royalty_pct: None` (not available on pump.fun)

**Important difference from Bags:** pump.fun does not provide a social username for the creator. The creator field will have `wallet` only and `username: None`. The Helius wallet history check becomes even more critical for pump.fun tokens because it's the only signal for creator reputation.

**Error handling:** Same as bags_client — catch all request exceptions, return None on failure.

---

### 7.4 clients/helius_client.py

**Purpose:** Deep on-chain analysis using creator wallet addresses. Also handles new token launch detection for platforms without their own APIs (LetsBonk, Moonshot) by monitoring Solana program IDs.

**Base URL:** `https://api.helius.xyz/v0`  
**Auth:** `?api-key={HELIUS_API_KEY}` as query param

**Functions to implement:**

```
get_recent_launches_by_program(program_id: str, limit=20) -> list
```
- Endpoint: GET `/program-events/{program_id}?api-key={key}&limit={limit}`
- Detects new token creation events for LetsBonk and Moonshot
- Returns list of token mints created since the last poll
- Each item normalized to the standard profile shape with appropriate `platform` tag
- Called by platform_router.py with the correct program ID per platform

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
- Takes the top holder wallet list
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

### 7.5 clients/birdeye_client.py

**Purpose:** Market-side analysis — liquidity, trading patterns, price action. Works identically for all platforms since Birdeye indexes all Solana tokens regardless of launchpad origin.

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

### 7.6 engine/database.py

**Purpose:** Lightweight SQLite persistence so we don't re-score the same token repeatedly and can serve the dashboard.

**Database file:** scamhound.db (path from .env)

**Tables:**

```sql
CREATE TABLE IF NOT EXISTS scored_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT UNIQUE NOT NULL,
    name TEXT,
    symbol TEXT,
    platform TEXT DEFAULT 'bags',  -- bags | pumpfun | letsbonk | moonshot
    risk_score INTEGER,            -- 0-100
    risk_level TEXT,               -- LOW / MEDIUM / HIGH / CRITICAL
    ai_verdict TEXT,               -- Claude's plain English explanation (2-3 sentences)
    top_10_concentration REAL,
    creator_wallet TEXT,
    creator_username TEXT,         -- Bags only; NULL for other platforms
    royalty_pct REAL,              -- Bags only; NULL for other platforms
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
get_recent_scores(limit=50, platform=None) -> list
```
Returns last N scored tokens ordered by scored_at DESC.
If `platform` is provided, filters to that platform only.
Used by dashboard — pass platform filter when a tab is selected.

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

```
get_platform_stats() -> dict
```
Returns count of scored tokens per platform and count of high-risk per platform.
Used by the dashboard stats bar.
Shape: `{"bags": {"total": 120, "high_risk": 14}, "pumpfun": {"total": 340, "high_risk": 67}, ...}`

---

### 7.7 engine/scorer.py

**Purpose:** The brain of the system. Platform-agnostic — receives normalized token data regardless of origin and asks Claude to score it. The prompt adapts based on which fields are available (Bags-exclusive fields like royalty and username are included when present; None values are noted as unavailable).

**Model to use:** claude-sonnet-4-6

**Functions to implement:**

```
calculate_risk_score(token_profile: dict) -> dict
```

This function:
1. Receives the full normalized token profile dict (platform + Helius + Birdeye data combined)
2. Builds a platform-aware prompt for Claude (includes platform name, omits unavailable fields)
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

When Bags.fm-specific fields (royalty %, creator username, fee claim data) are available, 
weight them heavily. When they are marked as unavailable, focus on the on-chain and market 
signals that apply to all platforms.
"""
```

**User prompt — platform-aware, injects available data:**

```python
USER_PROMPT = f"""
Analyze this Solana token for rug pull risk:

TOKEN DETAILS:
- Name: {name}
- Symbol: {symbol}
- Token Mint: {token_mint}
- Launched: {created_at}
- Platform: {platform}

PLATFORM DATA ({platform.upper()}):
- Creator username: {creator_username or "Not available on this platform"}
- Creator wallet: {creator_wallet}
- Creator royalty: {f"{royalty_pct}%" if royalty_pct is not None else "Not available on this platform"}
- Top 10 holder concentration: {top_10_concentration_pct}%
- Total holders: {total_holder_count}
- Lifetime trading fees collected: {lifetime_fees_sol} SOL
- Fee claim stats: {claim_stats if claim_stats else "Not available on this platform"}
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

### 7.8 engine/monitor.py

**Purpose:** The main poll loop. Runs every POLL_INTERVAL_SECONDS seconds. Uses platform_router to get new launches across all enabled platforms — it never calls individual clients directly.

**Functions to implement:**

```
run_monitor_cycle() -> None
```

Executes one full monitoring cycle:

1. Call `platform_router.get_all_new_launches(seen_mints)` where `seen_mints` is a set of already-scored token mints from the database
2. For each new token profile returned (already tagged with `platform`):
   a. Call `helius_client.get_previous_token_launches(creator_wallet)`
   b. Call `helius_client.check_wallet_clustering(top_holder_wallets)`
   c. Call `helius_client.get_wallet_age_days(creator_wallet)`
   d. Call `birdeye_client.get_liquidity_data(token_mint)`
   e. Call `birdeye_client.get_trade_history(token_mint)`
   f. Merge all returned data into the token profile dict
   g. Call `scorer.calculate_risk_score(merged_profile)`
   h. Call `database.save_score(score_result)`
   i. Log: `[SCAMHOUND] [{platform.upper()}] $SYMBOL | Score: 72 | HIGH | reason...`
3. After processing all tokens, call `twitter_bot.send_pending_alerts()`

```
start_scheduler() -> None
```
Uses APScheduler IntervalTrigger to run run_monitor_cycle() every POLL_INTERVAL_SECONDS.
Runs the first cycle immediately on startup.

---

### 7.9 alerts/twitter_bot.py

**Purpose:** Posts formatted risk alerts from @ScamHoundCrypto. Tweet format includes the platform name so followers know which launchpad the token came from.

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

Platform display name mapping (used in tweets):
```python
PLATFORM_DISPLAY = {
    "bags":      "@BagsApp",
    "pumpfun":   "pump.fun",
    "letsbonk":  "LetsBonk",
    "moonshot":  "Moonshot",
}
```

Tweet format for HIGH risk (score 61-80):
```
🚨 HIGH RISK — $SYMBOL on {platform_display}

⚠️ Score: {score}/100
📊 Top 10 holders: {concentration}% of supply
👛 Creator wallet: {wallet_age} days old
🔗 Prior rugpulls: {prior_launches}

{first_sentence_of_verdict}

🐕 @ScamHoundCrypto | @DeFiAuditCCIE
#Solana #ScamHound #{platform_hashtag}
```

Tweet format for CRITICAL risk (score 81-100):
```
🚨🚨 CRITICAL RUG PULL — $SYMBOL on {platform_display}

💀 Score: {score}/100
🔴 {top_risk_factors[0]}
🔴 {top_risk_factors[1]}
🔴 {top_risk_factors[2]}

{verdict_truncated}

🐕 @ScamHoundCrypto | @DeFiAuditCCIE
#RugPull #Solana #ScamHound
```

Platform hashtag mapping: `{"bags": "BagsApp", "pumpfun": "PumpFun", "letsbonk": "LetsBonk", "moonshot": "Moonshot"}`

**Important:** Keep all tweets under 280 characters. Truncate verdict to fit.

```
post_tweet(text: str) -> bool
```
Posts the tweet via Tweepy. Returns True on success, False on failure. Logs all errors.

---

### 7.10 dashboard/app.py

**Purpose:** FastAPI web server serving the live dashboard and widget.

**Routes to implement:**

```
GET /
```
Renders `index.html` with last 50 scored tokens across ALL platforms.
Passes platform stats to the template for the stats bar.
Tokens color-coded: green (LOW), yellow (MEDIUM), orange (HIGH), red (CRITICAL).
Auto-refreshes every 60 seconds via JavaScript.

```
GET /?platform={platform}
```
Same as above but filters the token feed to one platform.
The `platform` query param maps to the tab selected in the UI.
Valid values: `bags`, `pumpfun`, `letsbonk`, `moonshot`, `all`
Default is `all`.

```
GET /token/{token_mint}
```
Renders `token_detail.html` with full score data for one token.
Shows all risk factors, safe signals, Claude's verdict, on-chain data, and platform badge.

```
GET /widget/{token_mint}
```
Renders `widget.html` — minimal embeddable badge for Bags App Store.
Shows: token symbol, platform badge, risk score, risk level colored badge.
Designed to be embedded as an iframe on any Bags token page.

```
GET /api/scores
```
Returns JSON array of last 50 scores across all platforms.
Accepts optional `?platform=` query param to filter.
Allows third parties to consume the data.

```
GET /api/score/{token_mint}
```
Returns JSON for a single token. Used by the Bags App Store widget integration.

```
GET /api/stats
```
Returns platform stats from `database.get_platform_stats()`.
Shape: `{"bags": {"total": 120, "high_risk": 14}, "pumpfun": {"total": 340, "high_risk": 67}, ...}`

```
GET /health
```
Returns `{"status": "ok", "tokens_scored": N, "platforms_active": [...]}`. Used for uptime monitoring.

---

### 7.11 dashboard/templates/index.html

**Purpose:** Live public dashboard at the root URL.

**Design requirements:**
- Dark theme (crypto-native look)
- ScamHound logo + tagline: "Real-time rug pull detection for Solana launchpads"
- Subheadline: "Primary coverage: @BagsApp — also monitoring pump.fun, LetsBonk, Moonshot"
- Powered by @ScamHoundCrypto branding
- Stats bar at top: Total Scanned | High Risk Found | Critical Alerts | Last Updated
- Platform tab bar below stats: ALL | BAGS | PUMP.FUN | LETSBONK | MOONSHOT
  - Active tab highlighted, clicking a tab reloads with `?platform=` param
  - Bags tab is the default and displayed first — it is the primary platform
  - Each tab shows a count badge (e.g. "BAGS (120)")
- Token feed table with columns: Platform (badge), Token, Score, Risk Level, Creator, Concentration, Wallet Age, Verdict (truncated), Scored At
- Risk level badges: color-coded pills (green/yellow/orange/red)
- Platform badges: small colored pills showing platform name
- Each row links to /token/{token_mint} for full detail
- Auto-refresh every 60 seconds via JavaScript (preserves active platform tab)
- Mobile responsive

---

### 7.12 dashboard/templates/token_detail.html

**Purpose:** Full detail page for a single scored token.

**Sections:**
- Token header: name, symbol, mint address, platform badge, risk score badge
- Claude's full verdict in a highlighted box
- Risk factors list (red X icons)
- Safe signals list (green check icons)
- Raw on-chain data table (all numerical signals)
- Platform-specific links:
  - If `platform == "bags"`: link to bags.fm token page
  - If `platform == "pumpfun"`: link to pump.fun token page
  - All platforms: link to creator wallet on Solscan
- "Embed this badge" section with iframe code snippet (show for all platforms, not just Bags)

---

### 7.13 dashboard/templates/widget.html

**Purpose:** Minimal embeddable iframe badge. Primary use case is the Bags App Store, but works for any platform token page.

**Design:**
- Width: 300px, Height: 80px
- Background: dark with color-coded border based on risk level
- Shows: ScamHound logo (small), platform badge (small), $SYMBOL, risk score, risk level text
- "Powered by ScamHound | @ScamHoundCrypto" small text
- Links to full token detail page on click
- Renders cleanly when embedded via iframe on any page

**Embed code for creators to copy:**
```html
<iframe src="https://scamhound.app/widget/{token_mint}" 
        width="300" height="80" frameborder="0">
</iframe>
```

---

### 7.14 main.py

**Purpose:** Entry point — starts both the web server and the monitor. Logs which platforms are enabled on startup.

```python
import threading
from engine.database import init_db
from engine.monitor import start_scheduler
from clients.platform_router import get_enabled_platforms
from dashboard.app import app
import uvicorn
import os

def run_dashboard():
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

def run_monitor():
    start_scheduler()

if __name__ == "__main__":
    init_db()
    
    enabled = get_enabled_platforms()
    print(f"[SCAMHOUND] Starting with platforms: {enabled}")
    print(f"[SCAMHOUND] Primary platform: bags (hackathon mode)")
    
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

Build in this exact order. Each prompt is scoped to one module. Bags integration is built and verified before any secondary platform is added.

**Prompt 1 — Setup:**
"Create the project structure, requirements.txt, .env.example, and .gitignore for a Python project called scamhound. Include all directories and empty __init__.py files as specified in the file structure. The clients/ directory needs: platform_router.py, bags_client.py, pumpfun_client.py, helius_client.py, birdeye_client.py."

**Prompt 2 — Database:**
"Build engine/database.py using Python's built-in sqlite3 module. The scored_tokens table must include a `platform` TEXT column (default 'bags') and a `royalty_pct` REAL column (nullable). Implement all functions: init_db, token_already_scored, save_score, get_recent_scores (with optional platform filter param), get_token_score, get_high_risk_unnotified, mark_tweet_sent, get_platform_stats."

**Prompt 3 — Bags client (PRIMARY — build and verify first):**
"Build clients/bags_client.py. Base URL is https://public-api-v2.bags.fm/api/v1. Auth via x-api-key header from BAGS_API_KEY env var. All returned dicts must include platform='bags' and source='bags_api'. Implement: get_recent_launches, get_token_creators, get_top_holders, get_lifetime_fees, get_token_claim_stats, get_full_token_profile. Handle all HTTP errors gracefully — return None on failure."

**Prompt 4 — Helius client:**
"Build clients/helius_client.py. Base URL is https://api.helius.xyz/v0. Auth via ?api-key query param. Implement: get_recent_launches_by_program(program_id, limit) for LetsBonk/Moonshot detection, get_wallet_transaction_history, get_previous_token_launches, check_wallet_clustering, get_wallet_age_days. get_recent_launches_by_program should return normalized token profile dicts with the correct platform tag passed in as a parameter."

**Prompt 5 — pump.fun client:**
"Build clients/pumpfun_client.py using the PumpPortal free data API (https://pumpportal.fun/api). No API key required. All returned dicts must include platform='pumpfun' and source='pumpportal_api'. Set royalty_pct=None and claim_stats=None since pump.fun does not expose these. Implement: get_recent_launches, get_token_metadata, get_bonding_curve_data, get_full_token_profile. Add a 1-second sleep between bulk calls to respect rate limits."

**Prompt 6 — Birdeye client:**
"Build clients/birdeye_client.py. Base URL is https://public-api.birdeye.so. Auth via X-API-KEY header. This client is platform-agnostic — it works for any Solana token mint regardless of launchpad. Implement: get_token_overview, get_liquidity_data, get_trade_history, get_price_history."

**Prompt 7 — Platform router:**
"Build clients/platform_router.py. It reads ENABLE_BAGS, ENABLE_PUMPFUN, ENABLE_LETSBONK, ENABLE_MOONSHOT env vars to determine which platforms are active. Implement get_enabled_platforms() and get_all_new_launches(seen_mints: set). The router calls bags_client for bags, pumpfun_client for pumpfun, and helius_client.get_recent_launches_by_program() for letsbonk and moonshot using the correct program IDs. Returns a flat list of normalized token dicts all tagged with their platform. Bags is always called first."

**Prompt 8 — Scorer:**
"Build engine/scorer.py using the Anthropic Python SDK. Model is claude-sonnet-4-6. The prompt must be platform-aware: it injects the platform name and gracefully handles None values for fields not available on non-Bags platforms (royalty_pct, claim_stats, creator username). Implement calculate_risk_score() with the system prompt and user prompt as specified. Parse Claude's JSON response. Handle API errors by returning risk_score=50 with a caution message."

**Prompt 9 — Monitor:**
"Build engine/monitor.py using APScheduler. Implement run_monitor_cycle() and start_scheduler(). The monitor calls platform_router.get_all_new_launches() — never individual platform clients directly. After getting the launch list, it calls helius and birdeye clients for analysis, merges data, scores, saves, then triggers alerts. Log format: [SCAMHOUND] [PLATFORM] $SYMBOL | Score: 72 | HIGH | reason..."

**Prompt 10 — Twitter bot:**
"Build alerts/twitter_bot.py using Tweepy 4.x with OAuth 1.0a. Implement send_pending_alerts, format_tweet, and post_tweet. Tweets must include the platform name using the PLATFORM_DISPLAY mapping. Use the exact tweet formats specified. Keep tweets under 280 chars. Sleep 5 seconds between tweets."

**Prompt 11 — Dashboard backend:**
"Build dashboard/app.py as a FastAPI application with Jinja2 templates. Implement all routes including GET / with optional ?platform= query param for filtering, GET /api/stats returning per-platform counts, and the existing token detail, widget, and health routes. Pass platform_stats and active_platform to the index template."

**Prompt 12 — Dashboard frontend:**
"Build dashboard/templates/index.html, token_detail.html, and widget.html. Dark theme, mobile responsive, color-coded risk levels. Index must have a platform tab bar (ALL | BAGS | PUMP.FUN | LETSBONK | MOONSHOT) where clicking a tab filters the feed. BAGS tab is first and default. Each tab shows a count badge. Token rows include a small platform badge. Auto-refresh every 60 seconds preserving the active tab via URL param."

**Prompt 13 — Entry point:**
"Build main.py. On startup: call init_db(), log enabled platforms via get_enabled_platforms(), run the monitor loop in a background thread, and start the FastAPI server on the main thread."

**Prompt 14 — Integration test:**
"Write test_integration.py. Test Bags client with a known Bags.fm token mint. Test PumpPortal client with a known pump.fun token mint. Test Helius wallet history with a known wallet. Test Birdeye with a known token mint. Test the full scorer with a mock bundled payload. Print all results. This is the verification script run after each build phase."

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

## 11. Deployment

Deployment target is Hostinger VPS (Ubuntu 22.04 LTS). Full step-by-step deployment instructions including SSH setup, systemd service, Nginx reverse proxy, SSL via Certbot, and UFW firewall configuration are documented in the companion file:

**scamhound_hostinger_deployment.md**

Quick start for the impatient:
```bash
# On Hostinger VPS after initial setup
git clone https://github.com/YOUR_USERNAME/scamhound.git
cd scamhound
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env          # Fill in all API keys
python main.py     # Verify it starts before setting up systemd
```

Minimum VPS spec: KVM 1 (1 vCPU, 4GB RAM, 50GB NVMe) — sufficient for all platforms running concurrently at 60-second poll intervals.

---

## 12. Project Narrative (For Judges)

Every day, hundreds of new tokens launch on Bags.fm. Thousands more launch on pump.fun, LetsBonk, and Moonshot. Most are legitimate. Some aren't. Right now, there's no native security layer in any of these ecosystems to warn traders before they get rugged.

ScamHound fills that gap — starting with Bags.

The Bags.fm integration is the deepest in the system. Bags gives us signals no other launchpad provides: the creator's social identity tied to their wallet, their royalty percentage, and the velocity at which they're claiming trading fees. These are uniquely powerful rug pull signals and they only exist because Bags was built with creator accountability in mind.

On top of the Bags API, we layer Helius for on-chain creator history — checking whether the same wallet launched and abandoned three tokens last month — and Birdeye for market manipulation signals. Claude AI synthesizes all of it into a plain-English risk score any trader can understand in five seconds.

The multi-platform extension means ScamHound can cover pump.fun via PumpPortal and detect LetsBonk and Moonshot launches directly on-chain via Helius program monitoring. Same scoring engine, same output layer, same @ScamHoundCrypto alerts. The platform router abstracts the difference so adding a new launchpad later is a single registration, not a rewrite.

The Bags widget is the distribution flywheel. Any legitimate Bags creator can embed a green "ScamHound Verified" badge on their token page. That badge is only green if the token scored LOW risk. That creates an incentive for good actors to use the tool, and makes the red badges on bad tokens impossible to fake.

This is the tool that was missing from the Bags ecosystem. Built by a CCIE with 25 years of cybersecurity experience who has spent years identifying exactly these patterns in the wild. ScamHound doesn't predict the future. It reads the on-chain evidence that's already there — and translates it into something everyone understands.
