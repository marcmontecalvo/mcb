"""`mcplay` and `mcarena`: the normal CLI bound to the play or the arena instance."""

import os


def run(instance: str) -> int:
    """The lab CLI (``mclab.cli``) on ``instance``'s server, ports and runtime."""
    # Instance paths are computed when mclab.paths is first imported, so the
    # environment must be set before anything from mclab is imported.
    os.environ["MCLAB_INSTANCE"] = instance
    from mclab import cli

    return cli.main()


def main() -> int:
    """`mcplay`: the survival world the bot lives in."""
    return run("play")


def arena_main() -> int:
    """`mcarena`: the void world with the lobby and the arenas."""
    return run("arena")


if __name__ == "__main__":
    raise SystemExit(main())
