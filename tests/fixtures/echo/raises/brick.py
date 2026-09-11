"""A handler that raises an ordinary exception (not SystemExit). The SDK must turn it into a
brick_internal_error that *carries* the brick name, method, and full traceback, and also write
that traceback to VERIDIAN_HOME/logs/brick-errors.log — otherwise a run failure reaches the user
as one context-free line."""

from veridian.sdk import Brick, rpc, run


class Raises(Brick):
    name = "echo/raises"
    version = "1.0.0"
    implements = {"echo": ["say"]}

    @rpc("echo.say")
    async def say(self, params, ctx):
        marker = None
        return {"text": marker + " boom"}  # TypeError: NoneType + str


if __name__ == "__main__":
    run(Raises())
