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
    messages: list[dict] | None = None,
    user_prompt: str | None = None,
) -> dict:
    """Core agent loop. Returns {"status": str, "summary": str}.

    Completion is determined by the runtime:
      - Explicit task_complete tool call → success/partial
      - LLM stops calling tools without task_complete → confirm once, then partial
      - Max iterations reached → partial
    """
    if messages is None:
        messages = [{"role": "user", "content": context["task"]}]
    else:
        messages = list(messages)

    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    asked_for_explicit_completion = False

    for iteration in range(max_iterations):
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
                            "如果你已经完成任务，请显式调用 `task_complete` 并提供 `summary` 与 `status`。"
                            "如果尚未完成，请继续调用必要的工具推进，不要直接结束。"
                        ),
                    }
                )
                continue

            logger.info("LLM finished without task_complete after confirmation; marking partial")
            summary = (response.text or "").strip()
            if not summary:
                summary = "LLM stopped without explicit task_complete."
            return {"status": "partial", "summary": summary[:500], "messages": messages}

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
                status = tc.arguments.get("status", "success")
                summary = tc.arguments.get("summary", result.output)
                return {
                    "status": status,
                    "summary": (summary or "")[:500],
                    "messages": messages,
                }

    logger.warning(f"Stopped after {max_iterations} iterations")
    return {
        "status": "partial",
        "summary": f"Stopped after {max_iterations} iterations",
        "messages": messages,
    }
