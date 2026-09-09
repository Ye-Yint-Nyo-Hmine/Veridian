"""planning/default — a linear plan handed out one step at a time.

An observation of ``{"status": "failed"}`` for the active step marks it failed and stops; anything
else advances. If an inference brick is bound and ``use_inference`` is set, the goal is decomposed
by a model; otherwise the goal is split on ``and`` / ``then`` / newlines.
"""

from __future__ import annotations

import re
import uuid

from veridian.sdk import Brick, rpc, run

_SPLIT = re.compile(r"\s+(?:and then|then|and)\s+|[\n;]+", re.I)


class PlanningDefault(Brick):
    name = "planning/default"
    version = "0.1.0"
    implements = {"planner": ["plan", "next", "is_complete"]}

    async def on_initialize(self) -> bool:
        self._plans: dict[str, dict] = {}
        self.use_inference = bool(self.config.get("use_inference", False))
        return True

    def _split(self, goal: str) -> list[str]:
        parts = [p.strip(" .") for p in _SPLIT.split(goal) if p and p.strip(" .")]
        return parts or [goal.strip()]

    async def _decompose(self, goal: str) -> list[str]:
        if not self.use_inference:
            return self._split(goal)
        try:
            res = await self.host.contract_call(
                "inference",
                "generate",
                {
                    "messages": [
                        {"role": "user", "content": f"Break this task into 2-6 numbered imperative steps. Task: {goal}"}
                    ],
                    "max_tokens": 300,
                },
            )
            content = res["message"]["content"]
            if isinstance(content, list):
                content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            steps = [re.sub(r"^\s*\d+[.)]\s*", "", ln).strip() for ln in content.splitlines() if ln.strip()]
            return steps or self._split(goal)
        except Exception:  # noqa: BLE001
            return self._split(goal)

    @rpc("planner.plan")
    async def plan(self, params, ctx):
        plan_id = uuid.uuid4().hex
        descs = await self._decompose(params["goal"])
        steps = [
            {"id": f"s{i}", "description": d, "status": "pending"} for i, d in enumerate(descs)
        ]
        self._plans[plan_id] = {"goal": params["goal"], "steps": steps, "cursor": 0}
        return {"plan_id": plan_id, "steps": steps}

    @rpc("planner.next")
    async def next_(self, params, ctx):
        plan = self._plans[params["plan_id"]]
        steps = plan["steps"]
        observations = params.get("observations", [])

        # Resolve the currently-active step from the latest observation.
        for i, s in enumerate(steps):
            if s["status"] == "active":
                failed = any(o.get("status") == "failed" for o in observations)
                s["status"] = "failed" if failed else "done"
                if failed:
                    return {"step": None}
                plan["cursor"] = i + 1
                break

        for s in steps[plan["cursor"] :]:
            if s["status"] == "pending":
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
    run(PlanningDefault())
