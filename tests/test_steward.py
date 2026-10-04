"""The steward: its tool boundary, and the bridge that carries its questions.

The bridge is tested against an in-memory stand-in for the `steward_asks`
table rather than per-call mock assertions, because what matters here is a
lifecycle — posted, answered, reported — and the bugs worth catching are
the ones that only show up on the second poll.
"""

from unittest.mock import MagicMock, patch

import pytest

from mimir_agent import config, steward
from mimir_agent.steward_bridge import StewardBridge


# --- fakes ---------------------------------------------------------------


class FakeAsks:
    """The steward_asks table, in a dict."""

    def __init__(self):
        self.rows: dict[int, dict] = {}

    def record(self, run_id, channel, thread_ts, question):
        if run_id in self.rows:
            return False
        self.rows[run_id] = {
            "channel": channel,
            "thread_ts": thread_ts,
            "question": question,
            "answered": False,
            "reported": False,
        }
        return True

    def posted(self, run_id):
        return run_id in self.rows

    def for_thread(self, channel, thread_ts):
        unanswered = [
            rid for rid, r in self.rows.items()
            if r["channel"] == channel and r["thread_ts"] == thread_ts and not r["answered"]
        ]
        if unanswered:
            return unanswered[0]
        any_match = [
            rid for rid, r in self.rows.items()
            if r["channel"] == channel and r["thread_ts"] == thread_ts
        ]
        return any_match[0] if any_match else None

    def mark_answered(self, run_id):
        self.rows[run_id]["answered"] = True

    def awaiting_report(self):
        return [
            (rid, r["channel"], r["thread_ts"])
            for rid, r in self.rows.items()
            if r["answered"] and not r["reported"]
        ]

    def mark_reported(self, run_id):
        self.rows[run_id]["reported"] = True


def fake_run(run_id, status, question=None, output=None, agent_id=1):
    run = MagicMock()
    run.run_id = run_id
    run.status = status
    run.output = output
    run.agent_id = agent_id
    run.is_waiting = status == "waiting"
    if question is None:
        run.waiting_for = None
    else:
        run.waiting_for = MagicMock()
        run.waiting_for.question = question
    return run


@pytest.fixture
def asks():
    store = FakeAsks()
    with patch.multiple(
        "mimir_agent.db",
        record_steward_ask=store.record,
        steward_ask_posted=store.posted,
        steward_run_for_thread=store.for_thread,
        mark_steward_ask_answered=store.mark_answered,
        steward_asks_awaiting_report=store.awaiting_report,
        mark_steward_ask_reported=store.mark_reported,
    ):
        yield store


@pytest.fixture
def slack():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1700000000.000100"}
    return client


@pytest.fixture
def norns():
    client = MagicMock()
    agent = MagicMock()
    agent.id = 1
    client.get_agent.return_value = agent
    client.list_runs.return_value = []
    return client


@pytest.fixture
def bridge(slack, norns):
    return StewardBridge(slack, norns_client=norns, channel="C_STEWARD")


# --- the tool boundary ---------------------------------------------------


class TestStewardTools:
    def test_the_steward_holds_no_write_tools(self):
        """The whole safety argument: a 6am run cannot change anything."""
        names = {t.name for t in steward.STEWARD_TOOLS}
        for forbidden in ("bash", "write_file", "edit_file", "git"):
            assert forbidden not in names

    def test_every_tool_is_read_only_or_memory(self):
        names = {t.name for t in steward.STEWARD_TOOLS}
        # `remember` is the one side-effecting tool, and it writes only to
        # Mimir's own memory table.
        assert names - {"remember"} == {
            "list_github_issues",
            "read_github_issue",
            "list_github_prs",
            "read_github_pr",
            "list_github_commits",
            "list_github_branches",
            "read_github_file",
            "search_github",
            "read_url",
            "search_memory",
        }

    def test_allowed_tools_is_pinned_to_its_own_set(self):
        """Left unset, Norns offers it every tool in the tenant — including
        the coding agent's bash."""
        agent = steward.build()
        assert set(agent.allowed_tools) == {t.name for t in steward.STEWARD_TOOLS}

    def test_it_may_only_launch_the_coder(self):
        agent = steward.build()
        assert agent.subagents["mode"] == "allowlist"
        assert agent.subagents["allowed_agents"] == [config.STEWARD_CODER_AGENT]

    def test_the_prompt_is_fully_substituted(self):
        agent = steward.build()
        assert "{coder}" not in agent.system_prompt
        assert "{memory_project}" not in agent.system_prompt
        assert config.STEWARD_CODER_AGENT in agent.system_prompt

    def test_the_prompt_licenses_proposing_nothing(self):
        """A daily agent that must find work will invent it."""
        assert "Proposing nothing is a real answer" in steward.SYSTEM_PROMPT


