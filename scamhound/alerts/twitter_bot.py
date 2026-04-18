"""
ScamHound Twitter Bot
Posts formatted risk alerts to @ScamHoundCrypto
"""

import os
import logging
import time
from typing import Dict, Any, List

import tweepy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Twitter API credentials
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

RISK_THRESHOLD = int(os.getenv("RISK_ALERT_THRESHOLD", "65"))

# Initialize Twitter API client (OAuth 1.0a for posting tweets)
api = None
client = None


def _init_twitter():
    """Initialize Twitter API client."""
    global api, client
    
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        logger.warning("[TWITTER] Missing credentials - Twitter alerts disabled")
        return False
    
    try:
        # OAuth 1.0a authentication
        auth = tweepy.OAuthHandler(TWITTER_API_KEY, TWITTER_API_SECRET)
        auth.set_access_token(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
        api = tweepy.API(auth)
        
        # OAuth 2.0 client for v2 API
        client = tweepy.Client(
            bearer_token=TWITTER_BEARER_TOKEN,
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET
        )
        
        # Verify credentials
        api.verify_credentials()
        logger.info("[TWITTER] Authentication successful")
        return True
    except Exception as e:
        logger.error(f"[TWITTER] Authentication failed: {e}")
        return False


twitter_enabled = _init_twitter()


def format_tweet(token: Dict[str, Any]) -> str:
    """
    Format a tweet for a high-risk token.
    
    Returns a tweet text under 280 characters.
    """
    symbol = token.get("symbol", "???")
    name = token.get("name", "Unknown")
    score = token.get("risk_score", 0)
    risk_level = token.get("risk_level", "HIGH")
    token_mint = token.get("token_mint", "")
    
    concentration = token.get("top_10_concentration", 0)
    wallet_age = token.get("wallet_age_days", -1)
    prior_launches = token.get("prior_launches", 0)
    verdict = token.get("ai_verdict") or token.get("verdict", "")
    
    risk_factors = token.get("top_risk_factors", [])
    
    # Format based on risk level
    if risk_level == "CRITICAL":
        # Critical alert format
        tweet = f"🚨🚨 CRITICAL RUG PULL WARNING — ${symbol} on @BagsApp\n\n"
        tweet += f"💀 Risk Score: {score}/100 — CRITICAL\n"
        
        if risk_factors and len(risk_factors) > 0:
            tweet += f"🔴 {risk_factors[0][:50]}\n"
        if len(risk_factors) > 1:
            tweet += f"🔴 {risk_factors[1][:50]}\n"
        
        # Add verdict (truncated)
        if verdict:
            verdict_short = verdict[:80] + "..." if len(verdict) > 80 else verdict
            tweet += f"\n{verdict_short}\n"
        
        tweet += f"\n🐕 ScamHound | @DeFiAuditCCIE | bags.fm/{token_mint[:8]}\n"
        tweet += "#RugPull #Solana #ScamHound"
        
    else:
        # High risk format
        tweet = f"🚨 HIGH RISK ALERT — ${symbol} on @BagsApp\n\n"
        tweet += f"⚠️ Risk Score: {score}/100\n"
        tweet += f"📊 Top 10 holders: {concentration:.1f}% of supply\n"
        
        if wallet_age >= 0:
            tweet += f"👛 Creator wallet age: {wallet_age} days\n"
        
        if prior_launches > 0:
            tweet += f"🔗 Prior launches: {prior_launches}\n"
        
        # Add first sentence of verdict
        if verdict:
            first_sentence = verdict.split(". ")[0][:60]
            tweet += f"\n{first_sentence}.\n"
        
        tweet += f"\n🐕 ScamHound by @DeFiAuditCCIE | bags.fm/{token_mint[:8]}\n"
        tweet += "#Solana #CryptoSecurity #ScamHound"
    
    # Ensure tweet is under 280 characters
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."
    
    return tweet


def post_tweet(text: str) -> bool:
    """
    Post a tweet.
    
    Returns True on success, False on failure.
    """
    global client
    
    if not twitter_enabled or client is None:
        logger.warning("[TWITTER] Cannot post - Twitter not enabled")
        return False
    
    try:
        response = client.create_tweet(text=text)
        
        if response.data:
            tweet_id = response.data.get("id")
            logger.info(f"[TWITTER] Tweet posted successfully: {tweet_id}")
            return True
        else:
            logger.error("[TWITTER] Tweet post failed - no response data")
            return False
            
    except tweepy.TweepyException as e:
        logger.error(f"[TWITTER] Tweet post failed: {e}")
        return False
    except Exception as e:
        logger.error(f"[TWITTER] Unexpected error: {e}")
        return False


def send_pending_alerts() -> None:
    """
    Send tweets for all high-risk tokens that haven't been tweeted yet.
    """
    from engine import database
    
    if not twitter_enabled:
        logger.info("[TWITTER] Skipping alerts - Twitter not enabled")
        return
    
    # Get unnotified high-risk tokens
    high_risk_tokens = database.get_high_risk_unnotified(threshold=RISK_THRESHOLD)
    
    if not high_risk_tokens:
        logger.info("[TWITTER] No new high-risk tokens to alert")
        return
    
    logger.info(f"[TWITTER] Found {len(high_risk_tokens)} tokens to alert")
    
    for token in high_risk_tokens:
        token_mint = token.get("token_mint")
        symbol = token.get("symbol", "???")
        
        # Format the tweet
        tweet_text = format_tweet(token)
        
        logger.info(f"[TWITTER] Posting alert for ${symbol}...")
        
        # Post the tweet
        success = post_tweet(tweet_text)
        
        if success:
            # Mark as tweeted
            database.mark_tweet_sent(token_mint)
            logger.info(f"[TWITTER] Alert sent for ${symbol}")
        else:
            logger.warning(f"[TWITTER] Failed to send alert for ${symbol}")
        
        # Rate limit delay
        time.sleep(5)


def test_tweet() -> bool:
    """Test Twitter integration by posting a test tweet."""
    test_text = "🐕 ScamHound is watching... Test alert from the rug pull detection system. #Solana #ScamHound"
    return post_tweet(test_text)