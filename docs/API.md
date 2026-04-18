# ScamHound API Reference

Complete reference for all FastAPI endpoints in the ScamHound dashboard.

**Base URL:** `http://localhost:8000` (default)

---

## Dashboard Routes (HTML)

These endpoints return HTML pages rendered with Jinja2 templates.

### GET /

Main dashboard page displaying the last 50 scored tokens with statistics.

**Response:** HTML page with:
- List of recent token scores
- Dashboard statistics (total scanned, high risk count, critical alerts)
- Auto-refresh functionality

---

### GET /token/{token_mint}

Token detail page showing full analysis for a single token.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `token_mint` | path | Solana token mint address |

**Responses:**
- `200 OK` — Token detail page
- `404 Not Found` — Token not found in database

---

### GET /widget/{token_mint}

Embeddable widget badge for external websites.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `token_mint` | path | Solana token mint address |

**Response:** Minimal HTML widget showing risk score and level

---

### GET /settings

API key configuration page.

**Features:**
- Displays all configuration keys with masked values (••••last4)
- Input fields for updating API keys
- Save functionality via JavaScript fetch to `/api/settings`

**Response:** HTML settings page

---

## API Routes (JSON)

These endpoints return JSON responses for programmatic access.

### GET /api/scores

Get a list of recent token scores.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `limit` | query | 50 | Maximum number of scores to return |

**Response:**
```json
[
  {
    "id": 1,
    "token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "name": "USD Coin",
    "symbol": "USDC",
    "risk_score": 15,
    "risk_level": "LOW",
    "ai_verdict": "Token shows healthy distribution patterns...",
    "top_risk_factors": ["Low liquidity ratio"],
    "top_safe_signals": ["Established token", "High holder count"],
    "top_10_concentration": 25.5,
    "creator_wallet": "...",
    "creator_username": "...",
    "prior_launches": 0,
    "wallet_age_days": 365,
    "clustering_score": 0.1,
    "liquidity_usd": 1000000.00,
    "lifetime_fees_sol": 500.0,
    "tweet_sent": false,
    "scored_at": "2026-04-18T12:00:00",
    "created_at": "2020-01-01T00:00:00"
  }
]
```

---

### GET /api/score/{token_mint}

Get a single token's score by mint address.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `token_mint` | path | Solana token mint address |

**Responses:**

**200 OK:**
```json
{
  "id": 1,
  "token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "name": "USD Coin",
  "symbol": "USDC",
  "risk_score": 15,
  "risk_level": "LOW",
  "ai_verdict": "Token shows healthy distribution patterns...",
  "top_risk_factors": [],
  "top_safe_signals": ["Established token", "High holder count"],
  "top_10_concentration": 25.5,
  "creator_wallet": "...",
  "creator_username": "...",
  "prior_launches": 0,
  "wallet_age_days": 365,
  "clustering_score": 0.1,
  "liquidity_usd": 1000000.00,
  "lifetime_fees_sol": 500.0,
  "tweet_sent": false,
  "scored_at": "2026-04-18T12:00:00",
  "created_at": "2020-01-01T00:00:00"
}
```

**404 Not Found:**
```json
{
  "error": "Token not found"
}
```

---

### GET /api/stats

Get dashboard statistics.

**Response:**
```json
{
  "total_scanned": 150,
  "high_risk": 12,
  "critical_alerts": 3,
  "last_updated": "2026-04-18T12:00:00"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_scanned` | integer | Total number of tokens in database |
| `high_risk` | integer | Count of HIGH risk level tokens |
| `critical_alerts` | integer | Count of CRITICAL risk level tokens |
| `last_updated` | string | ISO timestamp of most recent score |

---

### POST /api/scan

Manually trigger a scan for a specific token mint address.

**Request Body:**
```json
{
  "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
}
```

**Validation:**
- Mint address must be 32-44 characters
- Must be valid base58 Solana address format (basic length check)

**Responses:**

**200 OK:**
```json
{
  "success": true,
  "result": {
    "token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "name": "USD Coin",
    "symbol": "USDC",
    "risk_score": 15,
    "risk_level": "LOW",
    "verdict": "Token shows healthy distribution patterns...",
    "top_risk_factors": [],
    "top_safe_signals": ["Established token"],
    "creator_wallet": "...",
    "creator_username": "...",
    "top_10_concentration": 25.5,
    "prior_launches": 0,
    "wallet_age_days": 365,
    "clustering_score": 0.1,
    "liquidity_usd": 1000000.00,
    "lifetime_fees_sol": 500.0,
    "token_age_minutes": 1234567,
    "token_status": "active",
    "scored_at": "2026-04-18T12:00:00",
    "created_at": "2020-01-01T00:00:00"
  }
}
```

