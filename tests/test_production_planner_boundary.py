"""Production entrypoints cannot select or import the Python search oracle."""

import subprocess
import sys


def test_production_imports_do_not_load_python_search_modules():
    code = (
        "import sys; import mclab.companion_body, mclab.natural, mclab.native_driver; "
        "forbidden=('mclab.route_search','mclab.terrain_route',"
        "'mclab.planning_worker'); "
        "assert not set(forbidden) & sys.modules.keys()"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_python_planner_cli_option_is_not_available():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mclab.cli",
            "natural",
            "run",
            "--seed",
            "1337",
            "--routes",
            "missing.json",
            "--python-planner",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments: --python-planner" in result.stderr
