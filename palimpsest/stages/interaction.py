from __future__ import annotations

from loguru import logger

from palimpsest.gateway.llm import LLMGateway
from palimpsest.gateway.tools import ToolGateway


def run_interaction_loop(
    job_id: str,
    context: dict,
    workspace_path: str,
    llm: LLMGateway,
    tools: ToolGateway,
    max_iterations: int,
) -> dict:
    """Core agent loop. Returns {"status": str, "summary": str}.

    Completion is determined by the runtime:
      - LLM stops calling tools → success
      - Max iterations reached → partial
    """
    messages = [{"role": "user", "content": context["task"]}]

    for iteration in range(max_iterations):
        logger.debug(f"Iteration {iteration + 1}/{max_iterations}")

        response = llm.call(
            [{"role": "system", "content": context["system"]}] + messages,
            tools.schema(),
        )
        messages.append(response.raw_message)

        if not response.tool_calls:
            logger.info("LLM finished without tool calls")
            return {"status": "success", "summary": (response.text or "")[:500]}

        for tc in response.tool_calls:
            result = tools.execute(tc.name, tc.id, tc.arguments, workspace_path)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.output})

    logger.warning(f"Stopped after {max_iterations} iterations")
    return {"status": "partial", "summary": f"Stopped after {max_iterations} iterations"}

