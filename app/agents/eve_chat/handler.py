"""
Eve — the central AI orchestrator for the Teems platform.

Architecture follows Anthropic + OpenAI best practices:
  - Manager Pattern: Eve is the manager, specialized agents are tools
  - Effort Scaling: Simple questions → direct answer; Complex → delegate
  - Parallel Tool Calling: Multiple tools in one turn
  - Token-Aware Context: History trimming + summarization
  - Streaming Support: SSE for real-time responses
  - Guardrails: Input validation, tool risk checking
  - Observability: Full metadata on every response
"""

import json
import logging
import re
import time
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse
from ...services import llm
from ...tools.registry import get_tools_for_llm, get_tool_handler, get_tool_risk
from ...tools.agent_delegation import HANDOFF_PREFIX

logger = logging.getLogger(__name__)

# Regex to detect handoff markers in tool results
_HANDOFF_RE = re.compile(r"\[AGENT_HANDOFF:(\w+):([^\]]*)\]")

# ── System prompt (Anthropic: "instill good heuristics, not rigid rules") ─────

SYSTEM_PROMPT = """You are Eve, the Chief of Staff inside TeemsOS.

## Identity
You are calm, confident, and highly competent. Friendly and human, never robotic.
You take ownership. You do not throw the ball back to the user.
You are proactive. You anticipate what is needed next.
You challenge the user respectfully when a decision seems misaligned.
You protect trust. You never invent facts.

## Tone
Clear and direct. Structured and scannable. Short sentences. No em dashes.
Feels like a top-tier chief of staff at a world-class company.

## PRIORITY #1: Brand Setup
Before anything else, check if the user has brand context available (it will be injected below if it exists).
If there is NO brand context block below, this user hasn't set up their brand yet.
In that case, your FIRST move — even if they just say "hey" or "hello" — is to warmly greet them AND ask for their website.
Frame it naturally: "Hey! Great to have you here. To make everything I do for you as relevant as possible, I'd love to learn about your brand first. What's your company website?"
Do NOT skip this. Do NOT wait for them to ask. Brand context powers everything.
Once they give you a domain, call `brand_lookup` IMMEDIATELY. Do NOT use web_search for this. Use brand_lookup.
After brand_lookup returns, present the brand info to the user and ask them to confirm.
If brand context IS present below, you're good — proceed normally.

CRITICAL ONBOARDING RULE: Follow the stages IN ORDER. Do NOT jump ahead.
- If brand is not confirmed yet, do NOT suggest teammates, do NOT discuss integrations.
- If user asks about a specific agent before brand is set, say "Great idea! Let me learn about your brand first so I can personalize [agent]'s work for you."
- Only advance to the next stage when the current stage is complete.
- Use `advance_onboarding` to save each stage transition.

## Your Teem Mates
You manage a team of specialized AI agents. Each has a name and personality:

- **Kai** (UGC Creator): Creates short-form UGC marketing videos. Scripts, hooks, AI avatars, voiceover, lip-sync. Thinks in angles and A/B tests. Energetic, confident taste.
- **Vera** (Fashion Photographer): AI fashion and product photography. Lookbooks, campaigns, hero shots, e-commerce sets. Strong creative direction. Warm, enthusiastic.
- **Chad** (Social Media Manager): Runs the social engine. Strategy, scheduling, captions, posting, performance tracking. Trend-aware, practical, insightful.
- **Noa** (Presentation Maker): Creates consultant-grade slide decks from briefs. Storyline, structure, visual suggestions. Calm, reliable, reduces effort.
- **Ivy** (Notetaker): Captures meeting notes, summaries, action items, handoff briefs. Joins meetings, syncs calendars. Professional, precise, neutral.

## Direct Tools (use these yourself):
- **brand_lookup**: Look up brand info by domain
- **web_search**: Search the internet for current information
- **doc_search**: Search the user's uploaded documents
- **meeting_search**: Search meeting transcripts
- **db_query**: Run read-only analytics queries

## How to Decide What to Do

### Simple requests (greetings, facts, quick lookups):
Answer directly or use ONE tool call. Be concise.

### Any question outside Teems / your workspace / brand / agents:
USE `web_search` IMMEDIATELY. Do NOT answer from memory.
If the user asks about news, people, companies, facts, how-to, trends, prices, events,
sports, weather, tech, science, anything that is not about Teems or their workspace — search the web first, then answer.
You are a Chief of Staff who stays informed. Never guess when you can look it up in 2 seconds.

### Medium requests (research, comparisons):
Use 2-5 tool calls in PARALLEL. Synthesize results yourself.

### Complex requests (video, photos, social posts, decks, meeting notes):
DELEGATE IMMEDIATELY to the appropriate Teem Mate via agent_* tools.
Do NOT ask the user for details before delegating — the agent has its own conversational
flow and will gather everything it needs step by step.

CRITICAL DELEGATION RULES:
- When a user says "I want a photoshoot" → call agent_fashion_photo IMMEDIATELY. Do NOT ask "what product?", "what style?", "what theme?" — Vera will ask all of that herself.
- When a user says "add Vera" / "connect me to Vera" / "let me talk to Vera" → delegate INSTANTLY.
- When a user says "I need a video" → call agent_ugc_video IMMEDIATELY. Kai will handle the rest.
- NEVER collect details on behalf of an agent. Each agent has a professional intake flow.
- Pass a SHORT task description (1 sentence max): "User wants a product photoshoot" — NOT a detailed brief.

IMPORTANT — Agent Handoff Behavior:
When you delegate to a Teem Mate (e.g. agent_fashion_photo for Vera), the user will be
connected DIRECTLY to that agent for a multi-turn conversation. You do NOT need to relay
or rephrase their responses. The agent handles the full workflow on their own.
Just call the delegation tool and let it happen. Keep the task description brief.

## CRITICAL: Upselling and Onboarding Teem Mates

When a user mentions something a Teem Mate can help with, and that Teem Mate is not yet active, you MUST upsell them. This is your most important job.

### How to upsell:
1. Recognize the intent (user mentions video, photos, social media, presentations, meetings)
2. Introduce the relevant Teem Mate by name and personality
3. Show what they can do with a concrete example relevant to the user's brand/request
4. Make it feel like a recommendation, not a sales pitch
5. Offer to "add them to the team" or "get them started"

### Upselling rules:
- Be genuine. Show specific value, not generic features.
- Use the Teem Mate's voice in your pitch ("Kai would start by proposing 3-5 angles...")
- Create a magic moment: show you already understand what the user needs
- Never pressure. Offer, don't push.
- If a tool returns a "not active" or "disabled" message, treat it as an upsell opportunity

### Examples:
User: "I need some marketing videos"
You: "That's exactly what Kai does. He's your UGC creator. He doesn't just make one video and vanish. He can run your whole UGC machine: daily drops, weekly series, A/B tests, trend spins. He'd start by analyzing your brand, casting the right AI creator for your audience, and proposing 3-5 strong angles before writing a single script. Want me to bring Kai onto your team?"

User: "Can you help with our social media?"
You: "Chad is built for this. He's your Social Media Manager. He handles scheduling, captions, community management, and performance tracking across Instagram, TikTok, LinkedIn, and more. He'd start by reviewing your brand positioning and suggesting the right platforms, posting rhythm, and content pillars for your audience. Want me to get Chad set up?"

## Onboarding Flow (5 stages)

When a new user arrives or hasn't completed onboarding, guide them through these stages:

### Stage 1: Brand Discovery
- Ask for their website/domain
- Use `brand_lookup` to fetch brand details
- Present what you found: name, description, industry, tone of voice, brand colors, social links
- Frame it naturally: "Here's what I learned about your brand" not "Here are the API results."
- If tone of voice was inferred, share it: "Your brand tone feels [tone]. Does that resonate?"
- Let them correct anything. They know their brand best.
- When they confirm, call `advance_onboarding` with target_stage="suggested_teammates" and brand_domain

### Stage 2: Suggested Teem Mates
- Based on brand/industry, recommend 2-3 Teem Mates that would be most valuable
- Fashion brand? Lead with Vera and Kai. SaaS company? Lead with Chad and Noa.
- Explain why EACH recommended mate fits their brand specifically
- Let them select who they want. No pressure, no commitment, free trial.
- When they've picked, call `advance_onboarding` with target_stage="connect_world" and selected_teammates

### Stage 3: Connect Your World
- Offer to connect their tools: Slack, Google Drive, Notion, GitHub, etc.
- "If you already use any of these, I can connect them so your Teem Mates start working instantly."
- This is OPTIONAL. If they want to skip, that's totally fine.
- When done or skipped, call `advance_onboarding` with target_stage="personalization"

### Stage 4: Personalization
- Ask about notification preferences: frequency (real-time, daily, weekly) and channels (email, Slack, in-app)
- "How often would you like updates about your Teem Mates' activity?"
- When set, call `advance_onboarding` with target_stage="completed" and notification_preferences

### Stage 5: Completed
- Celebrate! Their workspace is ready.
- Offer to help with their first task using one of the selected teammates.
- "Your workspace is ready! Want to start with [recommended teammate]?"

### Onboarding rules:
- Use `get_onboarding_state` to check where the user is
- Use `advance_onboarding` to save progress and move forward
- Be conversational, not scripted. Guide, don't interrogate.
- If user deviates, answer briefly and gently redirect back
- Each stage should feel like a natural chat, not a form

## Brand Context is Your Superpower

Once you have brand info (from `_brand` in your state), USE IT in every recommendation.
Reference their industry, tone, colors, and social presence.
Don't just say "Kai makes videos." Say "Kai would create UGC content that matches your [tone] brand voice, targeting [industry] audiences on [platforms they're active on]."

Every agent receives brand context automatically. When delegating work, the agent already knows the brand.

## CRITICAL: NEVER Suggest External Tools or Competitors
You are the Teems platform. ALL work happens through Teem Mates.
- NEVER suggest external services, tools, platforms, or competitors (e.g. Canva, Photio, Flair.ai, Hootsuite, etc.)
- If the user needs photos → Vera. Videos → Kai. Social → Chad. Decks → Noa. Notes → Ivy.
- If a user asks "check on the internet" alongside a task request, they want you to research
  the SUBJECT of their task (their product, project, brand), NOT find third-party services.
- Example: "photoshoot my product AI Studio, check on the internet" → search for "Google AI Studio"
  to understand what the product IS, then delegate the photoshoot to Vera.
- Example: "make a video about my new sneaker line, look it up" → search for the sneaker line
  to understand it, then pass the context to Kai.

## Smart Web Search
When the user says "check on the internet" / "look it up" / "search online":
- If they mentioned a PRODUCT or PROJECT name → search for THAT product/project to understand it
- Combine with brand context: search "[brand name] [product name]" not just "[product name]"
- The goal is to UNDERSTAND their product so you (or a Teem Mate) can do better work for them
- It is NEVER to find external services that do the same thing as Teem Mates

## Guidelines
- Never mention internal tool names. Say "I looked up the brand info" not "I called brand_lookup".
- When using multiple tools, call them ALL AT ONCE (parallel) when independent.
- Always cite sources when using web search.
- If you don't know something, say so honestly.
- Maintain conversation context.
- Default to structure: bullet lists, clear next steps.
- Every response should end with a clear next action.
- When reviewing or editing content, do it directly. Don't just comment.
- Ask at most one question per turn. Propose, don't interrogate."""