# --- outbound: parked run → Slack ----------------------------------------


class TestPostWaiting:
    def test_a_waiting_run_is_posted(self, bridge, slack, norns, asks):
        norns.list_runs.return_value = [fake_run(42, "waiting", question="Ship A or B?")]

        assert bridge.post_waiting() == 1

        text = slack.chat_postMessage.call_args.kwargs["text"]
        assert "Ship A or B?" in text
        assert "42" in text
        assert asks.posted(42)

    def test_it_is_not_posted_twice(self, bridge, slack, norns, asks):
        norns.list_runs.return_value = [fake_run(42, "waiting", question="Ship A or B?")]

        assert bridge.post_waiting() == 1
        assert bridge.post_waiting() == 0
        assert slack.chat_postMessage.call_count == 1

    def test_a_waiting_run_with_no_question_is_skipped(self, bridge, slack, norns, asks):
        norns.list_runs.return_value = [fake_run(42, "waiting")]

        assert bridge.post_waiting() == 0
        slack.chat_postMessage.assert_not_called()

    def test_a_failed_slack_post_is_retried_next_poll(self, bridge, slack, norns, asks):
        """The direction that matters: never mark a question posted that
        nobody saw, or the run parks forever."""
        norns.list_runs.return_value = [fake_run(42, "waiting", question="Ship A or B?")]
        slack.chat_postMessage.side_effect = RuntimeError("slack down")

        assert bridge.post_waiting() == 0
        assert not asks.posted(42)

        slack.chat_postMessage.side_effect = None
        slack.chat_postMessage.return_value = {"ts": "1.1"}
        assert bridge.post_waiting() == 1

    def test_it_only_looks_at_the_stewards_runs(self, bridge, norns, asks):
        bridge.post_waiting()
        kwargs = norns.list_runs.call_args.kwargs
        assert kwargs["status"] == "waiting"
        assert kwargs["agent_id"] == 1

    def test_nothing_happens_before_the_agent_is_registered(self, bridge, norns, asks):
        norns.get_agent.side_effect = RuntimeError("404")
        bridge._agent_id = None

        assert bridge.post_waiting() == 0
        norns.list_runs.assert_not_called()

    def test_a_long_question_is_truncated(self, bridge, slack, norns, asks):
        norns.list_runs.return_value = [fake_run(42, "waiting", question="x" * 9000)]
        bridge.post_waiting()
        assert len(slack.chat_postMessage.call_args.kwargs["text"]) <= 3500


# --- inbound: Slack reply → run ------------------------------------------


