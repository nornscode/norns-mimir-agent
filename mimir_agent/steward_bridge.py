"""The bridge between a parked steward run and a Slack thread.

A steward run that calls `ask_human` parks in Norns and holds no connection
to anything. Nobody is waiting on it: the cron trigger that started it is
long gone, and Mimir's Slack path is synchronous with a two-minute timeout,
so it cannot be the thing that waits. Something has to go looking.

That is all this is. On a timer it asks Norns which steward runs are
waiting, posts each new question into Slack once, and routes the reply back
to that run. The state it keeps is a Slack thread per run id, in Postgres,
so a redeploy mid-wait loses nothing — on the next poll it finds the same
parked run and the row that says it was already posted.

The deliberate choice here is that the *wait* is not ours. We could have
blocked a tool call until the answer came, which is simpler and about forty
lines shorter, and it would throw away the only property that matters: the
proposal would die with this process. Norns holds the wait; we only carry
messages.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from norns import NornsClient

from mimir_agent import config, db

logger = logging.getLogger("mimir_agent.steward")

# Slack rejects a message over 40k; a proposal should be nowhere near this.
MAX_SLACK_TEXT = 3500

# Slack channel IDs: C public/private, G legacy group, D direct message.
_CHANNEL_ID = re.compile(r"^[CGD][A-Z0-9]{6,}$")

# A question older than this has never been posted and nobody is sitting on
# it. Without the cutoff, pointing the bridge at a runtime with history
# dumps every run that ever parked into the channel at once. In normal
# operation a run parks and is posted within one poll, so this never bites.
MAX_ASK_AGE_HOURS = 24


def _truncate(text: str, limit: int = MAX_SLACK_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40] + "\n\n… (truncated; see the run in Norns)"


class StewardBridge:
    """Carries questions out to Slack and answers back to Norns."""

    def __init__(self, slack_client, norns_client: NornsClient | None = None, channel: str = ""):
        self.slack = slack_client
        self.norns = norns_client or NornsClient(config.NORNS_URL, api_key=config.NORNS_API_KEY)
        self.channel = channel or config.STEWARD_SLACK_CHANNEL
        self._agent_id: int | None = None
        self._agent_names: dict[int, str] | None = None

    # --- agent resolution -------------------------------------------------

    def agent_id(self) -> int | None:
        """The steward's agent id, cached. None until the worker has
        registered it, which on a cold start is a few seconds after boot."""
        if self._agent_id is None:
            try:
                self._agent_id = self.norns.get_agent(config.STEWARD_AGENT).id
            except Exception as e:
                logger.debug(f"steward agent not resolvable yet: {e}")
                return None
        return self._agent_id

    # --- outbound: a parked run becomes a Slack thread --------------------

    def post_waiting(self) -> int:
        """Post any parked question not yet in Slack. Returns how many.

        Every waiting run, not just the steward's. A coder the steward hands
        work to parks the same way on a permission prompt, and a parked run
        nobody can see is the babysitting this is supposed to remove.
        """
        try:
            runs = self.norns.list_runs(limit=100, status="waiting")
        except Exception as e:
            logger.warning(f"could not list waiting runs: {e}")
            return 0

        posted = 0
        for run in runs:
            if not run.waiting_for or not run.waiting_for.question:
                continue
            if db.steward_ask_posted(run.run_id):
                continue
            if self._too_old(run):
                logger.info(f"skipping run {run.run_id}, parked since {run.inserted_at}")
                continue
            parent_ts = self._parent_thread(run.run_id)
            if self._post_one(
                run.run_id,
                run.waiting_for.question,
                label=self._label(run.agent_id),
                thread_ts=parent_ts,
            ):
                posted += 1
        return posted

    def _too_old(self, run) -> bool:
        started = run.inserted_at
        if not isinstance(started, str):
            return False
        try:
            when = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - when > timedelta(hours=MAX_ASK_AGE_HOURS)

    def _label(self, agent_id: int | None) -> str:
        """The agent's name, for the header. Falls back to the id."""
        if self._agent_names is None:
            try:
                self._agent_names = {a.id: a.name for a in self.norns.list_agents()}
            except Exception as e:
                logger.debug(f"could not list agents: {e}")
                return f"agent {agent_id}"
        name = self._agent_names.get(agent_id)
        if name == config.STEWARD_AGENT:
            return "Norns steward"
        return name or f"agent {agent_id}"

    def _parent_thread(self, run_id: int) -> str | None:
        """The Slack thread of this run's parent, when it has one we posted.

        A coder's permission question belongs under the proposal that
        approved it, not in a thread of its own with no context.

        RunResponse carries no parent_run_id yet, so this reads the run
        directly. It should move into the SDK.
        """
        try:
            data = self.norns._request("GET", f"/api/v1/runs/{run_id}").json()["data"]
        except Exception as e:
            logger.debug(f"could not read run {run_id} for its parent: {e}")
            return None
        parent = data.get("parent_run_id")
        if not parent:
            return None
        try:
            return db.steward_thread_for_run(parent)
        except Exception as e:
            logger.debug(f"could not look up thread for parent run {parent}: {e}")
            return None

    def _post_one(
        self,
        run_id: int,
        question: str,
        label: str = "Norns steward",
        thread_ts: str | None = None,
    ) -> bool:
        from mimir_agent.slack_bot import to_slack_mrkdwn

        header = f":thread: *{label}* — run `{run_id}`\n\n"
        try:
            resp = self.slack.chat_postMessage(
                channel=self.channel,
                text=_truncate(header + to_slack_mrkdwn(question)),
                **({"thread_ts": thread_ts} if thread_ts else {}),
            )
            # In a parent's thread the reply key stays the parent's ts, so a
            # reply lands on the thread the user is actually looking at.
            thread_ts = thread_ts or resp["ts"]
        except Exception as e:
            logger.error(f"could not post steward question for run {run_id}: {e}")
            return False

        # Claim it only once Slack has it. The other order risks a question
        # that is marked posted and was never seen, which is the one failure
        # here with no way back — the run would park forever.
        try:
            db.record_steward_ask(run_id, self.channel, thread_ts, question)
        except Exception as e:
            logger.error(f"posted run {run_id} to Slack but could not record it: {e}")
            try:
                self.slack.chat_postMessage(
                    channel=self.channel,
                    thread_ts=thread_ts,
                    text=(
                        "I couldn't record this thread, so replying here won't reach "
                        f"the run. Answer it with `nornsctl runs reply {run_id}`."
                    ),
                )
            except Exception:
                pass
            return False

        logger.info(f"steward run {run_id} asked a question in {self.channel}")
        return True

    # --- inbound: a Slack reply answers the run ---------------------------

    def answer(self, channel: str, thread_ts: str, text: str) -> int | None:
        """Route a thread reply to the run waiting on it.

        Returns the run id if this thread belongs to a parked question —
        whether or not the answer could be delivered — and None only when
        the thread is not one of ours, which is the single case where the
        caller should go on and handle the message normally.

        The distinction matters: a reply in one of our threads is an answer
        to an agent, not a question for Mimir. Falling through on a run that
        has already finished let Mimir answer an approval and claim it had
        acted on it, which it has no way to do.
        """
        try:
            run_id = db.steward_run_for_thread(channel, thread_ts)
        except Exception as e:
            logger.warning(f"could not look up steward thread: {e}")
            return None
        if run_id is None:
            return None

        try:
            run = self.norns.get_run(run_id)
        except Exception as e:
            logger.warning(f"could not read steward run {run_id}: {e}")
            self._say(channel, thread_ts, f"I couldn't read run `{run_id}` to answer it: {e}")
            return run_id

        if not run.is_waiting:
            # Answered already, or it moved on. Say so rather than silently
            # dropping what the user typed.
            self._say(channel, thread_ts, f"Run `{run_id}` isn't waiting on an answer ({run.status}).")
            return run_id

        try:
            self.norns.reply(run_id, text)
        except Exception as e:
            logger.error(f"could not deliver answer to run {run_id}: {e}")
            self._say(channel, thread_ts, f"I couldn't deliver that to run `{run_id}`: {e}")
            return run_id

        try:
            db.mark_steward_ask_answered(run_id)
        except Exception as e:
            # The answer landed; only the bookkeeping failed. Worst case the
            # outcome is never reported back, which is not worth undoing a
            # delivered answer for.
            logger.warning(f"answered run {run_id} but could not mark it: {e}")

        logger.info(f"steward run {run_id} answered from Slack")
        self._react(channel, thread_ts)
        return run_id

    # --- outbound: the outcome, once the run finishes ---------------------

    def report_finished(self) -> int:
        """Post the result of any answered run that has since finished."""
        try:
            pending = db.steward_asks_awaiting_report()
        except Exception as e:
            logger.warning(f"could not list steward runs awaiting report: {e}")
            return 0

        reported = 0
        for run_id, channel, thread_ts in pending:
            try:
                run = self.norns.get_run(run_id)
            except Exception as e:
                logger.warning(f"could not read steward run {run_id}: {e}")
                continue

            if run.status == "waiting":
                # It asked a follow-up. That question is a new ask on the same
                # thread; let post_waiting carry it and keep this row open.
                continue
            if run.status not in ("completed", "failed"):
                continue

            from mimir_agent.slack_bot import to_slack_mrkdwn

            if run.status == "failed":
                body = f":warning: Run `{run_id}` failed."
            else:
                body = to_slack_mrkdwn(run.output or "Done — no output reported.")

            self._say(channel, thread_ts, _truncate(body))
            try:
                db.mark_steward_ask_reported(run_id)
                reported += 1
            except Exception as e:
                logger.warning(f"reported run {run_id} but could not mark it: {e}")
        return reported

    # --- plumbing ---------------------------------------------------------

    def _say(self, channel: str, thread_ts: str, text: str) -> None:
        try:
            self.slack.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
        except Exception as e:
            logger.warning(f"could not post to {channel}/{thread_ts}: {e}")

    def _react(self, channel: str, thread_ts: str) -> None:
        try:
            self.slack.reactions_add(channel=channel, timestamp=thread_ts, name="white_check_mark")
        except Exception:
            pass

    def poll_once(self) -> tuple[int, int]:
        return self.post_waiting(), self.report_finished()

    def run_forever(self, interval: int | None = None) -> None:
        interval = interval or config.STEWARD_POLL_SECONDS
        logger.info(f"steward bridge watching every parked run → {self.channel} every {interval}s")
        while True:
            try:
                self.poll_once()
            except Exception as e:  # a bad poll must never end the loop
                logger.error(f"steward poll failed: {e}", exc_info=True)
            time.sleep(interval)


