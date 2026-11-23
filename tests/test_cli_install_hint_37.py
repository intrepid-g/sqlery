"""#37: a console script on a bare install names the extra to install instead of crashing."""

import subprocess
import sys

import pytest

from sqlery._cli import install_hint


@pytest.mark.parametrize(
    "missing, extra",
    [("typer", "cli"), ("rich.console", "cli"), ("uvicorn", "standalone"), ("sqlmodel", "standalone")],
)
def test_a_missing_cli_package_names_the_extra_that_provides_it(missing, extra):
    assert f'pip install "sqlery[{extra}]"' in install_hint(missing)


def test_an_unrelated_missing_module_is_not_blamed_on_an_extra():
    assert install_hint("my_task_module") is None
    assert install_hint(None) is None


def test_the_launcher_exits_with_the_hint_when_typer_is_missing():
    code = (
        "import sys; sys.modules['typer'] = None\n"  # None in sys.modules makes `import typer` fail
        "from sqlery._cli import main; main()"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 1
    assert 'pip install "sqlery[cli]"' in out.stderr
    assert "Traceback" not in out.stderr
