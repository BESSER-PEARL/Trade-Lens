# SPDX-License-Identifier: MIT
"""LuxInsights, the agent: a BAF router and four specialists that draw on the page in their own pens.

- ``lux_insights_agent.py``: the BAF agent (states, WebSocket, --ask mode).
- ``conversation.py``: one conversation: route a question, run each specialist, remember the turn.
- ``router.py``: who answers (LLM plan, else a keyword table; numbered marks go to their owner).
- ``specialists.py``: the prompts and the read -> draw -> reply loop, with the reply checks.
- ``tools.py``: the tools on top of agents/kit (read /insights and /trends; draw with typed ops).
- ``page.py``, ``fmt.py``, ``registry.py``, ``settings.py``: the page, figures, registration, configuration.
"""
