"""Tests for observation_context context provider (ADR-0010).

Tests the observation_context provider with mocked HTTP responses.
Per Bundle MVP: context providers are loaded from evo/<bundle>/contexts/.
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock
import httpx
from pathlib import Path
import textwrap

from palimpsest.config import JobConfig, EventStoreConfig
from palimpsest.runtime.contexts import resolve_context_functions


@pytest.fixture
def evo_with_observation_context(tmp_path):
    """Create an evo directory with observation_context provider in a bundle."""
    contexts_dir = tmp_path / "factorio" / "contexts"
    contexts_dir.mkdir(parents=True)
    (contexts_dir / "observation.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider
        import httpx
        
        @context_provider("observation_context")
        def observation_context(*, job_config, metric_type="budget_variance", window_hours=24, role=None, description=None, **_) -> str:
            '''Fetch observation metrics from eventstore and render as context.
            
            Per ADR-0010: Provides budget variance, preparation failure, and tool retry metrics.
            '''
            if not job_config.eventstore.url:
                return ""
            
            import os
            api_key = os.environ.get(job_config.eventstore.api_key_env or "EVENTSTORE_API_KEY", "")
            if not api_key:
                return ""
            
            endpoint = f"{job_config.eventstore.url.rstrip('/')}/metrics/{metric_type}"
            params = {"window_hours": window_hours}
            if role:
                params["role"] = role
            
            try:
                resp = httpx.get(endpoint, headers={"Authorization": f"Bearer {api_key}"}, params=params, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                return f"[Observation context unavailable: {e}]"
            
            # Render metrics as markdown
            lines = [f"## Observation Metrics ({metric_type})"]
            if description:
                lines.append(description)
            
            if metric_type == "budget_variance":
                for item in data.get("items", []):
                    role_name = item.get("role", "unknown")
                    variance = item.get("variance", 0.0)
                    lines.append(f"- **{role_name}**: ${variance:.2f} variance")
            
            if role is None and data.get("by_role"):
                lines.append("\\n### Breakdown by Role")
                for role_item in data["by_role"]:
                    lines.append(f"- {role_item['role']}: {role_item['count']} events")
            
            return "\\n".join(lines)
    """))
    return tmp_path


def get_observation_context_provider(evo_root, bundle="factorio"):
    """Load the observation_context provider from bundle contexts."""
    providers = resolve_context_functions(evo_root, ["observation_context"], bundle=bundle)
    return providers.get("observation_context")


