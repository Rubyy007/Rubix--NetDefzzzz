"""
setup.py for RUBIX pip package.

The post_install command runs rubix_installer.install.main() automatically
after `pip install rubix` completes — the user never needs to run a
separate setup step.
"""

from setuptools import setup
from setuptools.command.install import install
from setuptools.command.develop import develop
import sys


class _PostInstall(install):
    """Run the RUBIX installer after pip places the package files."""

    def run(self):
        # Run normal pip install first (copies files into site-packages)
        super().run()
        # Then run our installer
        self._run_installer()

    @staticmethod
    def _run_installer():
        try:
            from rubix_installer.install import main
            main()
        except SystemExit as e:
            # install.py calls sys.exit(1) on fatal errors — re-raise so
            # pip reports a failed install rather than silently succeeding.
            raise
        except Exception as e:
            print(f"\n  [RUBIX] Installer error: {e}", file=sys.stderr)
            print(  "  [RUBIX] The Python package was installed but RUBIX binaries", file=sys.stderr)
            print(  "  [RUBIX] may not be in place. Run: rubix-install", file=sys.stderr)


class _PostDevelop(develop):
    """Same hook for `pip install -e .` (editable installs / dev mode)."""

    def run(self):
        super().run()
        _PostInstall._run_installer()


setup(
    cmdclass={
        "install": _PostInstall,
        "develop": _PostDevelop,
    },
)