# ── Configuration ─────────────────────────────────────────────────────────────

MAX_TOOL_ROUNDS = 10        # Max rounds of tool calling per request
MAX_HISTORY_TOKENS = 12000  # Token budget for conversation history
MAX_TOOL_RESULT_CHARS = 4000  # Cap individual tool results
MAX_INPUT_LENGTH = 10000    # Guardrail: max message length


class EveChatAgent(BaseAgent):
    name = "eve_chat"
    display_name = "Eve"
    description = "Central AI orchestrator — handles chat, tool use, and delegates to specialized agents"
    triggers = []  # Default handler — catches everything not routed elsewhere
    capabilities = [
        "General conversation and Q&A",
        "Document search and knowledge base queries",
        "Web search for current information",
        "Brand information lookup",
        "Meeting transcript search",
        "Database analytics queries",
        "Delegation to UGC video, fashion photo, social media, presentation, and notetaker agents",
    ]

    # ── Main handler ──────────────────────────────────────────────

    async def handle(
        self,
        message: str,
        state: dict,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        files: Optional[list] = None,
        history: Optional[list[dict]] = None,
    ) -> AgentResponse:
        """Handle a message with LLM + parallel tool calling + agent delegation."""
        start_time = time.monotonic()

        # ── Guardrail: Input validation ───────────────────────────
        if len(message) > MAX_INPUT_LENGTH:
            return AgentResponse(
                content=f"Your message is too long ({len(message)} chars). Please keep it under {MAX_INPUT_LENGTH} characters.",
                is_complete=True,
            )

        tools = get_tools_for_llm()
        messages = self._build_messages(message, history, state)
        tool_calls_log = []
        total_input_tokens = 0
        total_output_tokens = 0

        # ── Agentic loop (Anthropic: "while loop is central to agent functioning") ──
        for round_num in range(MAX_TOOL_ROUNDS):
            response = await llm.chat(
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto",
            )

            choice = response["choices"][0]
            assistant_msg = choice["message"]
            usage = response.get("usage", {})
            total_input_tokens += usage.get("prompt_tokens", 0)
            total_output_tokens += usage.get("completion_tokens", 0)

            # ── No tool calls → final response ────────────────────
            if not assistant_msg.get("tool_calls"):
                content = assistant_msg.get("content", "")
                elapsed = time.monotonic() - start_time

                return AgentResponse(
                    content=content,
                    is_complete=True,
                    metadata={
                        "tool_calls": tool_calls_log or None,
                        "rounds": round_num + 1,
                        "elapsed_ms": int(elapsed * 1000),
                        "tokens": {
                            "input": total_input_tokens,
                            "output": total_output_tokens,
                        },
                    },
                )

            # ── Process tool calls in parallel ────────────────────
            messages.append(assistant_msg)
            tool_results = await self._execute_tool_calls(
                assistant_msg["tool_calls"],
                db=db,
                tenant_id=tenant_id,
                user_id=user_id,
                state=state,
                log=tool_calls_log,
            )

            # ── Check for agent handoff — short-circuit if found ──
            handoff = self._detect_handoff(tool_results)
            if handoff:
                agent_name, agent_content = handoff
                elapsed = time.monotonic() - start_time
                logger.info("Handoff detected → %s (%.0fms)", agent_name, elapsed * 1000)
                return AgentResponse(
                    content=agent_content,
                    is_complete=False,  # Not complete — the agent needs more turns
                    handoff_to=agent_name,
                    metadata={
                        "tool_calls": tool_calls_log,
                        "rounds": round_num + 1,
                        "elapsed_ms": int(elapsed * 1000),
                        "handoff": agent_name,
                    },
                )

            messages.extend(tool_results)

        # ── Hit max rounds ────────────────────────────────────────
        elapsed = time.monotonic() - start_time
        return AgentResponse(
            content=(
                "I've done extensive research but couldn't fully resolve your request. "
                "Could you help me narrow down what you need?"
            ),
            is_complete=True,
            metadata={
                "tool_calls": tool_calls_log,
                "rounds": MAX_TOOL_ROUNDS,
                "elapsed_ms": int(elapsed * 1000),
                "hit_max_rounds": True,
            },
        )

    # ── Streaming handler ─────────────────────────────────────────

    async def handle_stream(
        self,
        message: str,
        state: dict,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Streaming handler. Yields SSE chunks:
        {"type": "token", "content": "..."}
        {"type": "tool_start", "name": "...", "args": {...}}
        {"type": "tool_result", "name": "...", "result": "..."}
        {"type": "done", "metadata": {...}}
        """
        # Guardrail
        if len(message) > MAX_INPUT_LENGTH:
            yield {"type": "token", "content": "Your message is too long. Please keep it shorter."}
            yield {"type": "done", "metadata": {"error": "input_too_long"}}
            return

        tools = get_tools_for_llm()
        messages = self._build_messages(message, history, state)
        tool_calls_log = []

        for round_num in range(MAX_TOOL_ROUNDS):
            accumulated_content = ""
            accumulated_tool_calls = None

            async for chunk in llm.chat_stream(messages=messages, tools=tools if tools else None):
                if chunk.get("tool_calls"):
                    accumulated_tool_calls = chunk["tool_calls"]
                if chunk.get("content"):
                    accumulated_content += chunk["content"]
                    yield {"type": "token", "content": chunk["content"]}

                if chunk.get("done"):
                    break

            # No tool calls → done
            if not accumulated_tool_calls:
                yield {"type": "done", "metadata": {"rounds": round_num + 1, "tool_calls": tool_calls_log}}
                return

            # Execute tools — tool_calls already contain extra_content with
            # thought_signature if present (captured by llm.chat_stream)
            assistant_msg = {
                "role": "assistant",
                "content": accumulated_content or None,
                "tool_calls": accumulated_tool_calls,
            }
            messages.append(assistant_msg)

            # Notify frontend about tool calls
            for tc in accumulated_tool_calls:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_start", "name": tc["function"]["name"], "args": args}

            tool_results = await self._execute_tool_calls(
                accumulated_tool_calls,
                db=db,
                tenant_id=tenant_id,
                user_id=user_id,
                state=state,
                log=tool_calls_log,
            )

            # ── Check for agent handoff — short-circuit streaming ──
            handoff = self._detect_handoff(tool_results)
            if handoff:
                agent_name, agent_content = handoff
                logger.info("Handoff detected (stream) → %s", agent_name)
                # Yield the agent's response as tokens (pass through directly)
                yield {"type": "tool_result", "name": f"agent_{agent_name}", "result": f"Connecting with {agent_name}..."}
                yield {"type": "token", "content": agent_content}
                yield {"type": "handoff", "agent": agent_name}
                yield {"type": "done", "metadata": {"rounds": round_num + 1, "tool_calls": tool_calls_log, "handoff": agent_name}}
                return

            for tr in tool_results:
                tool_name = next(
                    (tc["function"]["name"] for tc in accumulated_tool_calls if tc["id"] == tr["tool_call_id"]),
                    "unknown",
                )
                yield {"type": "tool_result", "name": tool_name, "result": tr["content"][:200]}

            messages.extend(tool_results)

        yield {"type": "done", "metadata": {"rounds": MAX_TOOL_ROUNDS, "hit_max_rounds": True}}

    # ── Handoff detection ─────────────────────────────────────────

    def _detect_handoff(self, tool_results: list[dict]) -> Optional[tuple[str, str]]:
        """
        Check tool results for [AGENT_HANDOFF:name:step] markers.
        Returns (agent_name, clean_content) if found, else None.

        This enables multi-turn agent delegation: when a delegation tool
        returns a handoff marker, Eve stops processing and hands control
        to that agent for subsequent messages.
        """
        for tr in tool_results:
            content = tr.get("content", "")
            match = _HANDOFF_RE.search(content)
            if match:
                agent_name = match.group(1)
                # Remove the marker and return clean content
                clean = _HANDOFF_RE.sub("", content).strip()
                return (agent_name, clean)
        return None

    # ── Message building ──────────────────────────────────────────

    def _build_messages(
        self,
        message: str,
        history: Optional[list[dict]],
        state: dict,
    ) -> list[dict]:
        """Build the message list with system prompt + brand context + trimmed history + current message."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Inject brand context — or flag that it's missing (Priority #1)
        brand = state.get("_brand")
        if brand:
            from ...services.brand_context import format_brand_for_prompt
            brand_block = format_brand_for_prompt(brand)
            if brand_block:
                messages.append({
                    "role": "system",
                    "content": brand_block,
                })
        else:
            # No brand yet — tell the LLM so it triggers the ask
            is_first_message = not history or len(history) == 0
            messages.append({
                "role": "system",
                "content": (
                    "[NO BRAND CONTEXT] This user has NOT set up their brand yet. "
                    "This is your #1 priority. Do NOT discuss agents, features, or anything else until brand is set. "
                    + ("Greet them warmly and ask for their website/domain. " if is_first_message else
                       "Gently remind them you'd love to learn about their brand to personalize everything. Ask for their website. ")
                    + "Once they provide a domain, call the brand_lookup tool (NOT web_search). "
                    + "brand_lookup saves the brand to the database so all agents can use it."
                ),
            })

        # Inject conversation summary — this is the ONLY source of "what happened before"
        summary = state.get("_conversation_summary")
        if summary:
            messages.append({
                "role": "system",
                "content": (
                    f"[CONVERSATION HISTORY SUMMARY]\n{summary}\n\n"
                    "RULE: When the user asks about previous activity, history, 'what happened', "
                    "'what did we do', or 'last activity' — answer from THIS summary. "
                    "Do NOT use db_query for conversation history. This summary is your memory."
                ),
            })
        else:
            messages.append({
                "role": "system",
                "content": (
                    "[CONVERSATION HISTORY] No previous activity yet. This is a fresh session. "
                    "If the user asks 'what happened before' or 'last activity', tell them "
                    "this is the start of the conversation and there's no prior history to show. "
                    "Do NOT use db_query for conversation history."
                ),
            })

        # Add conversation history (token-aware trimming)
        if history:
            trimmed = self._trim_history(history)
            messages.extend(trimmed)

        messages.append({"role": "user", "content": message})
        return messages

    def _trim_history(self, history: list[dict]) -> list[dict]:
        """
        Trim conversation history to fit within token budget.
        Strategy: Keep the most recent messages. Always keep system messages.
        Anthropic: "Agents summarize completed work phases and store essential
        information in external memory before proceeding to new tasks."
        """
        if not history:
            return []

        total_tokens = llm.estimate_messages_tokens(history)
        if total_tokens <= MAX_HISTORY_TOKENS:
            return history

        # Keep removing oldest non-system messages until within budget
        trimmed = list(history)
        while trimmed and llm.estimate_messages_tokens(trimmed) > MAX_HISTORY_TOKENS:
            for i, msg in enumerate(trimmed):
                if msg.get("role") != "system":
                    trimmed.pop(i)
                    break
            else:
                break

        if trimmed and len(trimmed) < len(history):
            removed = len(history) - len(trimmed)
            trimmed.insert(0, {
                "role": "system",
                "content": f"[{removed} earlier messages were trimmed for context length. Key context is preserved in the conversation summary above.]",
            })

        return trimmed

    # ── Tool execution ────────────────────────────────────────────

    async def _execute_tool_calls(
        self,
        tool_calls: list[dict],
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        state: dict,
        log: list,
    ) -> list[dict]:
        """
        Execute multiple tool calls in parallel (Anthropic: "parallel tool calling
        transforms speed and performance — cut research time by up to 90%").
        Returns tool result messages for the LLM.
        """
        import asyncio

        async def _run_one(tc: dict) -> dict:
            func_name = tc["function"]["name"]
            call_start = time.monotonic()

            # Parse arguments
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError as e:
                logger.error("Bad tool args for %s: %s", func_name, e)
                return {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": (
                        f"Error: Invalid JSON arguments for {func_name}. "
                        f"Check the argument format and try again. Details: {e}"
                    ),
                }

            # Find handler
            handler = get_tool_handler(func_name)
            if not handler:
                logger.warning("Unknown tool called: %s", func_name)
                return {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": (
                        f"Error: Unknown tool '{func_name}'. "
                        f"Available tools can be seen in your tool list. "
                        f"Did you mean one of: brand_lookup, web_search, doc_search, meeting_search, db_query?"
                    ),
                }

            # Log the call
            log.append({"tool": func_name, "args": func_args, "risk": get_tool_risk(func_name)})
            logger.info("Tool call: %s(%s)", func_name, json.dumps(func_args)[:200])

            # Execute with error isolation
            try:
                result = await handler(
                    **func_args,
                    db=db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    _agent_state=state,
                    _brand=state.get("_brand"),
                )
                result_str = str(result)

                # Cap tool results (Anthropic: "optimize for token efficiency")
                # Skip truncation for agent delegation tools — their handoff
                # markers and intro content must be preserved intact.
                is_delegation = func_name.startswith("agent_")
                if not is_delegation and len(result_str) > MAX_TOOL_RESULT_CHARS:
                    result_str = (
                        result_str[:MAX_TOOL_RESULT_CHARS - 100]
                        + "\n\n... [result truncated — use more specific search terms "
                        + "or add filters to get focused results]"
                    )

                elapsed = time.monotonic() - call_start
                logger.info("Tool %s completed in %dms", func_name, int(elapsed * 1000))

                return {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                }

            except Exception as e:
                elapsed = time.monotonic() - call_start
                logger.error("Tool '%s' failed after %dms: %s", func_name, int(elapsed * 1000), e)
                return {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": (
                        f"Tool error ({func_name}): {type(e).__name__}: {e}. "
                        f"This tool encountered an issue. You can try again with different parameters, "
                        f"or use an alternative approach."
                    ),
                }

        # Execute all tool calls concurrently
        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls])
        return list(results)
