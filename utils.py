from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import sys
import threading
import time
from typing import Iterable, List, Optional, Tuple

try:
    import psutil
except ImportError as exc:
    raise ImportError(
        "Для работы выбора физических ядер требуется psutil. "
        "Установите его командой: python -m pip install psutil"
    ) from exc


def _project_root() -> Path:
    """Find the application directory for both .py and packaged EXE."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path(__file__).resolve().parents[1])
    candidates.append(Path.cwd())
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _candidate_ycruncher_paths() -> List[Path]:
    root = _project_root()
    candidates = [
        root / "y-cruncher" / "y-cruncher.exe",
        root / "y-cruncher.exe",
        Path.cwd() / "y-cruncher" / "y-cruncher.exe",
        Path.cwd() / "y-cruncher.exe",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        m = Path(meipass)
        candidates += [m / "y-cruncher" / "y-cruncher.exe", m / "y-cruncher.exe"]
    result, seen = [], set()
    for p in candidates:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            result.append(p)
    return result


def find_ycruncher() -> Optional[Path]:
    for p in _candidate_ycruncher_paths():
        try:
            if p.is_file():
                return p
        except OSError:
            pass
    return None


def file_exists() -> bool:
    return find_ycruncher() is not None


def get_working_cfg_path() -> Path:
    return _project_root() / "working.cfg"


def get_default_cfg_path() -> Path:
    return _project_root() / "default.cfg"


def get_cruncher_cfg_path() -> Path:
    return _project_root() / "cruncher.cfg"


def _replace_field(text: str, field: str, value: str) -> str:
    pattern = re.compile(r"(?m)^(\s*" + re.escape(field) + r"\s*:\s*)([^\r\n]+)(\s*)$")
    text, count = pattern.subn(lambda m: m.group(1) + value + m.group(3), text, count=1)
    if count != 1:
        raise ValueError("В cfg не найден параметр: " + field)
    return text


def _format_cfg_tests(tests: Iterable[str]) -> str:
    return "[" + " ".join('"%s"' % t for t in tests) + "]"


def get_system_memory_for_test() -> Tuple[int, int, int]:
    """Return (test_memory_bytes, total_memory_bytes, available_memory_bytes).

    y-cruncher expects TotalMemory in bytes. We use the currently available
    physical RAM and leave 10% free (but never less than 1 GiB) for Windows
    and the GUI. The result is rounded down to 64 MiB so the cfg stays tidy.
    """
    vm = psutil.virtual_memory()
    total = int(vm.total)
    available = int(vm.available)

    reserve = max(1024 ** 3, int(total * 0.10))
    usable = max(256 * 1024 ** 2, available - reserve)

    block = 64 * 1024 ** 2
    usable = (usable // block) * block

    # Never request more than 90% of physical RAM.
    usable = min(usable, (total * 90 // 100 // block) * block)
    usable = max(256 * 1024 ** 2, usable)
    return usable, total, available


def get_logical_cpu_count() -> int:
    """Detect the number of logical processors available to the system."""
    return int(psutil.cpu_count(logical=True) or os.cpu_count() or 1)


def update_working_cfg(selected_tests: Iterable[str], seconds_total: int,
                       seconds_per_test: int, stop_on_error: bool) -> Path:
    default = get_default_cfg_path()
    cruncher = get_cruncher_cfg_path()
    working = get_working_cfg_path()
    source = default if default.is_file() else cruncher
    if not source.is_file():
        raise FileNotFoundError("Не найден default.cfg/cruncher.cfg")

    text = source.read_text(encoding="utf-8-sig", errors="replace")

    # Detect CPU topology at every test start instead of relying on the
    # hard-coded [0 1 2 3] that was present in the template cfg.
    logical_count = get_logical_cpu_count()
    logical_cpus = "[" + " ".join(str(i) for i in range(logical_count)) + "]"
    text = _replace_field(text, "LogicalCores", logical_cpus)

    # Automatically size the y-cruncher memory allocation from the current
    # physical RAM state and write the result directly to working.cfg.
    memory_bytes, total_memory, available_memory = get_system_memory_for_test()
    text = _replace_field(text, "TotalMemory", str(memory_bytes))

    text = _replace_field(text, "SecondsPerTest", str(int(seconds_per_test)))
    text = _replace_field(text, "SecondsTotal", str(int(seconds_total)))
    text = _replace_field(text, "StopOnError", "true" if stop_on_error else "false")
    text = _replace_field(text, "Tests", _format_cfg_tests(selected_tests))
    working.write_text(text, encoding="utf-8")

    return working


def reset_working_cfg() -> Path:
    src = get_default_cfg_path()
    dst = get_working_cfg_path()
    if not src.is_file():
        raise FileNotFoundError("default.cfg не найден")
    dst.write_text(src.read_text(encoding="utf-8-sig", errors="replace"), encoding="utf-8")
    return dst


# ---------------- Windows CPU topology / affinity -------------------------
# Physical-core topology is read from Windows, while PROCESS affinity is
# deliberately applied through psutil.  This is the same mechanism used by
# the user's standalone CPU Core Affinity utility and is more reliable here
# than duplicating process-affinity handling with raw Win32 calls.
if sys.platform == "win32":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    RelationProcessorCore = 0

    class GROUP_AFFINITY(ctypes.Structure):
        _fields_ = [
            ("Mask", ctypes.c_ulonglong),
            ("Group", wintypes.WORD),
            ("Reserved", wintypes.WORD * 3),
        ]

    class PROCESSOR_RELATIONSHIP_PREFIX(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.c_ubyte),
            ("EfficiencyClass", ctypes.c_ubyte),
            ("Reserved", ctypes.c_ubyte * 20),
            ("GroupCount", wintypes.WORD),
        ]


class PhysicalCore:
    def __init__(self, index: int, logical_processors: Tuple[int, ...],
                 groups: Tuple[int, ...], efficiency_class: int, core_type: str):
        self.index = index
        self.logical_processors = logical_processors
        self.groups = groups
        self.efficiency_class = efficiency_class
        self.core_type = core_type

    def label(self) -> str:
        logical = ", ".join(str(cpu + 1) for cpu in self.logical_processors)
        return "Ядро %d (поток %s)" % (self.index + 1, logical)


def _get_raw_core_information() -> bytes:
    if sys.platform != "win32":
        return b""
    f = kernel32.GetLogicalProcessorInformationEx
    f.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    f.restype = wintypes.BOOL
    size = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    f(RelationProcessorCore, None, ctypes.byref(size))
    err = ctypes.get_last_error()
    if size.value == 0 and err not in (0, 122):
        raise ctypes.WinError(err)
    buf = ctypes.create_string_buffer(size.value)
    if not f(RelationProcessorCore, ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buf.raw[:size.value]


def _detect_core_type(eff: int, classes: List[int]) -> str:
    unique = sorted(set(classes))
    if len(unique) <= 1:
        return "Core"
    return "P-Core" if eff == max(unique) else "E-Core"


def get_physical_cores() -> List[PhysicalCore]:
    if sys.platform != "win32":
        return []
    data = _get_raw_core_information()
    raw, offset = [], 0
    header = 8
    prefix_size = ctypes.sizeof(PROCESSOR_RELATIONSHIP_PREFIX)
    ga_size = ctypes.sizeof(GROUP_AFFINITY)
    while offset < len(data):
        if len(data) - offset < header:
            raise RuntimeError("Некорректная информация о топологии CPU.")
        relation = wintypes.DWORD.from_buffer_copy(data, offset).value
        record_size = wintypes.DWORD.from_buffer_copy(data, offset + 4).value
        if record_size < header or offset + record_size > len(data):
            raise RuntimeError("Некорректный размер записи топологии CPU.")
        if relation == RelationProcessorCore:
            base = offset + header
            prefix = PROCESSOR_RELATIONSHIP_PREFIX.from_buffer_copy(data, base)
            logical, groups = [], []
            masks = base + prefix_size
            for i in range(prefix.GroupCount):
                ga = GROUP_AFFINITY.from_buffer_copy(data, masks + i * ga_size)
                groups.append(int(ga.Group))
                mask = int(ga.Mask)
                bit = 0
                while mask:
                    if mask & 1:
                        logical.append(int(ga.Group) * 64 + bit)
                    mask >>= 1
                    bit += 1
            raw.append((tuple(sorted(logical)), tuple(groups), int(prefix.EfficiencyClass)))
        offset += record_size
    classes = [x[2] for x in raw]
    return [PhysicalCore(i, x[0], x[1], x[2], _detect_core_type(x[2], classes))
            for i, x in enumerate(raw)]


def _set_affinity(pid: int, cpus: Iterable[int]) -> List[int]:
    """Set and verify affinity using psutil, exactly as in CpuCoreAfinity.py."""
    cpus = sorted(set(int(c) for c in cpus))
    if not cpus:
        raise ValueError("Не выбраны logical CPU.")
    if sys.platform != "win32":
        return cpus

    proc = psutil.Process(int(pid))
    proc.cpu_affinity(cpus)
    actual = sorted(set(proc.cpu_affinity()))
    expected = sorted(set(cpus))
    if actual != expected:
        raise RuntimeError(
            "Windows/psutil вернул другую affinity: ожидалось %s, получено %s"
            % (expected, actual)
        )
    return actual


def _apply_affinity_tree(proc, cpus, output_queue=None):
    """Apply psutil affinity to the running y-cruncher process and children.

    Popen objects do not have psutil's ``is_running()`` method.  The previous
    version accidentally called that method on the Popen object, which caused
    the repeated ``'Popen' object has no attribute 'is_running'`` messages.
    Here every target is a psutil.Process, matching the proven standalone
    CpuCoreAfinity implementation.
    """
    try:
        root = psutil.Process(proc.pid)
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return

    targets = [root]
    try:
        targets.extend(root.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        pass

    expected = sorted(set(int(c) for c in cpus))
    seen = set()
    for target in targets:
        try:
            if target.pid in seen:
                continue
            seen.add(target.pid)
            if not target.is_running():
                continue

            # Do not rewrite affinity every half-second if it is already
            # correct.  This also keeps the GUI console clean.
            current = sorted(set(target.cpu_affinity()))
            if current != expected:
                actual = _set_affinity(target.pid, expected)
                _emit(output_queue,
                      "[Launcher] Affinity PID %d -> CPUs %s (verified)\n"
                      % (target.pid, actual))
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied as exc:
            _emit(output_queue, "[Launcher] Affinity PID %d: доступ запрещён: %s\n"
                  % (target.pid, exc))
        except Exception as exc:
            _emit(output_queue, "[Launcher] Affinity PID %d: %s\n"
                  % (target.pid, exc))


def _affinity_after_start(proc: subprocess.Popen, cpus: List[int], output_queue=None):
    """Wait until y-cruncher has actually started, then apply affinity.

    We intentionally do NOT set affinity immediately after Popen().  The
    standalone CPU Core Affinity utility supplied by the user works on an
    already running process, so the GUI follows the same order here.
    """
    # Give y-cruncher time to initialize its stress-test engine and begin the
    # actual test.  This is deliberately after process creation.
    deadline = time.time() + 1.5
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.20)

    if proc.poll() is not None:
        return

    _emit(output_queue, "[Launcher] Тест уже запущен -> назначаю выбранные ядра...\n")
    _apply_affinity_tree(proc, cpus, output_queue)

    # Keep the same affinity while the test is running.  This also catches a
    # possible y-cruncher child process created after the first application.
    while proc.poll() is None:
        time.sleep(0.50)
        if proc.poll() is not None:
            break
        _apply_affinity_tree(proc, cpus, output_queue)


# ---------------- Direct config launch -----------------------------------
# y-cruncher configuration files explicitly support direct launching with:
#     y-cruncher.exe config filename.cfg
# This is preferable on Windows 7 to trying to automate the interactive
# console.  The configuration itself contains the complete StressTest setup.

def _emit(q, text):
    if q is not None and text:
        q.put(("append", text))


def _reader_thread(proc, output_queue):
    """Forward ordinary stdout/stderr when y-cruncher provides it."""
    try:
        while proc.poll() is None:
            chunk = proc.stdout.readline()
            if not chunk:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", "replace")
            _emit(output_queue, chunk)
    except Exception as exc:
        _emit(output_queue, "\n[Launcher] Ошибка чтения y-cruncher: %s\n" % exc)


def set_process_affinity(pid: int, cpus: Iterable[int], output_queue=None) -> bool:
    """Apply affinity to an already-running process and its children.

    This is intentionally a public wrapper around the same psutil-based
    mechanism used by the standalone CPU Core Affinity utility.
    """
    cpus = sorted(set(int(c) for c in cpus))
    if not cpus:
        raise ValueError("Не выбраны logical CPU.")
    proc = psutil.Process(int(pid))
    _apply_affinity_tree(proc, cpus, output_queue)
    # Verify the main process exactly like CpuCoreAfinity.py does.
    actual = sorted(set(proc.cpu_affinity()))
    if actual != cpus:
        raise RuntimeError(
            "Affinity не совпала: ожидалось %s, получено %s" % (cpus, actual)
        )
    return True


def start_test(selected_tests: Iterable[str], output_queue=None,
               affinity_cpus: Optional[Iterable[int]] = None,
               stop_event=None) -> Optional[subprocess.Popen]:
    ycruncher = find_ycruncher()
    if ycruncher is None:
        _emit(output_queue, "[Launcher] y-cruncher.exe не найден.\n")
        return None
    cfg = get_working_cfg_path()
    if not cfg.is_file():
        _emit(output_queue, "[Launcher] working.cfg не найден.\n")
        return None
    tests = list(dict.fromkeys(selected_tests))
    if not tests:
        return None
    if stop_event is None:
        stop_event = threading.Event()

    # IMPORTANT:
    # Do not use the interactive menu here.  y-cruncher documents the direct
    # configuration form, and this also avoids the Windows 7 console/pipe
    # incompatibility that caused the previous versions to stall at "Option:".
    command = [str(ycruncher), "config", str(cfg)]
    _emit(output_queue, "[Launcher] Запуск через CFG: %s\n" % cfg)
    _emit(output_queue, "[Launcher] Команда: y-cruncher.exe config working.cfg\n")

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        proc = subprocess.Popen(
            command,
            cwd=str(ycruncher.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
            startupinfo=startupinfo,
            bufsize=1,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _emit(output_queue, "[Launcher] Не удалось запустить y-cruncher: %s\n" % exc)
        return None

    _emit(output_queue, "[Launcher] y-cruncher запущен. CFG должен начать тест автоматически.\n")
    threading.Thread(target=_reader_thread, args=(proc, output_queue),
                     name="ycruncher-output-reader", daemon=True).start()

    if affinity_cpus is not None:
        cpus = sorted(set(int(c) for c in affinity_cpus))
        if not cpus:
            _emit(output_queue, "[Launcher] Не выбраны logical CPU для affinity.\n")
        else:
            # IMPORTANT: affinity is assigned AFTER the test has started,
            # matching the behavior of the user's standalone utility.
            threading.Thread(
                target=_affinity_after_start,
                args=(proc, cpus, output_queue),
                name="ycruncher-affinity-after-start",
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
    try:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return proc.poll() is not None
        proc.terminate()
        proc.wait(timeout=3)
        return True
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
            return True
        except Exception:
            return False
