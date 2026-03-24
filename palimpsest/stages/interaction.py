from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

from palimpsest.runtime.llm import LLMGateway
from palimpsest.runtime.tools import UnifiedToolGateway


# ---------------------------------------------------------------------------
# One-shot loop warnings
# ---------------------------------------------------------------------------

@dataclass
class LoopWarning:
    """A one-shot warning injected into the agent conversation when triggered.

    Add new warning types (context budget, token budget, etc.) by creating
    additional instances and including them in _build_loop_warnings().
    """

    trigger: Callable[[int], bool]   # receives remaining iteration count
    message: Callable[[int], str]    # receives remaining iteration count
    _fired: bool = field(default=False, init=False, repr=False)

    def check(self, remaining: int) -> str | None:
        if not self._fired and self.trigger(remaining):
            self._fired = True
            return self.message(remaining)
        return None


def _build_loop_warnings(max_iterations: int) -> list[LoopWarning]:
    """Construct the active warning set for an interaction loop.

    To add a new warning type, append another LoopWarning here.
    """
    warn_at = max(1, min(5, max_iterations // 5))
    return [
        LoopWarning(
            trigger=lambda r, n=warn_at: r <= n,
            message=lambda r: (
                f"[Runtime] 你还剩 {r} 次迭代机会。"
                "如果你认为该 Job 的工作已告一段落，请尽快调用 task_complete 进行收尾，"
                "并提供 summary 说明已完成和未完成的部分。"
            ),
        ),
    ]


def run_interaction_loop(
    job_id: str,
    context: dict,
    workspace_path: str,
    llm: LLMGateway,
    tools: UnifiedToolGateway,
    max_iterations: int,
    messages: list[dict] | None = None,
    user_prompt: str | None = None,
) -> dict:
    """Core agent loop. Returns {"summary": str, "messages": list}.

    Completion is determined by the runtime:
      - Explicit task_complete tool call → interaction loop ends
      - LLM stops calling tools without task_complete → confirm once, then end
      - Max iterations reached → end
    """
    if messages is None:
        messages = [{"role": "user", "content": context["task"]}]
    else:
        messages = list(messages)

    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    asked_for_explicit_completion = False
    loop_warnings = _build_loop_warnings(max_iterations)

    for iteration in range(max_iterations):
        remaining = max_iterations - iteration - 1
        for w in loop_warnings:
            msg = w.check(remaining)
            if msg:
                logger.info(f"Loop warning injected (remaining={remaining})")
                messages.append({"role": "user", "content": msg})

        logger.debug(f"Iteration {iteration + 1}/{max_iterations}")

        response = llm.call(
            [{"role": "system", "content": context["system"]}] + messages,
            tools.schema(),
        )
        messages.append(response.raw_message)

        if not response.tool_calls:
            if not asked_for_explicit_completion:
                asked_for_explicit_completion = True
                logger.info("LLM finished without tool calls; requesting explicit task_complete")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "如果你已经完成了这个 Job 的所有工作，请显式调用 `task_complete` 并提供 `summary`。"
                            "如果尚未完成，请继续调用必要的工具推进，不要直接结束。"
                        ),
                    }
                )
                continue

            logger.info("LLM finished without task_complete after confirmation; ending loop")
            summary = (response.text or "").strip()
            if not summary:
                summary = "LLM stopped without explicit task_complete."
            return {
                "summary": summary[:500],
                "messages": messages,
            }

        for tc in response.tool_calls:
            result = tools.execute(tc.name, tc.id, tc.arguments, workspace_path)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.output})

            if result.terminal:
                if tc.name != "task_complete":
                    logger.warning(
                        f"Ignoring terminal signal from non-task_complete tool: {tc.name}"
                    )
                    continue

                logger.info("Runtime received terminal signal from task_complete")
                summary = tc.arguments.get("summary", result.output)
                return {
                    "summary": (summary or "")[:500],
                    "messages": messages,
                }

    logger.warning(f"Stopped after {max_iterations} iterations")
    return {
        "summary": f"Stopped after {max_iterations} iterations",
        "messages": messages,
    }
