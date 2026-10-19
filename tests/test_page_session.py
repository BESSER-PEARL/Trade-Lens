# SPDX-License-Identifier: MIT
"""The agent draws on the page of the session that asked: the page opens the BAF session as page-<sid>."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "agents"))


def test_a_page_session_names_the_visitors_page():
    agent = pytest.importorskip("lux_insights.lux_insights_agent")
    assert agent.page_session("page-tab-aaaaaaaa") == "tab-aaaaaaaa"
    assert agent.page_session("3f1c0e2a-uuid-of-a-connection") is None
    assert agent.page_session("page-x") is None   # too short to be a page's session id
