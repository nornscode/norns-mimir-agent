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
import threading
import time

from norns import NornsClient

from mimir_agent import config, db

logger = logging.getLogger("mimir_agent.steward")

# Slack rejects a message over 40k; a proposal should be nowhere near this.
MAX_SLACK_TEXT = 3500


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
        """Post any steward question not yet in Slack. Returns how many."""
        agent_id = self.agent_id()
        if agent_id is None:
            return 0

        try:
            runs = self.norns.list_runs(limit=100, status="waiting", agent_id=agent_id)
        except Exception as e:
            logger.warning(f"could not list waiting runs: {e}")
            return 0

        posted = 0
        for run in runs:
            if not run.waiting_for or not run.waiting_for.question:
                continue
            if db.steward_ask_posted(run.run_id):
                continue
            if self._post_one(run.run_id, run.waiting_for.question):
                posted += 1
        return posted

    def _post_one(self, run_id: int, question: str) -> bool:
        from mimir_agent.slack_bot import to_slack_mrkdwn

        header = f":thread: *Norns steward* — run `{run_id}`\n\n"
        try:
            resp = self.slack.chat_postMessage(
                channel=self.channel,
                text=_truncate(header + to_slack_mrkdwn(question)),
            )
            thread_ts = resp["ts"]
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

        Returns the run id if the reply was delivered, None if this thread
        belongs to no steward question — in which case the caller should
        handle the message normally.
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
            return None

        if not run.is_waiting:
            # Answered already, or it moved on. Say so rather than silently
            # dropping what the user typed.
            self._say(channel, thread_ts, f"Run `{run_id}` isn't waiting on an answer ({run.status}).")
            return None

        try:
            self.norns.reply(run_id, text)
        except Exception as e:
            logger.error(f"could not deliver answer to run {run_id}: {e}")
            self._say(channel, thread_ts, f"I couldn't deliver that to run `{run_id}`: {e}")
            return None

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
        logger.info(f"steward bridge watching {config.STEWARD_AGENT} → {self.channel} every {interval}s")
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

    _bridge = StewardBridge(slack_client, channel=config.STEWARD_SLACK_CHANNEL)
    threading.Thread(target=_bridge.run_forever, daemon=True, name="steward-bridge").start()
    return _bridge
