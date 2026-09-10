"""Egress control for containerised bricks (Milestone 2 / A3).

``isolation.network`` in a manifest is one of three postures:

* **``false``** (the default) — ``docker run --network none``. No socket to anything. This is what
  a brick that touches conversation content gets.
* **``true`` + non-empty ``allow_hosts``** — the brick runs on an ``--internal`` Docker network
  with no route of its own; a sidecar :mod:`veridian.security.egress_proxy` is the only way out and
  refuses every destination not on ``allow_hosts``. ``HTTPS_PROXY`` is injected so the brick's HTTP
  client uses it without code changes.
* **``true`` + empty ``allow_hosts``** — unrestricted bridge networking. Legal, but
  :func:`veridian.plugin_runtime.registry.check_egress_declared` refuses it for any brick that also
  implements a conversation-content contract.

This module only *builds the commands*. ``ContainerSpawn`` runs them as a brick's ``pre_run`` /
``post_run`` and points the brick at the proxy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from veridian.plugin_runtime.manifest import IsolationSpec
from veridian.security import egress_proxy

PROXY_PORT = 8888
PROXY_FILE_IN_CONTAINER = "/veridian_egress_proxy.py"
_PROXY_SOURCE = Path(egress_proxy.__file__).resolve()


@dataclass(frozen=True)
class EgressPlan:
    """Everything ``ContainerSpawn`` needs to enforce a brick's egress posture.

    ``network_args`` go straight into the brick's ``docker run``. ``env`` is merged into the
    brick's environment (``HTTPS_PROXY`` &c.). ``pre_run`` / ``post_run`` are engine commands the
    kernel runs around the brick's own container; ``post_run`` is best-effort.
    """

    network_args: tuple[str, ...]
    env: dict[str, str]
    pre_run: tuple[tuple[str, ...], ...] = ()
    post_run: tuple[tuple[str, ...], ...] = ()
    proxied_to: tuple[str, ...] = ()  # allow_hosts this plan enforces, for logging/audit


def _csv(hosts: tuple[str, ...]) -> str:
    return ",".join(hosts)


def plan_egress(spec: IsolationSpec, *, engine: str, image: str, suffix: str | None = None) -> EgressPlan:
    """Build the :class:`EgressPlan` for a container brick's isolation spec."""
    if not spec.network:
        return EgressPlan(network_args=("--network", "none"), env={})

    if not spec.allow_hosts:
        # Unrestricted. The registry decides whether this brick is allowed to be; here we just
        # leave it on the engine's default bridge.
        return EgressPlan(network_args=(), env={})

    sfx = suffix or uuid.uuid4().hex[:12]
    net_int = f"veridian-egress-int-{sfx}"
    net_ext = f"veridian-egress-ext-{sfx}"
    proxy = f"veridian-egress-proxy-{sfx}"
    allow = _csv(spec.allow_hosts)
    proxy_url = f"http://{proxy}:{PROXY_PORT}"

    pre_run = (
        (engine, "network", "create", "--internal", net_int),
        (engine, "network", "create", net_ext),
        (
            engine, "run", "-d", "--rm", "--name", proxy,
            "--network", net_int,
            "-v", f"{_PROXY_SOURCE.as_posix()}:{PROXY_FILE_IN_CONTAINER}:ro",
            "-e", f"VERIDIAN_ALLOW_HOSTS={allow}",
            "-e", f"VERIDIAN_PROXY_PORT={PROXY_PORT}",
            image, "python", PROXY_FILE_IN_CONTAINER,
        ),
        (engine, "network", "connect", net_ext, proxy),
    )
    post_run = (
        (engine, "rm", "-f", proxy),
        (engine, "network", "rm", net_int),
        (engine, "network", "rm", net_ext),
    )
    env = {
        "HTTPS_PROXY": proxy_url,
        "HTTP_PROXY": proxy_url,
        "https_proxy": proxy_url,
        "http_proxy": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }
    return EgressPlan(
        network_args=("--network", net_int),
        env=env,
        pre_run=pre_run,
        post_run=post_run,
        proxied_to=spec.allow_hosts,
    )