_bridge: StewardBridge | None = None


def bridge() -> StewardBridge | None:
    """The process-wide bridge, or None when the steward is not configured."""
    return _bridge


def start(slack_client) -> StewardBridge | None:
    """Start the poller in a daemon thread. A no-op without a channel to
    post into — the steward is opt-in, and a half-configured one that parks
    runs nobody ever sees would be worse than none."""
    global _bridge

    if not config.STEWARD_SLACK_CHANNEL:
        logger.info("STEWARD_SLACK_CHANNEL not set, steward bridge disabled")
        return None

    channel = config.STEWARD_SLACK_CHANNEL
    if not _CHANNEL_ID.match(channel):
        # A channel name would post fine and then strand every approval.
        # Slack events report `channel` as an ID, so the thread row we
        # write under a name can never be matched to the reply that comes
        # back, and the run parks until someone notices by hand.
        logger.error(
            f"STEWARD_SLACK_CHANNEL={channel!r} is not a channel ID, steward bridge "
            "disabled. Use the ID (Slack: channel name -> View channel details, "
            "bottom of the About tab)."
        )
        return None

    _bridge = StewardBridge(slack_client, channel=config.STEWARD_SLACK_CHANNEL)
    threading.Thread(target=_bridge.run_forever, daemon=True, name="steward-bridge").start()
    return _bridge
