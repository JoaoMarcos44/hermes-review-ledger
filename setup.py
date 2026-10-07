"""Build the native directory payload from its authoritative source files."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPlugin(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / "review_ledger" / "_plugin_payload"
        if destination.exists():
            shutil.rmtree(destination)
        names = [Path("__init__.py"), Path("plugin.yaml")]
        names += [p.relative_to(root) for p in (root / "review_ledger").glob("*.py")]
        names += [p.relative_to(root) for p in (root / "review_ledger" / "migrations").glob("*.sql")]
        names += [p.relative_to(root) for p in (root / "skills").rglob("*.md")]
        names += [p.relative_to(root) for p in (root / "review_ledger" / "resources").glob("*.md")]
        names += [p.relative_to(root) for p in (root / "review_ledger" / "prompts").glob("*.md")]
        for name in names:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)


setup(cmdclass={"build_py": BuildPlugin})
