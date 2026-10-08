"""Build both copies of the native payload from one authoritative snapshot."""
from pathlib import Path
import runpy
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPlugin(build_py):
    def run(self):
        root = Path(__file__).resolve().parent
        # Load only the stdlib-only inventory module, without importing runtime
        # dependencies or an unrelated installed review_ledger package.
        read_payload = runpy.run_path(str(root / "review_ledger" / "payload_manifest.py"))["read_payload"]
        payload = read_payload(root)
        package = Path(self.build_lib) / "review_ledger"
        source = root / "review_ledger"
        if (package.resolve().is_relative_to(source.resolve())
                or source.resolve().is_relative_to(package.resolve())):
            raise ValueError("Build output must not overlap the source package")
        for component in (package.absolute(), *package.absolute().parents):
            if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
                raise ValueError("Build output must not contain a symbolic link or junction")
        # Incremental build_py relies on mtimes and retains removed modules.
        # A clean destination prevents stale source from entering a new wheel.
        if package.exists():
            shutil.rmtree(package)
        super().run()
        destination = package / "_plugin_payload"
        for name, data in payload.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if name.startswith("review_ledger/"):
                # Keep the importable package and installer snapshot identical,
                # even if build_py cached a source listing or source mtimes.
                target = Path(self.build_lib) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)


setup(cmdclass={"build_py": BuildPlugin})
