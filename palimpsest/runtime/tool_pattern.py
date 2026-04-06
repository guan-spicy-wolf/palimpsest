"""Tool pattern detection for observation signals.

Detects repetitive tool call patterns in interaction loop history,
emits observation events for optimization tasks to analyze.

Per Factorio Tool Evolution MVP: detects tool_repetition pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRecord:
    """Record of a single tool call during interaction loop."""
    name: str
    args_json: str


@dataclass
class RepetitionFinding:
    """Finding from pattern detection: repetitive tool usage."""
    tool_name: str
    call_count: int
    arg_pattern: str
    similarity: float


def detect_repetition(
    history: list[ToolCallRecord],
    *,
    min_count: int = 5,
    similarity_threshold: float = 0.7,
) -> list[RepetitionFinding]:
    """Find tools called >= min_count with high arg similarity.
    
    For dispatcher tools (like factorio_call_script), extracts nested script_name
    from args and groups by that instead of tool name.
    
    Args:
        history: List of ToolCallRecord from interaction loop
        min_count: Minimum calls to consider as repetition (default 5)
        similarity_threshold: Minimum key-set overlap to consider similar (default 0.7)
    
    Returns:
        List of RepetitionFinding for each detected pattern
    """
    # Group by (tool_name, script_name_if_dispatcher)
    groups: dict[tuple[str, str], list[dict]] = {}
    for rec in history:
        try:
            args = json.loads(rec.args_json)
        except json.JSONDecodeError:
            args = {}
        
        # If args has "name" field (dispatcher's script_name), use it for grouping
        script_name = args.get("name", "")
        key = (rec.name, script_name) if script_name else (rec.name, "")
        groups.setdefault(key, []).append(args)
    
    findings = []
    for (tool_name, script_name), args_list in groups.items():
        if len(args_list) < min_count:
            continue
        
        # Calculate parameter structure similarity (key-set overlap)
        if not args_list:
            continue
        key_sets = [set(a.keys()) for a in args_list]
        
        # Calculate pairwise Jaccard similarity average
        pair_count = len(key_sets) * (len(key_sets) - 1) // 2
        if pair_count == 0:
            avg_similarity = 1.0  # Single call, trivially similar
        else:
            total_similarity = sum(
                len(k1 & k2) / max(len(k1 | k2), 1)
                for i, k1 in enumerate(key_sets)
                for k2 in key_sets[i+1:]
            )
            avg_similarity = total_similarity / pair_count
        
        if avg_similarity >= similarity_threshold:
            # arg_pattern: if dispatcher, use script_name; otherwise use tool_name
            pattern = script_name if script_name else tool_name
            findings.append(RepetitionFinding(
                tool_name=f"{tool_name}({script_name})" if script_name else tool_name,
                call_count=len(args_list),
                arg_pattern=pattern,
                similarity=avg_similarity,
            ))
    
    return findings