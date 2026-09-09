"""planning/tree-search — maintain several candidate branches and walk the best-scoring one.

Each branch is an ordering of the same step set. A failed observation penalises the active
branch; ``next`` switches to the branch with the best score and resumes it. This is a deliberately
small search — its point is that a different planning *strategy* drops into the same contract.
"""

from __future__ import annotations

import itertools
import re
import uuid

from veridian.sdk import Brick, rpc, run

_SPLIT = re.compile(r"\s+(?:and then|then|and)\s+|[,\n;]+", re.I)


class PlanningTreeSearch(Brick):
    name = "planning/tree-search"
    version = "0.1.0"
    implements = {"planner": ["plan", "next", "is_complete"]}

    async def on_initialize(self) -> bool:
        self._plans: dict[str, dict] = {}
        self.max_branches = int(self.config.get("max_branches", 3))
        return True

    @rpc("planner.plan")
    async def plan(self, params, ctx):
        plan_id = uuid.uuid4().hex
        clauses = [c.strip(" .") for c in _SPLIT.split(params["goal"]) if c and c.strip(" .")]
        clauses = clauses or [params["goal"].strip()]

        orderings = list(itertools.islice(itertools.permutations(range(len(clauses))), self.max_branches))
        if not orderings:
            orderings = [(0,)]
        branches = [
            {
                "steps": [
                    {"id": f"b{b}s{i}", "description": clauses[o], "status": "pending"} for i, o in enumerate(order)
                ],
                "score": 0.0,
                "cursor": 0,
            }
            for b, order in enumerate(orderings)
        ]
        self._plans[plan_id] = {"goal": params["goal"], "branches": branches, "active": 0}
        return {"plan_id": plan_id, "steps": [
            {"id": s["id"], "description": s["description"], "status": s["status"]}
            for s in branches[0]["steps"]
        ]}

    @rpc("planner.next")
    async def next_(self, params, ctx):
        plan = self._plans[params["plan_id"]]
        branches = plan["branches"]
        observations = params.get("observations", [])
        cur = branches[plan["active"]]

        for i, s in enumerate(cur["steps"]):
            if s["status"] == "active":
                if any(o.get("status") == "failed" for o in observations):
                    s["status"] = "failed"
                    cur["score"] -= 1.0
                else:
                    s["status"] = "done"
                    cur["score"] += 1.0
                    cur["cursor"] = i + 1
                break

        # Pick the branch with the best score that still has pending steps.
        candidates = [
            (b["score"], idx)
            for idx, b in enumerate(branches)
            if any(s["status"] == "pending" for s in b["steps"])
        ]
        if not candidates:
            return {"step": None}
        plan["active"] = max(candidates)[1]
        chosen = branches[plan["active"]]
        for s in chosen["steps"]:
            if s["status"] == "pending":
                s["status"] = "active"
                return {"step": {"id": s["id"], "description": s["description"]}}
        return {"step": None}

    @rpc("planner.is_complete")
    async def is_complete(self, params, ctx):
        branches = self._plans[params["plan_id"]]["branches"]
        if any(all(s["status"] == "done" for s in b["steps"]) for b in branches):
            return {"complete": True, "reason": "a branch completed"}
        if all(any(s["status"] == "failed" for s in b["steps"]) for b in branches):
            return {"complete": True, "reason": "every branch failed"}
        return {"complete": False, "reason": "branches still open"}


if __name__ == "__main__":
    run(PlanningTreeSearch())
