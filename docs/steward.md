# The steward

A second agent on Mimir's worker. Once a day it reads the state of the Norns
repos, puts one decision-ready proposal in Slack, and waits — for as long as
it takes — for an answer. When the answer approves work, it hands each item
to the coding agent and reports back in the same thread.

```
cron trigger (Norns)
      │
      ▼
norns-steward ──► GitHub API, issues, commits, docs, memory
      │
      │ ask_human
      ▼
  run parks in Norns ──────────────┐
      ▲                            │ steward bridge polls
      │ POST /runs/:id/reply       ▼
   Slack thread ◄──────────── proposal posted
      │
      │ "do the first one"
      ▼
norns-steward ──► launch_agent ──► sleipnir (on a gard, with the checkout)
      │
      ▼
  report in the same thread
```

## Why it is two agents and two machines

The planner needs no checkout and must run when the laptop is shut, so it
lives on Mimir's worker on Fly and reads GitHub over the API. The coder needs
a real working tree, a shell, and a permission allow list, so it lives on a
gard on a real machine.

That split is also the safety argument. The steward holds no write tools —
`list_github_issues`, `read_github_file`, `search_memory` and so on, plus
`remember`. It cannot edit a file or run a command on its own judgment at
6am, and `allowed_tools` on its registration is what makes that true rather
than merely intended: Norns offers an agent *every* tool in the tenant unless
told otherwise, so without the allowlist the steward would be handed
sleipnir's `bash` the moment that worker connected. Core enforces the
allowlist twice — the tool list the model is shown, and again at dispatch —
and records refusals as `tool_call_denied`.

## Why the wait is Norns' and not ours

`ask_human` parks the run in the orchestrator. Nothing is holding it open:
the cron trigger that started the run has long returned, and the process that
posted to Slack can be redeployed, evicted, or restarted without the proposal
going anywhere. Answering tomorrow resumes *that* run, with its survey still
in context, rather than starting a new one that has to read everything again.

A blocking tool call would have been about forty lines shorter and would have
lost exactly that. It is the reason this is a Norns agent instead of a cron
script.

The cost is that a parked run has no connection to anybody, so nothing knows
to look at it. That is what the bridge is for.

## The bridge

`mimir_agent/steward_bridge.py`, a daemon thread in the Mimir process.

- **Out:** every `STEWARD_POLL_SECONDS` it asks Norns for steward runs with
  status `waiting`, and posts any question not already in Slack. The
  `steward_asks` row — run id, channel, thread — is the record of "already
  posted", written *after* Slack accepts the message. Marking a question
  posted that nobody saw is the one failure with no way back, since the run
  would then park forever; a failed post is simply retried next poll.
- **In:** a reply in a recorded thread is routed to `POST /runs/:id/reply`
  instead of starting a new Mimir run. This is checked before the channel →
  project prefix is added, because an approval is not a question for Mimir.
- **Out again:** once an answered run reaches `completed` or `failed`, its
  outcome is posted into the thread the question came from. A run that parks
  again instead has asked a follow-up, and the row stays open for it.

State lives in Postgres, so a redeploy mid-wait loses nothing: the next poll
finds the same parked run and the row saying it was already posted.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `STEWARD_SLACK_CHANNEL` | — | Channel id for proposals. **Unset disables the steward bridge entirely.** |
| `STEWARD_AGENT` | `norns-steward` | Agent name to register and watch. |
| `STEWARD_MODEL` | `claude-opus-5` | The planning run is judgment-heavy and runs once a day. |
| `STEWARD_CODER_AGENT` | `sleipnir` | The only agent it may launch. |
| `STEWARD_POLL_SECONDS` | `20` | How often the bridge looks for a parked run. |

The bridge is off without a channel. A half-configured steward that parks
runs nobody ever sees is worse than no steward.

## The triggers

Schedules are Norns data, not config in this repo. Cron is UTC.

`nornsctl triggers create --agent` takes the numeric agent id, which
`nornsctl agents list` prints once the worker has registered the steward.

```bash
STEWARD_ID=$(nornsctl agents list | awk '$2 == "norns-steward" { print $1 }')

# Weekday planning run, 13:00 UTC (06:00 PT)
nornsctl triggers create \
  --agent "$STEWARD_ID" \
  --name steward-daily \
  --cron '0 13 * * 1-5' \
  --message 'This is a planning run. Survey the Norns repos and put one decision-ready proposal in front of me, following your planning-run instructions. Use the current date from your system prompt for memory keys.'

# Weekly narrative run, Monday 14:00 UTC
nornsctl triggers create \
  --agent "$STEWARD_ID" \
  --name steward-narrative \
  --cron '0 14 * * 1' \
  --message 'This is a narrative run. Look at what has been learned since your last one and propose either a reframe, a "Built on Norns" agent worth experimenting with, or something the project is getting wrong. Follow your narrative-run instructions.'
```

No `conversation_key`: each firing is a fresh run. Continuity comes from
memory under the `norns-steward` project, not from an ever-growing thread —
which is also what stops it re-proposing something that was turned down.
`nornsctl triggers fire <id>` runs one now, outside its schedule.

## Handing work over

On approval the steward records the decision, then `launch_agent`s the coder
once per work item, serially, so a bad first result stops the rest.

If the coder is not connected — laptop shut, usually — its tool tasks queue
in Norns until it is. That is the designed behaviour, not a failure: the
steward says the work is queued and stops, and the child run picks up when
the machine comes back.

## What it is told not to do

Two instructions in the prompt matter more than the rest.

**Proposing nothing is a real answer.** A daily agent that must find work
will invent it, and an agent that always finds something to do is one nobody
reads. If the honest reading is that the work in flight should be finished,
it says so and asks to stand down for the day.

**A reframe that makes the same claim in warmer words is worse than leaving
it alone,** and a "Built on Norns" idea that would work just as well as a
cron script and a text file is not a demo of anything. It is told to name
which primitive — the durable wait, the cross-worker handoff, replay, gard
affinity — actually carries the agent it is proposing.
