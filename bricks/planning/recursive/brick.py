"""planning/recursive — walk a plan, expanding any step that still looks compound into sub-steps.

A step is "compound" if it splits on `and` / `then` / `,` into more than one clause and has not
already been expanded. Expansion happens lazily in ``next`` up to ``max_depth``.
"""

from __future__ import annotations

import re
import uuid

from veridian.sdk import Brick, rpc, run

_SPLIT = re.compile(r"\s+(?:and then|then|and)\s+|[,\n;]+", re.I)


class PlanningRecursive(Brick):
    name = "planning/recursive"
    version = "0.1.0"
    implements = {"planner": ["plan", "next", "is_complete"]}

    async def on_initialize(self) -> bool:
        self._plans: dict[str, dict] = {}
        self.max_depth = int(self.config.get("max_depth", 3))
        return True

    def _clauses(self, text: str) -> list[str]:
        return [c.strip(" .") for c in _SPLIT.split(text) if c and c.strip(" .")]

    @rpc("planner.plan")
    async def plan(self, params, ctx):
        plan_id = uuid.uuid4().hex
        clauses = self._clauses(params["goal"]) or [params["goal"].strip()]
        steps = [
            {"id": f"s{i}", "description": c, "status": "pending", "depth": 0}
            for i, c in enumerate(clauses)
        ]
        self._plans[plan_id] = {"goal": params["goal"], "steps": steps, "counter": len(steps)}
        return {"plan_id": plan_id, "steps": [self._public(s) for s in steps]}

    def _public(self, s: dict) -> dict:
        return {"id": s["id"], "description": s["description"], "status": s["status"]}

    @rpc("planner.next")
    async def next_(self, params, ctx):
        plan = self._plans[params["plan_id"]]
        steps = plan["steps"]
        observations = params.get("observations", [])

        for s in steps:
            if s["status"] == "active":
                s["status"] = "failed" if any(o.get("status") == "failed" for o in observations) else "done"
                if s["status"] == "failed":
                    return {"step": None}
                break

        for idx, s in enumerate(steps):
            if s["status"] != "pending":
                continue
            clauses = self._clauses(s["description"])
            if len(clauses) > 1 and s["depth"] < self.max_depth:
                subs = []
                for c in clauses:
                    plan["counter"] += 1
                    subs.append(
                        {"id": f"s{plan['counter']}", "description": c, "status": "pending", "depth": s["depth"] + 1}
                    )
                steps[idx : idx + 1] = subs
                s = subs[0]
            s["status"] = "active"
            return {"step": {"id": s["id"], "description": s["description"]}}
        return {"step": None}

    @rpc("planner.is_complete")
    async def is_complete(self, params, ctx):
        steps = self._plans[params["plan_id"]]["steps"]
        if any(s["status"] == "failed" for s in steps):
            return {"complete": True, "reason": "a step failed"}
        done = all(s["status"] == "done" for s in steps)
        return {"complete": done, "reason": "all steps done" if done else "steps remain"}


if __name__ == "__main__":
    run(PlanningRecursive())
