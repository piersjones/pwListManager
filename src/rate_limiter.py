import time
import random
import logging
from typing import Optional, Callable

logger = logging.getLogger("pwListManager")

def throttle(seconds: float = 3.0, jitter: float = 1.0):
    """Sleep for `seconds` ± random jitter to mimic human pacing."""
    delay = seconds + random.uniform(-jitter, jitter)
    delay = max(0.5, delay)
    logger.debug(f"Throttling for {delay:.1f}s")
    time.sleep(delay)

def retry_with_backoff(
    func: Callable,
    max_retries: int = 5,
    base_delay: float = 5.0,
    max_delay: float = 80.0,
    retryable_status_codes: Optional[list] = None,
    logger_instance: Optional[logging.Logger] = None,
):
    """
    Retry a function with exponential backoff.
    On each retry, delay doubles: 5s -> 10s -> 20s -> 40s -> 80s
    If a 429 status is detected, honor Retry-After header.
    """
    if retryable_status_codes is None:
        retryable_status_codes = [429, 500, 502, 503, 504]
    if logger_instance is None:
        logger_instance = logger

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e

            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            retry_after = None

            if status_code == 429:
                resp = getattr(e, 'response', None)
                if resp and hasattr(resp, 'headers'):
                    retry_after_str = resp.headers.get('Retry-After')
                    if retry_after_str:
                        try:
                            retry_after = int(retry_after_str)
                        except ValueError:
                            pass

            if attempt >= max_retries:
                break

            if retry_after:
                delay = retry_after + random.uniform(0.5, 1.5)
                logger_instance.warning(f"Rate limited (429). Retrying after {delay:.1f}s (Retry-After={retry_after})")
            else:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 2), max_delay)
                logger_instance.warning(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay:.1f}s")

            time.sleep(delay)

    raise last_exception