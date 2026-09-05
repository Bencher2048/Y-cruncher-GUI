from pathlib import Path
import subprocess
import sys
import threading
from typing import Iterable, Optional


def _candidate_ycruncher_paths() -> list[Path]:
    """Return all sensible locations where y-cruncher may be installed.

    The important case for PyInstaller --onedir is:
        dist/y-cruncher GUI 2.0/y-cruncher/y-cruncher.exe
    """
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        # --onedir: the executable lives in the dist application folder.
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "y-cruncher" / "y-cruncher.exe",
                exe_dir / "y-cruncher.exe",
            ]
        )

        # Also check PyInstaller's runtime directory. This makes the
        # launcher tolerant of both --onedir and older --onefile builds.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_dir = Path(meipass).resolve()
            candidates.extend(
                [
                    meipass_dir / "y-cruncher" / "y-cruncher.exe",
                    meipass_dir / "y-cruncher.exe",
                ]
            )
    else:
        # Running ui.py directly from the source project.
        project_root = Path(__file__).resolve().parents[1]
        candidates.extend(
            [
                project_root / "y-cruncher" / "y-cruncher.exe",
                project_root / "y-cruncher.exe",
            ]
        )

    # Finally, allow launching the GUI from another working directory.
    cwd = Path.cwd().resolve()
    candidates.extend(
        [
            cwd / "y-cruncher" / "y-cruncher.exe",
            cwd / "y-cruncher.exe",
        ]
    )

    # Remove duplicates while preserving order.
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)

    return unique


def find_ycruncher() -> Optional[Path]:
    """Find y-cruncher.exe and return its absolute path."""
    for path in _candidate_ycruncher_paths():
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


def get_ycruncher_path() -> str:
    path = find_ycruncher()
    return str(path) if path else ""


def file_exists() -> bool:
    return find_ycruncher() is not None


def _reader(stream, output_queue) -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(line)
    except Exception as exc:
        if output_queue is not None:
            output_queue.put(f"\n[Launcher] Ошибка чтения вывода y-cruncher: {exc}\n")
    finally:
        try:
            stream.close()
        except Exception:
            pass


def start_test(
    selected_tests: Iterable[str],
    minutes: float,
    output_queue=None,
) -> Optional[subprocess.Popen]:
    ycruncher_path = find_ycruncher()
    if ycruncher_path is None:
        if output_queue is not None:
            output_queue.put(
                "[Launcher] y-cruncher.exe не найден. "
                "Ожидаемый путь: <папка программы>\\y-cruncher\\y-cruncher.exe\n"
            )
        return None

    try:
        duration = float(minutes)
    except (TypeError, ValueError):
        return None

    if duration <= 0:
        return None

    cli_tests = list(dict.fromkeys(selected_tests))
    if not cli_tests:
        return None

    seconds = max(1, round(duration * 60))
    command = [
        str(ycruncher_path),
        "stress",
        f"-TL:{seconds}",
        *cli_tests,
    ]

    # y-cruncher should run with its own directory as the working directory.
    # This is important because it loads Binaries/, Custom Formulas/, DLLs,
    # and other files using relative paths.
    ycruncher_dir = ycruncher_path.parent

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ycruncher_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        if output_queue is not None:
            output_queue.put(
                f"[Launcher] Не удалось запустить y-cruncher: {error}\n"
            )
        return None

    if output_queue is not None and proc.stdout is not None:
        threading.Thread(
            target=_reader,
            args=(proc.stdout, output_queue),
            name="y-cruncher-output",
            daemon=True,
        ).start()

    return proc


def is_process_running(proc: Optional[subprocess.Popen]) -> bool:
    return proc is not None and proc.poll() is None


def stop_process(proc: Optional[subprocess.Popen]) -> bool:
    if proc is None:
        return False

    if proc.poll() is not None:
        return True

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                proc.wait(timeout=5)
                return True
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        proc.terminate()
        proc.wait(timeout=3)
        return True
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
            proc.wait(timeout=3)
            return True
        except (OSError, subprocess.SubprocessError):
            return False
