"""
Build a standalone Windows executable for the Page Numbering Tool.
"""

import os
import shutil
import subprocess
import sys

script_dir = os.path.dirname(os.path.realpath(__file__))
desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
main_script = os.path.join(script_dir, "main.py")
icon_path = os.path.join(script_dir, "PageNumber.ico")
fonts_json = os.path.join(script_dir, "fonts.json")


def ensure_pyinstaller() -> None:
    """Install PyInstaller into the current interpreter if it is missing."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )


def build_command() -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--noconsole",
        "--name",
        "PageNumberingTool",
        "--icon",
        icon_path,
        "--exclude-module",
        "__pycache__",
        "--clean",
        "--distpath",
        desktop_dir,
        "--workpath",
        os.path.join(script_dir, "build"),
        "--specpath",
        script_dir,
    ]
    if os.path.isfile(fonts_json):
        # Windows: source;destination inside the bundle
        cmd.extend(["--add-data", f"{fonts_json};."])
    cmd.append(main_script)
    return cmd


def cleanup_artifacts() -> None:
    for directory in ("build", "__pycache__"):
        path = os.path.join(script_dir, directory)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            print(f"Cleaned up {directory}/")

    spec_path = os.path.join(script_dir, "PageNumberingTool.spec")
    if os.path.isfile(spec_path):
        os.remove(spec_path)
        print(f"Cleaned up {os.path.basename(spec_path)}")


def main() -> int:
    ensure_pyinstaller()
    command = build_command()

    print("Running PyInstaller with the following command:")
    print(" ".join(command))

    result = subprocess.run(
        command,
        cwd=script_dir,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        print("PyInstaller failed.")
        return result.returncode

    print(f"Build succeeded. Executable is on the Desktop: PageNumberingTool.exe")
    cleanup_artifacts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
