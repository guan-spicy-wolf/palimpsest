from __future__ import annotations

# ---------------------------------------------------------------------------
# Architecture note: Why idle detection instead of a task_complete tool?
#
# An earlier design had an explicit `task_complete` tool that agents called
# to signal they were done. This was removed (ADR-0002) because:
#
# 1. It conflated Task and Job semantics — an agent saying "I'm done" is
#    a Job-level observation, but callers read it as a Task-level statement.
# 2. Model self-reporting is unreliable — agents frequently claim completion
#    prematurely or forget to call the tool entirely.
# 3. Idle detection (two consecutive no-tool-call LLM responses) moves the
#    exit decision to the runtime, which observes behavior rather than
#    trusting self-assessment. The first idle response becomes the candidate
#    summary; a confirmation prompt gives the agent one chance to resume.
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from loguru import logger

from palimpsest.runtime.llm import LLMGateway
from palimpsest.runtime.tools import UnifiedToolGateway

if TYPE_CHECKING:
    from palimpsest.runtime.context import RuntimeContext


# ---------------------------------------------------------------------------
# One-shot loop warnings
# ---------------------------------------------------------------------------

@dataclass
class LoopWarning:
    """A one-shot warning injected into the agent conversation when triggered.

    Warnings now operate on the full budget snapshot exposed by the LLM
    gateway, so they can react to iterations, token budgets, or cost.
    """

    trigger: Callable[[dict], bool]
    message: Callable[[dict], str]
    _fired: bool = field(default=False, init=False, repr=False)

    def check(self, budget: dict) -> str | None:
        if not self._fired and self.trigger(budget):
            self._fired = True
            return self.message(budget)
        return None


def _dimension(budget: dict, name: str) -> dict:
    return budget.get(name, {})


def _limited_remaining(budget: dict, name: str) -> tuple[int | float | None, int | float | None]:
    dimension = _dimension(budget, name)
    if not dimension.get("limited"):
        return None, None
    return dimension.get("remaining"), dimension.get("limit")


def _near_fractional_limit(
    budget: dict,
    name: str,
    *,
    fraction: float = 0.2,
) -> bool:
    remaining, limit = _limited_remaining(budget, name)
    if remaining is None or limit in (None, 0):
        return False
    return remaining <= (limit * fraction)


def _build_loop_warnings() -> list[LoopWarning]:
    return [
        LoopWarning(
            trigger=lambda budget: (
                (remaining := _limited_remaining(budget, "iterations")[0]) is not None
                and remaining <= max(1, min(5, int(_limited_remaining(budget, "iterations")[1] or 0) // 5))
            ),
            message=lambda budget: (
                f"[Runtime] 你还剩 {_dimension(budget, 'iterations').get('remaining')} 次 LLM 调用预算。"
                "如果还需要继续工作，就继续调用工具；如果工作已经完成，请停止调用工具并自然收尾。"
            ),
        ),
        LoopWarning(
            trigger=lambda budget: _near_fractional_limit(budget, "input_tokens"),
            message=lambda budget: (
                f"[Runtime] 你的累计输入 token 预算只剩 {_dimension(budget, 'input_tokens').get('remaining')}。"
                "请优先收敛上下文，避免继续展开无关操作。"
            ),
        ),
        LoopWarning(
            trigger=lambda budget: _near_fractional_limit(budget, "output_tokens"),
            message=lambda budget: (
                f"[Runtime] 你的累计输出 token 预算只剩 {_dimension(budget, 'output_tokens').get('remaining')}。"
                "请减少冗长说明，优先完成剩余关键操作。"
            ),
        ),
        LoopWarning(
            trigger=lambda budget: _near_fractional_limit(budget, "cost"),
            message=lambda budget: (
                f"[Runtime] 你的成本预算只剩 ${(_dimension(budget, 'cost').get('remaining') or 0):.4f}。"
                "请快速收尾，避免继续产生非必要调用。"
            ),
        ),
    ]


def _budget_exhausted_summary(reason: str, budget: dict, candidate_summary: str | None) -> str:
    if candidate_summary:
        return candidate_summary

    reason_messages = {
        "max_iterations": "LLM call budget exhausted before the next interaction step.",
        "max_iterations_hard": "Hard iteration ceiling exhausted before the next interaction step.",
        "input_tokens": "Input token budget exhausted before the next interaction step.",
        "output_tokens": "Output token budget exhausted before the next interaction step.",
        "cost": "Cost budget exhausted before the next interaction step.",
    }
    detail = _dimension(
        budget,
        {
            "max_iterations": "iterations",
            "max_iterations_hard": "iterations_hard",
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "cost": "cost",
        }.get(reason, ""),
    )
    used = detail.get("used")
    limit = detail.get("limit")
    if limit is not None:
        return f"{reason_messages.get(reason, 'Budget exhausted.')} Used {used}/{limit}."
    return reason_messages.get(reason, "Budget exhausted.")


def run_interaction_loop(
    job_id: str,
    context: dict,
    workspace_path: str,
    llm: LLMGateway,
    tools: UnifiedToolGateway,
    messages: list[dict] | None = None,
    user_prompt: str | None = None,
    runtime_context: RuntimeContext | None = None,
) -> dict:
    """Core agent loop. Returns {"summary": str, "status": str, "code": str, "messages": list}.

    Completion is determined by the runtime:
      - LLM stops calling tools → confirm once, then end using the first idle summary
      - Any enforced budget is exhausted → end with status=partial, code=budget_exhausted

    ADR-0011: runtime_context is passed to tools.execute() for injection into tool calls.
    """
    if messages is None:
        messages = [{"role": "user", "content": context["task"]}]
    else:
        messages = list(messages)

    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    idle_confirmation_pending = False
    candidate_summary: str | None = None
    loop_warnings = _build_loop_warnings()

    while True:
        budget_reason = llm.budget_exhausted()
        budget = llm.budget_remaining()
        if budget_reason:
            logger.warning(f"Stopping interaction loop due to budget exhaustion: {budget_reason}")
            return {
                "summary": _budget_exhausted_summary(
                    budget_reason, budget, candidate_summary
                )[:500],
                "status": "partial",
                "code": "budget_exhausted",
                "budget_dim": budget_reason,
                "messages": messages,
            }

        for w in loop_warnings:
            msg = w.check(budget)
            if msg:
                logger.info("Loop warning injected based on remaining budget")
                messages.append({"role": "user", "content": msg})

        logger.debug(
            "Calling LLM "
            f"(next_iteration={_dimension(budget, 'iterations').get('used', 0) + 1})"
        )

        response = llm.call(
            [{"role": "system", "content": context["system"]}] + messages,
            tools.schema(),
        )
        messages.append(response.raw_message)

        if not response.tool_calls:
            response_summary = (response.text or "").strip()
            if not idle_confirmation_pending:
                candidate_summary = response_summary or "LLM stopped calling tools."
                idle_confirmation_pending = True
                logger.info("LLM returned no tool calls; requesting idle confirmation")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "如果你还有工作要做，请继续调用必要的工具。"
                            "如果没有更多工作，这个 Job 将在下一次无工具响应后结束。"
                        ),
                    }
                )
                continue

            logger.info("LLM remained idle after confirmation; ending loop")
            summary = candidate_summary or response_summary or "LLM stopped calling tools."
            return {
                "summary": summary[:500],
                "status": "complete",
                "code": "",
                "messages": messages,
            }

        idle_confirmation_pending = False
        candidate_summary = None
        for tc in response.tool_calls:
            result = tools.execute(tc.name, tc.id, tc.arguments, workspace_path, runtime_context=runtime_context)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.output})
