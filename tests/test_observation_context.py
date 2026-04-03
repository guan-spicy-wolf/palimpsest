"""Tests for observation_context context provider (ADR-0010).

Tests the real implementation from evo/contexts/loaders.py with mocked HTTP responses.
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock
import httpx
from pathlib import Path

from palimpsest.config import JobConfig, EventStoreConfig
from palimpsest.runtime.contexts import resolve_context_functions

EVO_ROOT = Path(__file__).parent.parent / "evo"


def get_observation_context_provider():
    """Load the observation_context provider from evo/contexts/loaders.py."""
    providers = resolve_context_functions(EVO_ROOT, ["observation_context"])
    return providers.get("observation_context")


class TestObservationContextProvider:
    """Test observation_context provider from evo/contexts/loaders.py."""

    def test_provider_loads_successfully(self):
        """Provider is registered and loadable."""
        observation_context = get_observation_context_provider()
        assert observation_context is not None
        assert callable(observation_context)

    def test_no_eventstore_url(self):
        """Returns empty string when no eventstore URL."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="", api_key_env=""),
        )
        result = observation_context(job_config)
        assert result == ""

    def test_successful_budget_variance_query(self):
        """Queries budget variance aggregation."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(
                url="http://localhost:8080",
                api_key_env="TEST_API_KEY",
            ),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {
            "sample_count": 10,
            "mean_variance_ratio": 0.15,
            "median_variance_ratio": 0.12,
            "underestimate_rate": 0.6,
            "overestimate_rate": 0.4,
            "total_estimated_budget": 5.0,
            "total_actual_cost": 6.5,
        }

        mock_by_role_response = Mock()
        mock_by_role_response.status_code = 200
        mock_by_role_response.json.return_value = []  # Empty list for by_role

        # Mock httpx.Client at the module level where loaders.py imports it
        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_by_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, window_hours=24)

            assert "Observation Context" in result
            assert "Budget Variance Analysis" in result
            assert "10" in result
            assert "0.150" in result  # mean_variance_ratio formatted

    def test_http_error_handling(self):
        """Handles HTTP errors gracefully."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = httpx.HTTPError("Connection failed")
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config)

            assert "[Error querying observation data:" in result

    def test_custom_window_hours(self):
        """Uses custom window hours parameter."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {"sample_count": 5}

        mock_by_role_response = Mock()
        mock_by_role_response.status_code = 200
        mock_by_role_response.json.return_value = []

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_by_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, window_hours=48)

            assert "48 hours" in result
            # Verify the window_hours was passed to the first API call (aggregate)
            first_call_args = client_instance.get.call_args_list[0]
            assert first_call_args[1]["params"]["window_hours"] == 48

    def test_role_filter_passed_to_api(self):
        """Passes role filter to budget variance aggregation API."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sample_count": 3}

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.return_value = mock_response
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, window_hours=24, role="planner")

            assert "Role filter: planner" in result
            # Verify the role was passed to the API
            call_args = client_instance.get.call_args
            assert call_args[1]["params"]["role"] == "planner"

    def test_metric_type_displayed_in_context(self):
        """Displays metric_type filter in context output."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {"sample_count": 0}

        mock_by_role_response = Mock()
        mock_by_role_response.status_code = 200
        mock_by_role_response.json.return_value = []

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_by_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, metric_type="budget_variance")

            assert "Metric filter: budget_variance" in result

    def test_by_role_breakdown_included_when_no_role_filter(self):
        """Includes by_role breakdown when not filtering by specific role."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {"sample_count": 10}

        mock_role_response = Mock()
        mock_role_response.status_code = 200
        mock_role_response.json.return_value = [
            {"role": "planner", "sample_count": 5, "mean_variance_ratio": 0.2, "total_estimated_budget": 2.0, "total_actual_cost": 2.5},
            {"role": "implementer", "sample_count": 5, "mean_variance_ratio": 0.1, "total_estimated_budget": 3.0, "total_actual_cost": 3.3},
        ]

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, window_hours=24)

            assert "By Role" in result
            assert "planner" in result
            assert "implementer" in result

    def test_by_role_breakdown_skipped_when_role_filter(self):
        """Skips by_role breakdown when filtering by specific role."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sample_count": 5}

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.return_value = mock_response
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, window_hours=24, role="planner")

            assert "By Role" not in result  # Should not include breakdown when filtering

    def test_custom_description(self):
        """Uses custom description parameter."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {"sample_count": 0}

        mock_by_role_response = Mock()
        mock_by_role_response.status_code = 200
        mock_by_role_response.json.return_value = []

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_by_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, description="Custom Analysis")

            assert "Custom Analysis" in result

    def test_metric_type_budget_variance_queries_budget_variance(self):
        """When metric_type is budget_variance, queries budget_variance endpoints."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {"sample_count": 10, "mean_variance_ratio": 0.15}

        mock_by_role_response = Mock()
        mock_by_role_response.status_code = 200
        mock_by_role_response.json.return_value = []

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_by_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, metric_type="budget_variance")

            # Should query budget_variance endpoints
            calls = client_instance.get.call_args_list
            assert any("budget_variance" in str(c) for c in calls)
            assert "Budget Variance Analysis" in result

    def test_metric_type_preparation_failure_no_endpoint(self):
        """When metric_type is preparation_failure, shows message that endpoint not implemented."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, metric_type="preparation_failure")

            # Should NOT call any endpoints (preparation_failure not implemented)
            assert client_instance.get.call_count == 0
            assert "not yet implemented" in result.lower()
            assert "Preparation Failure" in result

    def test_metric_type_tool_retry_no_endpoint(self):
        """When metric_type is tool_retry, shows message that endpoint not implemented."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, metric_type="tool_retry")

            # Should NOT call any endpoints (tool_retry not implemented)
            assert client_instance.get.call_count == 0
            assert "not yet implemented" in result.lower()
            assert "Tool Retry" in result

    def test_metric_type_none_defaults_to_budget_variance(self):
        """When metric_type is None (not specified), defaults to budget_variance."""
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        mock_agg_response = Mock()
        mock_agg_response.status_code = 200
        mock_agg_response.json.return_value = {"sample_count": 5}

        mock_by_role_response = Mock()
        mock_by_role_response.status_code = 200
        mock_by_role_response.json.return_value = []

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.get.side_effect = [mock_agg_response, mock_by_role_response]
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            result = observation_context(job_config, metric_type=None)

            # Should default to budget_variance
            assert client_instance.get.call_count == 2  # aggregate and by_role
            assert "Budget Variance Analysis" in result

    def test_unknown_metric_type_shows_warning(self):
        """Unknown metric type shows explicit warning instead of silent failure.

        Per review feedback: unknown metrics should not fail open silently.
        The context should clearly indicate the metric type is unsupported.
        """
        observation_context = get_observation_context_provider()
        job_config = JobConfig(
            eventstore=EventStoreConfig(url="http://localhost:8080"),
        )

        with patch("httpx.Client") as mock_client_class:
            client_instance = Mock()
            client_instance.close = Mock()
            mock_client_class.return_value = client_instance

            # Note: metric_type is typed as str | None in the function signature,
            # so this is testing defense-in-depth for the context provider
            result = observation_context(job_config, metric_type="unknown_metric")

            # Should NOT call any endpoints
            assert client_instance.get.call_count == 0
            # Should show explicit warning about unknown metric
            assert "Unknown Metric Type" in result
            assert "unknown_metric" in result
            assert "WARNING" in result
            assert "Supported metric types" in result