**400 Bad Request:**
```json
{
  "success": false,
  "error": "Missing 'mint' field"
}
```
```json
{
  "success": false,
  "error": "Invalid mint address format"
}
```

**500 Internal Server Error:**
```json
{
  "success": false,
  "error": "Scan failed or token not found"
}
```

---

### POST /api/settings

Save API keys and configuration settings.

**Request Body:**
```json
{
  "HELIUS_API_KEY": "your_helius_key",
  "BAGS_API_KEY": "your_bags_key",
  "BIRDEYE_API_KEY": "your_birdeye_key",
  "BUBBLEMAPS_API_KEY": "your_bubblemaps_key",
  "ANTHROPIC_API_KEY": "your_anthropic_key",
  "RISK_ALERT_THRESHOLD": "65",
  "POLL_INTERVAL_SECONDS": "60"
}
```

**Behavior:**
- Empty values are skipped (won't overwrite existing)
- Masked values (starting with `••••`) are skipped
- Saves to `config.json` and updates `os.environ`

**Responses:**

**200 OK:**
```json
{
  "success": true,
  "message": "Settings saved"
}
```

**400 Bad Request:**
```json
{
  "success": false,
  "error": "Invalid request body"
}
```

**500 Internal Server Error:**
```json
{
  "success": false,
  "error": "Failed to save settings"
}
```

---

### GET /health

Health check endpoint for uptime monitoring.

**Response:**
```json
{
  "status": "ok",
  "tokens_scored": 150
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always "ok" if server is running |
| `tokens_scored` | integer | Total tokens in database |

---

## Error Handling

All API endpoints follow consistent error response formats:

### Client Errors (4xx)

```json
{
  "success": false,
  "error": "Human-readable error message"
}
```

### Server Errors (5xx)

```json
{
  "success": false,
  "error": "Internal server error"
}
```

---

## Rate Limits

ScamHound does not implement API-level rate limiting. However, external API clients have internal rate limiting:

| Client | Rate Limit | Strategy |
|--------|------------|----------|
| Birdeye | 0.5s between requests | Client-side delay + exponential backoff |
| Helius | Provider limits | Returns None on 429 |
| BubbleMaps | Provider limits | Standard request timeout |
| Bags.fm | Provider limits | 30s timeout |

---

## Data Types

### Risk Level

| Value | Score Range | Description |
|-------|-------------|-------------|
| `LOW` | 0-30 | Token shows healthy signals |
| `MEDIUM` | 31-60 | Some concerns, proceed with caution |
| `HIGH` | 61-80 | Multiple red flags, high risk |
| `CRITICAL` | 81-100 | Strong indicators of imminent rug pull |

### Token Status

| Value | Description |
|-------|-------------|
| `bonding` | Token in bonding curve phase |
| `graduated` | Token graduated from bonding curve |
| `active` | Token has liquidity and trading |
| `unknown` | Status could not be determined |

### Concentration Score

| Value | Top 1 Holder | Description |
|-------|--------------|-------------|
| `critical` | >50% | Single holder controls majority |
| `high` | >30% | High concentration |
| `moderate` | >15% | Moderate concentration |
| `low` | ≤15% | Distributed ownership |

---

## Example Usage

### cURL Examples

**Get recent scores:**
```bash
curl http://localhost:8000/api/scores?limit=10
```

**Get single token:**
```bash
curl http://localhost:8000/api/score/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
```

**Manual scan:**
```bash
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}'
```

**Save settings:**
```bash
curl -X POST http://localhost:8000/api/settings \
  -H "Content-Type: application/json" \
  -d '{
    "HELIUS_API_KEY": "your_key",
    "BAGS_API_KEY": "your_key",
    "ANTHROPIC_API_KEY": "your_key"
  }'
```

**Health check:**
```bash
curl http://localhost:8000/health
```

### Python Example

```python
import requests

BASE_URL = "http://localhost:8000"

# Get recent scores
response = requests.get(f"{BASE_URL}/api/scores", params={"limit": 10})
scores = response.json()

# Scan a token
response = requests.post(
    f"{BASE_URL}/api/scan",
    json={"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}
)
result = response.json()

if result.get("success"):
    print(f"Risk Score: {result['result']['risk_score']}")
    print(f"Verdict: {result['result']['verdict']}")
```

### JavaScript Example

```javascript
const BASE_URL = 'http://localhost:8000';

// Get recent scores
fetch(`${BASE_URL}/api/scores?limit=10`)
  .then(res => res.json())
  .then(scores => console.log(scores));

// Scan a token
fetch(`${BASE_URL}/api/scan`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
  })
})
  .then(res => res.json())
  .then(result => {
    if (result.success) {
      console.log('Risk Score:', result.result.risk_score);
      console.log('Verdict:', result.result.verdict);
    }
  });
```
