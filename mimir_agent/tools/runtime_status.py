"""What the runtime can actually serve right now.

The steward hands approved work to a coding agent on another machine. Whether
that machine is there is not a thing it can infer: an agent row exists whether
or not any worker serves it, and `launch_agent` into an agent nobody serves
does not queue politely — the child's first LLM task goes unanswered and the
run fails on the five-minute task timeout.

So it gets to look first. One read-only tool, so an unattended run can say
"the coder is offline, I have recorded the plan" instead of spending five
minutes failing.
"""

from __future__ import annotations

import httpx

from norns import tool

from mimir_agent import config

TIMEOUT = 10.0


def _get(path: str) -> list[dict]:
    resp = httpx.get(
        f"{config.NORNS_URL.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {config.NORNS_API_KEY}"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    # /api/v1/tools answers with a bare list; the others wrap theirs in "data".
    return body if isinstance(body, list) else body.get("data", [])


@tool
def runtime_status() -> str:
    """Which agents are registered and which workers are connected right now.

    Call this before handing work to another agent. An agent being listed
    means it is registered; it does not mean anything is there to run it. Work
    handed to an agent whose tools no connected worker provides does not wait
    — the run fails after five minutes.
    """
    try:
        agents = _get("/api/v1/agents")
    except Exception as e:
        return f"Could not read agents: {e}"

    try:
        workers = _get("/api/v1/workers")
    except Exception as e:
        workers = []
        worker_note = f"\nCould not read workers: {e}"
    else:
        worker_note = ""

    lines = [f"Runtime: {config.NORNS_URL}", "", "Agents registered:"]
    for a in sorted(agents, key=lambda a: a.get("name") or ""):
        lines.append(f"  {a.get('id')}  {a.get('name')}  ({a.get('model')})")

    lines.append("")
    if workers:
        lines.append("Workers connected:")
        for w in workers:
            caps = ",".join(w.get("capabilities") or []) or "none"
            gard = w.get("gard")
            where = f" gard={gard}" if gard else ""
            lines.append(
                f"  {w.get('worker_id')}  capabilities={caps}{where}  "
                f"tools={w.get('tool_count', 0)}"
            )
    else:
        lines.append("Workers connected: none.")
        lines.append(
            "  Nothing can run right now — no worker is holding a connection. "
            "Do not hand work over; report this and stop."
        )

    coder = config.STEWARD_CODER_AGENT
    names = {a.get("name") for a in agents}
    lines.append("")
    if coder not in names:
        lines.append(
            f"The coding agent '{coder}' is NOT registered on this runtime. It cannot be "
            f"launched here at all — this is not a temporary outage. Report it and stop."
        )
    elif not workers:
        lines.append(f"'{coder}' is registered, but no worker is connected to serve it.")
    else:
        try:
            catalog = _get("/api/v1/tools")
        except Exception as e:
            lines.append(f"'{coder}' is registered, but the tool catalog did not read: {e}")
        else:
            worker_tools = sorted(
                t.get("name", "") for t in catalog if t.get("source") != "builtin"
            )
            lines.append(
                f"'{coder}' is registered. {len(worker_tools)} worker-provided tools "
                f"are being served: {', '.join(worker_tools) or 'none'}."
            )

    return "\n".join(lines) + worker_note
