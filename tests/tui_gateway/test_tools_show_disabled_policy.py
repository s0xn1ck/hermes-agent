"""Request-level supplement to #44505: /tools must respect the agent policy."""

from types import SimpleNamespace

import pytest

import model_tools
import tui_gateway.server as server


@pytest.mark.parametrize("disabled", [[], ["file"]])
def test_tools_show_uses_live_agent_disabled_toolsets(monkeypatch, disabled):
    # Exercise real schema resolution, not a mock that merely records kwargs.
    enabled = ["file", "terminal"]
    unrestricted = model_tools.get_tool_definitions(
        enabled_toolsets=enabled, quiet_mode=True, skip_tool_search_assembly=True,
    )
    expected = model_tools.get_tool_definitions(
        enabled_toolsets=enabled, disabled_toolsets=disabled,
        quiet_mode=True, skip_tool_search_assembly=True,
    )
    def names(tools):
        return {tool["function"]["name"] for tool in tools}

    assert "read_file" in names(unrestricted)
    assert "terminal" in names(expected)
    if disabled:
        assert "read_file" not in names(expected)
    monkeypatch.setattr(server, "_sessions", {
        "policy-session": {"agent": SimpleNamespace(
            enabled_toolsets=enabled, disabled_toolsets=disabled,
        )},
    })

    response = server._methods["tools.show"](1, {"session_id": "policy-session"})

    assert "error" not in response, response
    result = response["result"]
    shown = {tool["name"] for section in result["sections"] for tool in section["tools"]}
    assert shown == names(expected)
    assert result["total"] == len(shown)
