"""
ScamHound Retry Utility
Shared retry logic with exponential backoff and jitter for all API clients.
"""

import time
import random
import logging
import requests

logger = logging.getLogger(__name__)


def request_with_retry(method, url, max_retries=3, base_delay=1.0, max_delay=30.0, **kwargs):
    """
    Make an HTTP request with exponential backoff and jitter.
    Retries on: 429 (rate limit), 500, 502, 503, 504 (server errors), connection errors.
    
    Args:
        method: requests.get or requests.post
        url: The URL to request
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        **kwargs: Additional arguments passed to requests (headers, params, json, timeout, etc.)
    
    Returns:
        Response object from the final request attempt
    
    Raises:
        requests.ConnectionError: If all retries exhausted on connection error
        requests.Timeout: If all retries exhausted on timeout
    """
    for attempt in range(max_retries + 1):
        try:
            response = method(url, **kwargs)
            
            # Success or client error (not retryable)
            if response.status_code < 500 and response.status_code != 429:
                return response
            
            # Retryable error
            if attempt < max_retries:
                # Check for Retry-After header
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    delay = float(retry_after)
                else:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    delay += random.uniform(0, delay * 0.1)  # jitter
                
                logger.warning(
                    f"Request to {url} returned {response.status_code}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
            else:
                return response  # Return last response even if failed
                
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                delay += random.uniform(0, delay * 0.1)
                logger.warning(
                    f"Connection error to {url}: {e}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
            else:
                raise
    
    return None  # Should not reach here
