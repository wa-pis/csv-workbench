"""Build and smoke-test a standalone bundle on the current OS."""

import os
import platform
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PyInstaller.__main__ import run


def main():
    os.chdir(Path(__file__).resolve().parents[1])
    name = "csv-workbench"
    run(
        [
            "--noconfirm",
            "--onedir",
            "--windowed",
            "--hidden-import=uuid",
            "--name",
            name,
            "src/csv_workbench/main.py",
        ]
    )
    bundle = Path("dist") / name
    executable = bundle / name
    if sys.platform == "darwin":
        bundle = bundle.with_suffix(".app")
        executable = bundle / "Contents" / "MacOS" / name
    elif sys.platform == "win32":
        executable = executable.with_suffix(".exe")
    subprocess.run([str(executable.resolve()), "--smoke-test"], check=True, timeout=60)

    if sys.platform == "darwin":
        # Exercise the bundled Cocoa plugin; offscreen smoke tests cannot catch QTBUG-149612.
        subprocess.run([str(executable.resolve()), "--accessibility-smoke-test"],
                       check=True, timeout=60)

    archive = bundle.parent / f"{name}-{platform.system()}-{platform.machine()}.zip"
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(bundle), str(archive)], check=True)
    elif sys.platform.startswith("linux"):
        with tarfile.open(archive.with_suffix(".tar.gz"), "w:gz") as output:
            output.add(bundle, arcname=bundle.name)
    else:
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
            for path in bundle.rglob("*"):
                if path.is_file():
                    output.write(path, path.relative_to(bundle.parent))


if __name__ == "__main__":
    main()