class TestAnswer:
    def _park(self, bridge, norns, run_id=42):
        norns.list_runs.return_value = [fake_run(run_id, "waiting", question="Ship A or B?")]
        bridge.post_waiting()
        norns.list_runs.return_value = []
        return bridge.slack.chat_postMessage.call_args.kwargs.get("channel"), "1700000000.000100"

    def test_a_reply_reaches_the_run(self, bridge, norns, asks):
        channel, ts = self._park(bridge, norns)
        norns.get_run.return_value = fake_run(42, "waiting", question="Ship A or B?")

        assert bridge.answer(channel, ts, "do A") == 42
        norns.reply.assert_called_once_with(42, "do A")

    def test_an_answered_run_is_marked(self, bridge, norns, asks):
        channel, ts = self._park(bridge, norns)
        norns.get_run.return_value = fake_run(42, "waiting", question="Ship A or B?")

        bridge.answer(channel, ts, "do A")
        assert asks.rows[42]["answered"] is True

    def test_an_unknown_thread_is_not_ours(self, bridge, norns, asks):
        """None tells the Slack handler to treat it as a normal message."""
        assert bridge.answer("C_OTHER", "9.9", "hello mimir") is None
        norns.reply.assert_not_called()

    def test_a_run_that_is_no_longer_waiting_is_not_replied_to(self, bridge, slack, norns, asks):
        channel, ts = self._park(bridge, norns)
        norns.get_run.return_value = fake_run(42, "running")

        assert bridge.answer(channel, ts, "do A") is None
        norns.reply.assert_not_called()
        assert "isn't waiting" in slack.chat_postMessage.call_args.kwargs["text"]

    def test_a_failed_delivery_is_reported_and_not_marked(self, bridge, slack, norns, asks):
        channel, ts = self._park(bridge, norns)
        norns.get_run.return_value = fake_run(42, "waiting", question="Ship A or B?")
        norns.reply.side_effect = RuntimeError("boom")

        assert bridge.answer(channel, ts, "do A") is None
        assert asks.rows[42]["answered"] is False
        assert "couldn't deliver" in slack.chat_postMessage.call_args.kwargs["text"]


# --- outbound: the outcome ----------------------------------------------


class TestReportFinished:
    def _answered(self, bridge, norns, asks, run_id=42):
        norns.list_runs.return_value = [fake_run(run_id, "waiting", question="Ship A or B?")]
        bridge.post_waiting()
        norns.list_runs.return_value = []
        asks.mark_answered(run_id)

    def test_a_completed_run_reports_into_its_thread(self, bridge, slack, norns, asks):
        self._answered(bridge, norns, asks)
        norns.get_run.return_value = fake_run(42, "completed", output="Landed the gard fix.")

        assert bridge.report_finished() == 1
        kwargs = slack.chat_postMessage.call_args.kwargs
        assert kwargs["thread_ts"] == "1700000000.000100"
        assert "Landed the gard fix." in kwargs["text"]
        assert asks.rows[42]["reported"] is True

    def test_it_reports_once(self, bridge, norns, asks):
        self._answered(bridge, norns, asks)
        norns.get_run.return_value = fake_run(42, "completed", output="done")

        assert bridge.report_finished() == 1
        assert bridge.report_finished() == 0

    def test_a_failed_run_is_called_out(self, bridge, slack, norns, asks):
        self._answered(bridge, norns, asks)
        norns.get_run.return_value = fake_run(42, "failed")

        bridge.report_finished()
        assert "failed" in slack.chat_postMessage.call_args.kwargs["text"]

    def test_a_still_running_run_is_left_alone(self, bridge, norns, asks):
        self._answered(bridge, norns, asks)
        norns.get_run.return_value = fake_run(42, "running")

        assert bridge.report_finished() == 0
        assert asks.rows[42]["reported"] is False

    def test_a_follow_up_question_keeps_the_row_open(self, bridge, norns, asks):
        """The agent may ask again after an answer. That is a new question on
        the same thread, not a finished run."""
        self._answered(bridge, norns, asks)
        norns.get_run.return_value = fake_run(42, "waiting", question="Also bump the SDK?")

        assert bridge.report_finished() == 0
        assert asks.rows[42]["reported"] is False


# --- configuration -------------------------------------------------------


class TestStart:
    def test_without_a_channel_the_bridge_stays_off(self, slack):
        from mimir_agent import steward_bridge

        with patch.object(config, "STEWARD_SLACK_CHANNEL", ""):
            assert steward_bridge.start(slack) is None

    def test_a_disabled_bridge_does_not_intercept_slack_messages(self):
        from mimir_agent import steward_bridge

        assert steward_bridge.bridge() is None
