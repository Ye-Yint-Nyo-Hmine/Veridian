"""The ``conversation`` contract: local, first-class chat history.

Distinct from ``memory``. ``memory`` is what an agent chooses to remember; ``conversation`` is the
verbatim, ordered turn log of a session. It is stored on the user's device and never leaves it —
that is the Version 0 privacy property this contract exists to make concrete.
"""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="conversation",
    version="1.0",
    schema_file="protocol/conversation.schema.json",
    methods=_methods(
        MethodSpec("append"),
        MethodSpec("load"),
        MethodSpec("list_sessions"),
        MethodSpec("delete"),
    ),
)
