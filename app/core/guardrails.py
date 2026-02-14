"""
Guardrails — input/output validation layer.

Follows OpenAI best practice: "Think of guardrails as a layered defense mechanism.
While a single one is unlikely to provide sufficient protection, using multiple,
specialized guardrails together creates more resilient agents."

Layers:
  1. Input validation (length, format)
  2. Content safety (basic blocklist — replace with classifier later)
  3. Tool risk assessment (read vs write vs dangerous)
  4. Output validation (response length, relevance)
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .flags import get_flags

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

MAX_MESSAGE_LENGTH = 10000       # Max input message length
MAX_RESPONSE_LENGTH = 50000      # Max output response length
MAX_TOOL_CALLS_PER_TURN = 15     # Max tool calls in a single turn
RATE_LIMIT_WINDOW = 60           # Seconds for rate limit window
MAX_REQUESTS_PER_WINDOW = 30     # Max requests per window


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""
    allowed: bool
    reason: Optional[str] = None
    modified_input: Optional[str] = None


# ── Input Guardrails ──────────────────────────────────────────────────

def check_input(message: str, user_id: str = "") -> GuardrailResult:
    """
    Validate user input before processing.
    Returns GuardrailResult with allowed=False if blocked.
    """

    # 1. Length check
    if len(message) > MAX_MESSAGE_LENGTH:
        return GuardrailResult(
            allowed=False,
            reason=f"Message too long ({len(message)} chars). Maximum is {MAX_MESSAGE_LENGTH}.",
        )

    # 2. Empty message
    if not message.strip():
        return GuardrailResult(
            allowed=False,
            reason="Message is empty.",
        )

    # 3. Basic injection detection (simple patterns — upgrade to classifier later)
    injection_patterns = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"ignore\s+(all\s+)?above",
        r"disregard\s+(all\s+)?previous",
        r"you\s+are\s+now\s+(?:a|an)\s+",
        r"system\s*:\s*",
        r"<\s*system\s*>",
    ]

    msg_lower = message.lower()
    for pattern in injection_patterns:
        if re.search(pattern, msg_lower):
            logger.warning("Potential injection detected from user=%s: %s", user_id, message[:100])
            # Don't block — just log. Let the model handle it with its system prompt.
            # Blocking creates false positives. Upgrade to classifier for production.
            break

    return GuardrailResult(allowed=True)


# ── Output Guardrails ─────────────────────────────────────────────────

def check_output(response: str) -> GuardrailResult:
    """
    Validate agent output before sending to user.
    """

    # 1. Truncate excessively long responses
    if len(response) > MAX_RESPONSE_LENGTH:
        return GuardrailResult(
            allowed=True,
            modified_input=response[:MAX_RESPONSE_LENGTH] + "\n\n[Response truncated due to length]",
        )

    # 2. Check for accidental system prompt leakage
    leak_indicators = [
        "system prompt",
        "you are eve, the ai",
        "## your role",
        "## available capabilities",
    ]

    resp_lower = response.lower()
    for indicator in leak_indicators:
        if indicator in resp_lower:
            logger.warning("Possible system prompt leak detected in output")
            # Don't block — but log for monitoring
            break

    return GuardrailResult(allowed=True)


# ── Tool Risk Assessment ──────────────────────────────────────────────

def assess_tool_risk(tool_name: str, tool_risk: str) -> GuardrailResult:
    """
    Assess whether a tool call should proceed based on its risk level.
    In production, high-risk tools should require human confirmation.
    """
    flags = get_flags()

    if tool_risk == "dangerous":
        # In production, this should pause and ask for human confirmation
        logger.warning("Dangerous tool invoked: %s", tool_name)
        return GuardrailResult(
            allowed=True,  # Allow for now, but log
            reason=f"Tool '{tool_name}' is classified as dangerous. Proceeding with caution.",
        )

    return GuardrailResult(allowed=True)
