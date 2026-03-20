"""Retry utilities with exponential backoff.

Handles retry logic for LLM calls with configurable backoff strategy.
"""

import time
from typing import Any, Callable, TypeVar

from loguru import logger

T = TypeVar("T")


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""
    pass


def retry_with_exponential_backoff(
    func: Callable[..., T],
    max_retries: int,
    initial_delay: float,
    max_delay: float,
    backoff_factor: float,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute a function with exponential backoff retry.
    
    Args:
        func: Function to execute
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for delay on each retry
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Result of func
        
    Raises:
        RetryError: If all retries are exhausted
        Any exception from the last attempt
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exception = exc
            
            # Check if this is a retryable error
            if not _is_retryable_error(exc):
                logger.warning(f"Non-retryable error on attempt {attempt + 1}: {exc}")
                raise
            
            if attempt < max_retries:
                # Calculate delay with exponential backoff
                delay = min(
                    initial_delay * (backoff_factor ** attempt),
                    max_delay
                )
                
                # If it's a rate limit error, respect the retry-after header if available
                retry_after = _extract_retry_after(exc)
                if retry_after:
                    delay = max(delay, retry_after)
                
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries + 1} failed: {exc}. "
                    f"Retrying in {delay:.2f}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"All {max_retries + 1} attempts failed. Last error: {exc}")
    
    # Should never reach here, but for type safety
    raise RetryError(f"Failed after {max_retries + 1} attempts") from last_exception


def _is_retryable_error(exc: Exception) -> bool:
    """Determine if an exception is retryable."""
    # Network errors
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    
    # Generic network-related errors
    if isinstance(exc, (OSError, IOError)):
        error_str = str(exc).lower()
        if any(keyword in error_str for keyword in ["timeout", "connection", "network", "broken pipe", "reset"]):
            return True
    
    # Check for common HTTP error patterns
    error_str = str(exc).lower()
    
    # OpenAI errors
    if "openai" in type(exc).__module__.lower():
        # 502, 503, 504 are retryable
        if any(code in error_str for code in ["502", "503", "504", "timeout"]):
            return True
        # Rate limit errors should be retried with delay
        if "rate limit" in error_str or "429" in error_str:
            return True
        # API connection errors
        if "connection" in error_str or "timeout" in error_str:
            return True
    
    # Anthropic errors
    if "anthropic" in type(exc).__module__.lower():
        if any(code in error_str for code in ["502", "503", "504"]):
            return True
        if "rate limit" in error_str or "429" in error_str:
            return True
        if "timeout" in error_str or "connection" in error_str:
            return True
    
    # Generic retryable patterns
    if any(keyword in error_str for keyword in ["timeout", "connection", "network"]):
        return True
    
    # By default, retry all exceptions during testing/development
    # In production, you might want to be more selective
    return True


def _extract_retry_after(exc: Exception) -> float | None:
    """Extract retry-after delay from exception if available."""
    # Try to extract from exception attributes
    if hasattr(exc, "response") and hasattr(exc.response, "headers"):
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    
    # Try to parse from error message
    error_str = str(exc)
    if "retry after" in error_str.lower():
        # Simple extraction - could be improved with regex
        import re
        match = re.search(r"retry after[^\d]*(\d+)", error_str, re.IGNORECASE)
        if match:
            return float(match.group(1))
    
    return None
