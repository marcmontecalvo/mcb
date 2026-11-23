"""`mcplay`: the normal CLI bound to the play instance."""

import os


def main() -> int:
    # Instance paths are computed when mclab.paths is first imported, so the
    # environment must be set before anything from mclab is imported.
    os.environ["MCLAB_INSTANCE"] = "play"
    from mclab import cli

    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
