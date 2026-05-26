"""
setup.py for RUBIX pip package.

The post-install hook runs rubix_installer.install.main() automatically
after  pip install rubix-defzz  completes on the end user's machine.

It does NOT run during  python -m build  — that would fail because
rubix_installer is not on sys.path during the build step.
"""

import sys
import os
from setuptools import setup
from setuptools.command.install import install
from setuptools.command.develop import develop


def _is_build_step() -> bool:
    """
    Return True when we are inside  python -m build  (bdist_wheel / sdist).
    In this case we must NOT invoke the installer — the package files are
    not yet on sys.path and the end user's machine is not the target.
    """
    building = any(arg in sys.argv for arg in (
        "bdist_wheel", "bdist_egg", "sdist", "egg_info",
        "build", "build_py", "build_ext",
    ))
    # Also set by pip's isolated build environment
    pip_build = os.environ.get("PIP_BUILD_TRACKER") is not None
    return building or pip_build


class _PostInstall(install):
    def run(self):
        super().run()
        if _is_build_step():
            return
        self._invoke()

    @staticmethod
    def _invoke():
        try:
            from rubix_installer.install import main
            main()
        except SystemExit:
            raise
        except Exception as exc:
            print(f"\n  [RUBIX] Installer error: {exc}", file=sys.stderr)
            print("  [RUBIX] Run  rubix-install  to retry.", file=sys.stderr)
            sys.exit(1)


class _PostDevelop(develop):
    def run(self):
        super().run()
        if _is_build_step():
            return
        _PostInstall._invoke()


setup(
    cmdclass={
        "install": _PostInstall,
        "develop": _PostDevelop,
    },
)