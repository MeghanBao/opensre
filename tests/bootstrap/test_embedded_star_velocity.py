"""Scripted Python API coverage for repeated GitHub star-velocity turns."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bootstrap.embedded import start_embedded_session
from bootstrap.process import reset_process_runtime_for_tests
from core.agent_harness.harness import SessionConfig
from core.llm.factory import LLMRole
from core.llm.types import AgentLLMResponse, ToolCall
from platform.harness_ports import reset_harness_ports


class _StarVelocityScriptedLLM:
    """Handoff star questions, then call the GitHub history tool."""

    model_id = "scripted-star-velocity"

    def __init__(self) -> None:
        self.star_history_calls = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, str]]:
        return [{"name": str(tool.name)} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        del system
        tool_names = {str(schema.get("name")) for schema in tools or []}
        if "assistant_handoff" in tool_names:
            if any(message.get("role") == "tool" for message in messages):
                return AgentLLMResponse(content="", tool_calls=[], raw_content=None)
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="handoff-star-velocity",
                        name="assistant_handoff",
                        input={
                            "content": "Fetch GitHub star history.",
                            "requires_gather": True,
                        },
                    )
                ],
                raw_content=None,
            )
        if "get_github_star_history" in tool_names:
            if any(message.get("role") == "tool" for message in messages):
                return AgentLLMResponse(
                    content="GitHub star history fetched.",
                    tool_calls=[],
                    raw_content=None,
                )
            self.star_history_calls += 1
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"star-history-{self.star_history_calls}",
                        name="get_github_star_history",
                        input={"days": 7},
                    )
                ],
                raw_content=None,
            )
        raise AssertionError(f"unexpected tool schemas: {sorted(tool_names)}")

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "input": call.input} for call in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {"role": "tool", "content": json.dumps(results, default=str)}

    @staticmethod
    def with_structured_output(schema: Any) -> Any:
        class _GoalReached:
            @staticmethod
            def invoke(_prompt: str) -> Any:
                return schema(verdict="GOAL_REACHED")

        return _GoalReached()


class _DefiniteStarAnswerLLM:
    model_id = "scripted-star-answer"

    def __init__(self) -> None:
        self.observations: list[str] = []

    def invoke_stream(self, messages: list[dict[str, Any]]) -> Any:
        prompt = json.dumps(messages, default=str)
        assert "get_github_star_history" in prompt
        assert "stargazers_count" in prompt and "100" in prompt
        assert "stars_in_window" in prompt and "3" in prompt
        self.observations.append(prompt)
        yield "Tracer-Cloud/opensre has 100 stars and gained 3 in 7 days (0.43/day), an upward trend."


def test_embedded_session_retries_star_velocity_with_live_github_data(
    monkeypatch: Any,
) -> None:
    """Repeated Python API turns keep gathering until each answer is definite."""
    prompts = (
        "what is our current github star developer velocity?",
        (
            "What do you mean? Just look up the github star velocity on "
            "https://github.com/Tracer-Cloud/opensre"
        ),
        "No this is wrong you should get to the definite answer",
    )
    scripted = _StarVelocityScriptedLLM()
    answer = _DefiniteStarAnswerLLM()

    def _get_llm(role: LLMRole) -> Any:
        if role is LLMRole.AGENT:
            return scripted
        if role is LLMRole.REASONING:
            return answer
        raise AssertionError(f"unexpected LLM role: {role}")

    def _github_request(_self: Any, _method: str, path: str, **_kwargs: Any) -> Any:
        if path == "/repos/Tracer-Cloud/opensre":
            return {"stargazers_count": 100}
        if path == "/repos/Tracer-Cloud/opensre/stargazers":
            return [
                {"starred_at": "2026-08-18T10:00:00Z"},
                {"starred_at": "2026-08-19T10:00:00Z"},
                {"starred_at": "2026-08-20T10:00:00Z"},
            ]
        raise AssertionError(f"unexpected GitHub path: {path}")

    def _prepare_session(state: Any) -> None:
        state.resolved_integrations_cache = {}

    reset_harness_ports()
    reset_process_runtime_for_tests()
    monkeypatch.setenv("OPENSRE_WORKSPACE_REPO", "Tracer-Cloud/opensre")
    monkeypatch.setattr("core.llm.factory.get_llm", _get_llm)
    monkeypatch.setattr(
        "integrations.github.tools.stargazers._now_utc",
        lambda: datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        _github_request,
    )

    session = start_embedded_session(
        SessionConfig(
            load_env=False,
            hydrate_integrations=False,
            warm_integrations=False,
            persistent_tasks=False,
            open_store=False,
        ),
        prepare_session=_prepare_session,
    )

    results = [session.chat(prompt) for prompt in prompts]

    assert scripted.star_history_calls == 3
    assert len(answer.observations) == 3
    assert [result.gather_success_count for result in results] == [1, 1, 1]
    assert all("100 stars" in result.primary_response_text for result in results)
    assert all("upward trend" in result.primary_response_text for result in results)
