import os
from unittest.mock import patch

from palimpsest.config import LLMConfig
from palimpsest.runtime.llm import LLMResponse, UnifiedLLMGateway


class _GatewayStub:
    def emit(self, event):
        return None


def test_budget_remaining_reports_all_dimensions():
    llm = UnifiedLLMGateway(
        LLMConfig(
            model="gpt-4o-mini",
            max_iterations=5,
            max_total_input_tokens=100,
            max_total_output_tokens=60,
            max_total_cost=1.0,
        ),
        _GatewayStub(),
    )
    llm.total_iterations = 2
    llm.total_input_tokens = 30
    llm.total_output_tokens = 10
    llm.total_cost = 0.25

    remaining = llm.budget_remaining()

    assert remaining["iterations"]["remaining"] == 3
    assert remaining["input_tokens"]["remaining"] == 70
    assert remaining["output_tokens"]["remaining"] == 50
    assert remaining["cost"]["remaining"] == 0.75


def test_budget_exhausted_uses_first_exhausted_dimension():
    llm = UnifiedLLMGateway(
        LLMConfig(
            model="gpt-4o-mini",
            max_iterations=2,
            max_total_input_tokens=100,
            max_total_output_tokens=60,
            max_total_cost=1.0,
        ),
        _GatewayStub(),
    )
    llm.total_iterations = 2
    llm.total_input_tokens = 100

    assert llm.budget_exhausted() == "input_tokens"


def test_budget_cost_limit_is_disabled_for_unknown_pricing():
    llm = UnifiedLLMGateway(
        LLMConfig(model="unknown-model", max_total_cost=0.1),
        _GatewayStub(),
    )

    remaining = llm.budget_remaining()

    assert remaining["cost"]["limited"] is True
    assert llm.cost_tracking_state() == "degraded"
    assert llm.total_cost == 0.0
    assert llm.budget_exhausted() is None


def test_record_usage_accumulates_tokens_and_cost():
    llm = UnifiedLLMGateway(
        LLMConfig(model="gpt-4o-mini"),
        _GatewayStub(),
    )

    llm._record_usage(
        LLMResponse(
            text="done",
            tool_calls=[],
            finish_reason="stop",
            input_tokens=2000,
            output_tokens=500,
            raw_message={},
        )
    )

    assert llm.total_iterations == 1
    assert llm.total_input_tokens == 2000
    assert llm.total_output_tokens == 500
    assert llm.total_cost > 0


def test_mock_fallback_respects_configured_api_key_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CUSTOM_OPENAI_KEY", "present")

    llm = UnifiedLLMGateway(
        LLMConfig(model="gpt-4o-mini", api_key_env="CUSTOM_OPENAI_KEY"),
        _GatewayStub(),
    )

    assert llm._mock_gateway is None


def test_degraded_cost_tracking_warns_at_initialization():
    with patch("palimpsest.runtime.llm.logger.warning") as warning:
        llm = UnifiedLLMGateway(
            LLMConfig(model="unknown-model", max_total_cost=0.5),
            _GatewayStub(),
        )

    assert llm.cost_tracking_degraded() is True
    warning.assert_any_call(
        "Cost budget configured for model 'unknown-model', but pricing is unknown; token-cost tracking is degraded"
    )


def test_iteration_penalty_rolls_into_cost_budget():
    llm = UnifiedLLMGateway(
        LLMConfig(
            model="unknown-model",
            max_iterations=2,
            iteration_penalty_cost=0.3,
            max_total_cost=0.5,
        ),
        _GatewayStub(),
    )

    response = LLMResponse(
        text="done",
        tool_calls=[],
        finish_reason="stop",
        input_tokens=0,
        output_tokens=0,
        raw_message={},
    )
    llm._record_usage(response)
    llm._record_usage(response)
    llm._record_usage(response)
    llm._record_usage(response)

    assert llm.total_cost == 0.6
    assert llm.budget_exhausted() == "cost"


def test_hard_iteration_ceiling_is_independent_backstop():
    llm = UnifiedLLMGateway(
        LLMConfig(
            model="gpt-4o-mini",
            max_iterations=50,
            max_iterations_hard=3,
            max_total_cost=10.0,
        ),
        _GatewayStub(),
    )
    llm.total_iterations = 3

    assert llm.budget_exhausted() == "max_iterations_hard"