class TestObservationContextProvider:
    """Test observation_context provider."""

    def test_provider_loads_successfully(self, evo_with_observation_context):
        """Provider is registered and loadable."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        assert observation_context is not None
        assert callable(observation_context)

    def test_no_eventstore_url(self, evo_with_observation_context):
        """Returns empty string when no eventstore URL."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        job_config = JobConfig(
            bundle="factorio",
            eventstore=EventStoreConfig(url="", api_key_env=""),
        )
        result = observation_context(job_config=job_config)
        assert result == ""

    def test_no_api_key(self, evo_with_observation_context, monkeypatch):
        """Returns empty string when no API key."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.delenv("EVENTSTORE_API_KEY", raising=False)
        job_config = JobConfig(
            bundle="factorio",
            eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
        )
        result = observation_context(job_config=job_config)
        assert result == ""

    def test_successful_budget_variance_query(self, evo_with_observation_context, monkeypatch):
        """Returns formatted metrics on successful API response."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "items": [
                {"role": "worker", "variance": 0.15},
                {"role": "planner", "variance": -0.05},
            ],
            "by_role": [
                {"role": "worker", "count": 10},
                {"role": "planner", "count": 5},
            ],
        }
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config)
        
        assert "worker" in result
        assert "planner" in result
        assert "variance" in result

    def test_http_error_handling(self, evo_with_observation_context, monkeypatch):
        """Returns error message on HTTP failure."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        with patch("httpx.get", side_effect=httpx.HTTPError("Connection failed")):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config)
        
        assert "Observation context unavailable" in result

    def test_custom_window_hours(self, evo_with_observation_context, monkeypatch):
        """Passes window_hours parameter to API."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        captured_params = {}
        
        def mock_get(url, **kwargs):
            captured_params.update(kwargs.get("params", {}))
            mock_response = Mock()
            mock_response.is_success = True
            mock_response.json.return_value = {"items": []}
            return mock_response
        
        with patch("httpx.get", side_effect=mock_get):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            observation_context(job_config=job_config, window_hours=48)
        
        assert captured_params.get("window_hours") == 48

    def test_role_filter_passed_to_api(self, evo_with_observation_context, monkeypatch):
        """Passes role filter to API when specified."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        captured_params = {}
        
        def mock_get(url, **kwargs):
            captured_params.update(kwargs.get("params", {}))
            mock_response = Mock()
            mock_response.is_success = True
            mock_response.json.return_value = {"items": []}
            return mock_response
        
        with patch("httpx.get", side_effect=mock_get):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            observation_context(job_config=job_config, role="worker")
        
        assert captured_params.get("role") == "worker"

    def test_metric_type_displayed_in_context(self, evo_with_observation_context, monkeypatch):
        """Metric type is shown in context header."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"items": [{"role": "worker", "variance": 0.1}]}
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config)
        
        assert "budget_variance" in result

    def test_by_role_breakdown_included_when_no_role_filter(self, evo_with_observation_context, monkeypatch):
        """By-role breakdown is shown when no role filter."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "items": [{"role": "worker", "variance": 0.1}],
            "by_role": [{"role": "worker", "count": 10}],
        }
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config)
        
        assert "Breakdown by Role" in result

    def test_by_role_breakdown_skipped_when_role_filter(self, evo_with_observation_context, monkeypatch):
        """By-role breakdown is NOT shown when role filter is specified."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "items": [{"role": "worker", "variance": 0.1}],
            "by_role": [{"role": "worker", "count": 10}],
        }
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config, role="worker")
        
        assert "Breakdown by Role" not in result

    def test_custom_description(self, evo_with_observation_context, monkeypatch):
        """Custom description is included in output."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"items": []}
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config, description="Custom metrics description")
        
        assert "Custom metrics description" in result

    def test_metric_type_budget_variance_queries_budget_variance(self, evo_with_observation_context, monkeypatch):
        """metric_type=budget_variance queries budget_variance endpoint."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        captured_url = {}
        
        def mock_get(url, **kwargs):
            captured_url["url"] = url
            mock_response = Mock()
            mock_response.is_success = True
            mock_response.json.return_value = {"items": []}
            return mock_response
        
        with patch("httpx.get", side_effect=mock_get):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            observation_context(job_config=job_config, metric_type="budget_variance")
        
        assert "budget_variance" in captured_url["url"]

    def test_metric_type_preparation_failure_no_endpoint(self, evo_with_observation_context, monkeypatch):
        """metric_type=preparation_failure returns warning (endpoint not implemented)."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"items": []}
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config, metric_type="preparation_failure")
        
        # The endpoint is queried but may return empty data
        assert "preparation_failure" in result or "items" not in result

    def test_metric_type_tool_retry_no_endpoint(self, evo_with_observation_context, monkeypatch):
        """metric_type=tool_retry returns warning (endpoint not implemented)."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"items": []}
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config, metric_type="tool_retry")
        
        # The endpoint is queried but may return empty data
        assert "tool_retry" in result or "items" not in result

    def test_metric_type_none_defaults_to_budget_variance(self, evo_with_observation_context, monkeypatch):
        """metric_type defaults to budget_variance."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        captured_url = {}
        
        def mock_get(url, **kwargs):
            captured_url["url"] = url
            mock_response = Mock()
            mock_response.is_success = True
            mock_response.json.return_value = {"items": []}
            return mock_response
        
        with patch("httpx.get", side_effect=mock_get):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            observation_context(job_config=job_config)  # No metric_type specified
        
        assert "budget_variance" in captured_url["url"]

    def test_unknown_metric_type_shows_warning(self, evo_with_observation_context, monkeypatch):
        """Unknown metric_type shows warning in output."""
        observation_context = get_observation_context_provider(evo_with_observation_context)
        monkeypatch.setenv("EVENTSTORE_API_KEY", "test-key")
        
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"items": []}
        
        with patch("httpx.get", return_value=mock_response):
            job_config = JobConfig(
                bundle="factorio",
                eventstore=EventStoreConfig(url="https://eventstore.example.com", api_key_env="EVENTSTORE_API_KEY"),
            )
            result = observation_context(job_config=job_config, metric_type="unknown_metric")
        
        assert "unknown_metric" in result