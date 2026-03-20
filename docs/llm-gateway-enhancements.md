# LLM Gateway Enhancements

## Overview

The LLM Gateway has been enhanced with exponential backoff retry, Anthropic cache control, extended generation parameters, and configurable tool timeouts.

## Configuration

### LLM Configuration

Extended `LLMConfig` with the following fields:

```python
@dataclass
class LLMConfig:
    # ... existing fields ...
    
    # Generation parameters
    max_tokens: int = 4096
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    
    # Retry configuration (exponential backoff)
    max_retries: int = 3
    retry_initial_delay: float = 1.0  # seconds
    retry_max_delay: float = 60.0  # seconds
    retry_backoff_factor: float = 2.0
    
    # Anthropic-specific cache control
    anthropic_cache_system: bool = True
    anthropic_cache_tools: bool = True
```

#### Example YAML

```yaml
llm:
  model: "claude-3-5-sonnet-20241022"
  api_key_env: "ANTHROPIC_API_KEY"
  temperature: 0.7
  max_tokens: 8192
  top_p: 0.9
  max_retries: 5
  retry_initial_delay: 1.0
  retry_max_delay: 120.0
  retry_backoff_factor: 2.0
  anthropic_cache_system: true
  anthropic_cache_tools: true
```

### Tools Configuration

Extended `ToolsConfig.builtin` to support per-tool configuration:

```yaml
tools:
  builtin:
    bash:
      timeout: 120  # seconds
      output_limit: 8192  # characters
```

## Features

### 1. Exponential Backoff Retry

All LLM calls now use exponential backoff retry with configurable parameters:

- **Initial delay**: Starting delay between retries (default: 1.0s)
- **Backoff factor**: Multiplier for each retry (default: 2.0)
- **Max delay**: Maximum delay between retries (default: 60.0s)
- **Max retries**: Maximum number of retry attempts (default: 3)

**Retryable errors**:
- Network errors (ConnectionError, TimeoutError)
- HTTP 502, 503, 504 errors
- Rate limit errors (429)
- Provider-specific connection/timeout errors

The retry logic is implemented in `palimpsest/runtime/retry_utils.py` and automatically applied to both OpenAI and Anthropic API calls.

### 2. Anthropic Cache Control

Anthropic's caching feature is now supported to reduce costs for repeated system prompts and tool definitions.

**System message caching**:
- When `anthropic_cache_system=True`, the system message is wrapped with `{"cache_control": {"type": "ephemeral"}}`
- This allows Anthropic to cache the system prompt across multiple API calls

**Tool definition caching**:
- When `anthropic_cache_tools=True`, each tool definition includes `{"cache_control": {"type": "ephemeral"}}`
- This caches tool schemas to avoid re-transmitting them

**Cost savings**: Caching can significantly reduce input token costs for multi-turn conversations with the same system prompt and tools.

### 3. Extended Generation Parameters

Additional LLM parameters are now configurable:

- `max_tokens`: Maximum tokens in the completion (required for Anthropic)
- `top_p`: Nucleus sampling parameter (OpenAI only)
- `frequency_penalty`: Penalize frequent tokens (OpenAI only)
- `presence_penalty`: Penalize new tokens (OpenAI only)

These parameters are passed through to the respective provider SDKs.

### 4. Configurable Tool Timeouts

The `bash` builtin tool now supports configurable timeouts:

```yaml
tools:
  builtin:
    bash:
      timeout: 120  # Override default 60s timeout
      output_limit: 8192  # Override default 4096 character limit
```

The timeout is enforced at the subprocess level using Python's `subprocess.run(timeout=...)`.

## Implementation Details

### Retry Utility

The `retry_with_exponential_backoff()` function in `palimpsest/runtime/retry_utils.py`:

1. Attempts the function call
2. Catches exceptions and determines if retryable
3. Calculates delay using: `delay = min(initial_delay * (backoff_factor ^ attempt), max_delay)`
4. Respects `Retry-After` headers for rate limit errors
5. Logs each retry attempt with delay information
6. Raises `RetryError` if all attempts fail

### Anthropic Message Format

When cache control is enabled, messages are formatted as:

```python
# System message with cache
system_content = [
    {
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"}
    }
]

# Tool definition with cache
tool_def = {
    "name": "tool_name",
    "description": "...",
    "input_schema": {...},
    "cache_control": {"type": "ephemeral"}  # Added when enabled
}
```

### Tool Configuration Injection

The `bash` tool now accepts a `config` parameter that is injected by `UnifiedToolGateway`:

```python
def bash(command: str, workspace: str, config: ToolsConfig | None = None) -> ToolResult:
    if config and "bash" in config.builtin:
        timeout = config.builtin["bash"].get("timeout", 60)
        output_limit = config.builtin["bash"].get("output_limit", 4096)
    # ...
```

## Migration Guide

### From Old Configuration

If you were using the old configuration format without these fields, the defaults will be applied automatically. No migration is required.

### Customizing Retry Behavior

To disable retries:
```yaml
llm:
  max_retries: 0
```

To use aggressive retry:
```yaml
llm:
  max_retries: 10
  retry_initial_delay: 0.5
  retry_max_delay: 300.0
  retry_backoff_factor: 1.5
```

### Disabling Cache Control

To disable Anthropic caching:
```yaml
llm:
  anthropic_cache_system: false
  anthropic_cache_tools: false
```

## Testing

The implementation has been verified to:
- ✅ Load extended configuration fields correctly
- ✅ Apply retry logic to both OpenAI and Anthropic calls
- ✅ Format Anthropic messages with cache_control when enabled
- ✅ Pass through generation parameters to provider SDKs
- ✅ Apply configurable timeouts to bash tool

## Future Enhancements

Potential future improvements:
- Per-provider retry configuration
- Fine-grained cache control (per-message, per-tool)
- Dynamic timeout adjustment based on task complexity
- Retry metrics and monitoring
- Circuit breaker pattern for repeated failures
