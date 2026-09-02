"""Dependency requirements for the application.

This file represents the original requirements.py.txt content as valid Python.
Install the dependencies with:

    pip install -r requirements.py.txt

or programmatically with the ``install_requirements`` helper below.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REQUIREMENTS: tuple[str, ...] = (
    "aiohttp>=3.9.0",
    "aiohttp-socks>=0.8.4",
)


def install_requirements() -> None:
    """Install the declared dependencies into the active Python environment."""
    subprocess.check_call([sys.executable, "-m", "pip", "install", *REQUIREMENTS])


def write_requirements_file(path: str | Path = "requirements.txt") -> Path:
    """Write the dependency specifications to a standard requirements file."""
    output_path = Path(path)
    output_path.write_text("\n".join(REQUIREMENTS) + "\n", encoding="utf-8")
    return output_path


if __name__ == "__main__":
    print("Declared requirements:")
    print("\n".join(f"- {requirement}" for requirement in REQUIREMENTS))
    print("\nTo install them, run: python -c 'import requirements; requirements.install_requirements()'")
