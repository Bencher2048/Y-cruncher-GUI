"""Read-only HWiNFO integration. No SDK binaries or hardware driver bundled."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import math
import json
import traceback
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import threading
import time

import psutil

HEADER = struct.Struct('<IIIqIIIIII')
SENSOR = struct.Struct('<II128s128s')
READING = struct.Struct('<III128s128s16sdddd')
SENSOR_V2_SIZE = SENSOR.size + 128
READING_V2_SIZE = READING.size + 128 + 16
MAGIC = int.from_bytes(b'HWiS', 'little')
MAPPING_NAME = r'Global\HWiNFO_SENS_SM2'
MUTEX_NAME = r'Global\HWiNFO_SM2_MUTEX'
MAX_MAPPING = 16 * 1024 * 1024
BUILD_ID = 'sensors-17'
DEFAULT_POLL_MS = 2000


def poll_seconds(polling_ms, minimum=0.5, maximum=5.0):
    """Read interval that matches HWiNFO's own update rate.

    Shared memory cannot change faster than HWiNFO writes it, so polling at
    1 s when HWiNFO polls at 2 s only doubles the decode work: about 1% of a
    CPU core, permanently, even with the sensor window closed.
    """
    try:
        seconds = float(polling_ms) / 1000.0
    except (TypeError, ValueError):
        seconds = DEFAULT_POLL_MS / 1000.0
    if not math.isfinite(seconds):
        seconds = DEFAULT_POLL_MS / 1000.0
    return max(minimum, min(maximum, seconds))


class SensorUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Reading:
    key: str
    kind: int
    hardware: str
    hardware_original: str
    label: str
    original: str
    unit: str
    value: float
    minimum: float
    maximum: float
    average: float

    @property
    def source(self):
        return '%s / %s' % (self.hardware, self.label)


def _decode(raw):
    raw = raw.split(b'\0', 1)[0]
    # Original HWiNFO labels are ANSI; use the actual Windows code page.
    return raw.decode('mbcs' if sys.platform == 'win32' else 'cp1252', 'replace').strip()


def _decode_utf8(raw):
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace').strip()


def header_layout(blob):
    if len(blob) < HEADER.size:
        raise SensorUnavailable('Неполный заголовок Shared Memory')
    signature, version, revision, stamp, so, ss, sn, ro, rs, rn = HEADER.unpack_from(blob)
    if signature != MAGIC:
        raise SensorUnavailable('Shared Memory выключен или HWiNFO завершён')
    if version not in (1, 2):
        raise SensorUnavailable('Неподдерживаемая версия Shared Memory: %d' % version)
    sensor_min = SENSOR_V2_SIZE if version == 2 else SENSOR.size
    reading_min = READING_V2_SIZE if version == 2 else READING.size
    if ss < sensor_min or rs < reading_min or sn > 4096 or rn > 32768:
        raise SensorUnavailable('Некорректные размеры записей Shared Memory')
    se, re_ = so + ss * sn, ro + rs * rn
    if (so < HEADER.size or ro < HEADER.size or max(se, re_) > MAX_MAPPING or
            (sn and rn and max(so, ro) < min(se, re_))):
        raise SensorUnavailable('Некорректные границы Shared Memory')
    return stamp, so, ss, sn, ro, rs, rn, max(se, re_)


def parse_snapshot(blob):
    stamp, so, ss, sn, ro, rs, rn, required = header_layout(blob)
    if len(blob) < required:
        raise SensorUnavailable('Неполный снимок Shared Memory')
    version = HEADER.unpack_from(blob)[1]
    hardware = []
    for index in range(sn):
        ident, instance, original, user = SENSOR.unpack_from(blob, so + ss * index)
        display = _decode(user)
        if version == 2:
            display = _decode_utf8(blob[so + ss * index + SENSOR.size:so + ss * index + SENSOR_V2_SIZE]) or display
        hardware.append((ident, instance, _decode(original), display))
    rows = []
    for index in range(rn):
        kind, parent, ident, original, user, unit, value, low, high, avg = READING.unpack_from(blob, ro + rs * index)
        if parent >= len(hardware):
            raise SensorUnavailable('Индекс датчика вне списка оборудования')
        if not math.isfinite(value):
            continue
        hid, instance, horig, huser = hardware[parent]
        if not _decode(original):
            continue
        display, units = _decode(user), _decode(unit)
        if version == 2:
            extra = ro + rs * index + READING.size
            display = _decode_utf8(blob[extra:extra + 128]) or display
            units = _decode_utf8(blob[extra + 128:extra + 144]) or units
        reading = Reading('%08x:%d:%08x' % (hid, instance, ident), kind,
                          huser or horig, horig, display or _decode(original),
                          _decode(original), units, value, low, high, avg)
        # WHEA counters use the SDK's generic reading type, without a % unit.
        if kind in (1, 2, 6) or units == '%' or is_whea_count(reading):
            rows.append(reading)
    return stamp, rows


class SharedMemoryReader:
    def __init__(self):
        self.diagnostics = {}
        self.polling_ms = DEFAULT_POLL_MS
        self._last_stamp = None
        self._last_change = None
        if sys.platform != 'win32':
            self.kernel = None
            return
        k = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.OpenFileMappingW.restype = wintypes.HANDLE
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.OpenMutexW.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.ReleaseMutex.argtypes = [wintypes.HANDLE]
        class MemoryInfo(ctypes.Structure):
            _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                        ('AllocationProtect', wintypes.DWORD), ('RegionSize', ctypes.c_size_t),
                        ('State', wintypes.DWORD), ('Protect', wintypes.DWORD), ('Type', wintypes.DWORD)]
        self.MemoryInfo = MemoryInfo
        k.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryInfo), ctypes.c_size_t]
        k.VirtualQuery.restype = ctypes.c_size_t

    def read(self):
        if self.kernel is None:
            raise SensorUnavailable('Датчики HWiNFO доступны только в Windows')
        k = self.kernel
        handle = k.OpenFileMappingW(4, False, MAPPING_NAME)
        if not handle:
            raise SensorUnavailable('Нет Shared Memory. Запустите HWiNFO и включите Shared Memory Support')
        pointer = mutex = None
        acquired = False
        try:
            pointer = k.MapViewOfFile(handle, 4, 0, 0, 0)
            if not pointer:
                raise SensorUnavailable('Нет доступа к Shared Memory HWiNFO')
            memory = self.MemoryInfo()
            if not k.VirtualQuery(pointer, ctypes.byref(memory), ctypes.sizeof(memory)) or memory.RegionSize < HEADER.size:
                raise SensorUnavailable('Не удалось проверить размер отображения HWiNFO')
            mutex = k.OpenMutexW(0x00100001, False, MUTEX_NAME)
            if not mutex:
                raise SensorUnavailable('Нет доступа к mutex HWiNFO. Запустите GUI от администратора')
            result = k.WaitForSingleObject(mutex, 100)
            if result not in (0, 128):
                raise SensorUnavailable('HWiNFO занят обновлением датчиков')
            acquired = True
            head = ctypes.string_at(pointer, HEADER.size)
            fields = HEADER.unpack(head)
            self.diagnostics = dict(zip(('signature', 'version', 'revision', 'poll_time',
                'sensor_offset', 'sensor_size', 'sensor_count', 'reading_offset',
                'reading_size', 'reading_count'), fields))
            required = header_layout(head)[-1]
            if required > memory.RegionSize:
                raise SensorUnavailable('Shared Memory выходит за границы отображения')
            blob = ctypes.string_at(pointer, required)
        finally:
            if acquired:
                k.ReleaseMutex(mutex)
            if mutex:
                k.CloseHandle(mutex)
            if pointer:
                k.UnmapViewOfFile(pointer)
            k.CloseHandle(handle)
        stamp, rows = parse_snapshot(blob)
        polling_ms = struct.unpack_from('<I', blob, 44)[0] if HEADER.unpack_from(blob)[2] >= 1 and min(header_layout(blob)[1], header_layout(blob)[4]) >= 48 else 2000
        if polling_ms:
            self.polling_ms = int(polling_ms)
        freshness = max(15, min(120, polling_ms / 1000 * 3))
        now = time.monotonic()
        if stamp != self._last_stamp:
            self._last_stamp, self._last_change = stamp, now
        if stamp <= 0 or time.time() - stamp > freshness or now - self._last_change > freshness:
            raise SensorUnavailable('Показания HWiNFO устарели; проверьте опрос датчиков или перезапустите HWiNFO')
        return rows


def is_cpu(row):
    return bool(re.search(r'\bcpu\b|\bцп\b|\bryzen\b|\bintel\s+(?:core|xeon|pentium|celeron)|\bamd\s+(?:fx|athlon|epyc|threadripper)', row.hardware_original, re.I)) and not re.search(r'\bgpu\b|radeon|geforce', row.hardware_original, re.I)


def voltage_unit(row):
    return row.unit.lower().replace('м', 'm').replace('в', 'v')


def voltage_role(row):
    if row.kind != 2 or voltage_unit(row) not in ('v', 'mv'):
        return None
    name = (row.original + ' ' + row.label).lower()
    if re.search(r'\bvids?\b', name):
        return 'vid' if is_cpu(row) else None
    if re.search(r'\bv\s*core\b|\bcpu\s*(?:core\s*)?voltage\b', name):
        if not is_cpu(row) and not re.search(r'gpu|geforce|radeon', row.hardware_original, re.I):
            return 'board_vcore'
    return None


def usable_voltage(row):
    value = row.value / 1000 if voltage_unit(row) == 'mv' else row.value
    return 0 < value < 3


def select_voltage(rows, preferred=''):
    board = [row for row in rows if voltage_role(row) == 'board_vcore' and usable_voltage(row)]
    vid = [row for row in rows if voltage_role(row) == 'vid' and usable_voltage(row)]
    # A user selection can resolve multiple motherboard sensors, but can
    # never replace an available board Vcore with a VID.
    candidates = board or vid
    if not candidates:
        return None
    for row in candidates:
        if row.key == preferred:
            return row
    def rank(row):
        label = row.original.strip().lower()
        return (0 if label == 'vcore' else 1 if label in ('cpu vcore', 'cpu core voltage') else 2,
                0 if label in ('core vids', 'cpu vid', 'vid') else 1, row.key)
    return sorted(candidates, key=rank)[0]


def temperature_rank(row):
    name = row.original.lower()
    return (0 if 'tdie' in name and 'tctl' not in name else
            1 if 'package' in name or 'tctl/tdie' in name else
            2 if 'tctl' in name else 3, row.key)


def cpu_rows(rows):
    cpu = [row for row in rows if is_cpu(row)]
    clocks = [r for r in cpu if r.kind == 6 and re.search(r'core|cpu|average|ядр|цп|частот', r.original, re.I)
              and not re.search(r'bus|fabric|memory|uncore|ring|soc|шин|памят', r.original, re.I)]
    clocks = [r for r in clocks if not re.search(
        r'average.*effective|средн.*эффектив', r.original + ' ' + r.label, re.I)]
    temps = sorted([r for r in cpu if r.kind == 1 and not re.search(
        r'tj\s*max|distance|расстояни|\b(?:cpu|цп)\s*(?:gt|uncore|dts2)\b',
        r.original + ' ' + r.label, re.I)], key=temperature_rank)
    return clocks, temps


def cpu_load_rows(rows):
    """Per-core/thread usage from HWiNFO, including localized labels."""
    return [r for r in rows if is_cpu(r) and r.unit == '%' and re.search(
        r'(?:core|ядр[оа])\s*\d+', r.original + ' ' + r.label, re.I)
        and re.search(r'usage|utili[sz]ation|использован|загруз', r.original + ' ' + r.label, re.I)]


def is_whea_count(row):
    """Only hardware-error counters; absence is not a zero reading."""
    hardware = row.hardware_original + ' ' + row.hardware
    label = row.original + ' ' + row.label
    text = hardware + ' ' + label
    return (row.kind not in (1, 2, 6) and row.unit.strip() != '%' and row.value >= 0
            and bool(re.search(r'\bwhea\b|windows hardware errors|windows hardware error architecture|аппаратн.*ошиб.*windows', text, re.I))
            and bool(re.search(r'errors?|count|ошиб|количеств|сч[её]т', label, re.I)))


def whea_rows(rows):
    return [row for row in rows if is_whea_count(row)]


def is_memory_temperature(row):
    if row.kind != 1:
        return False
    hardware = row.hardware_original + ' ' + row.hardware
    label = row.original + ' ' + row.label
    text = hardware + ' ' + label
    # Video-memory and storage temperatures are outside this table's RAM scope.
    if re.search(r'\bgpu\b|radeon|geforce|nvidia|\bvram\b|\bgddr\w*\b|видеопамят|графич|\b(?:ssd|nvme|hdd|optane)\b', text, re.I):
        return False
    # On servers CLTT exposes DIMM TSOD readings in the CPU section under this name.
    if re.search(r'memory.*channel.*rank|памят.*канал.*ранг', label, re.I):
        return True
    if re.search(r'memory.*controller|контроллер.*памят|памят.*контроллер', label, re.I):
        return False
    return bool(re.search(r'\b(?:dimm|[lu]?rdimm|ddr[345]|dram|ram|spd|tsod|memory)\b|оперативн.*памят|памят[ьи]|\bозу\b', text, re.I))


def memory_temperature_rows(rows):
    return [row for row in rows if is_memory_temperature(row)]


def get_cpu_name():
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                return str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).strip()
        except OSError:
            pass
    return 'Название процессора недоступно'


def resolve_hwinfo_path(root, saved=''):
    """Use only the portable hwinfo folder beside the GUI."""
    folder = Path(root) / 'hwinfo'
    candidate = folder / 'HWiNFO64.exe'
    if folder.is_dir():
        for item in folder.iterdir():
            if item.is_file() and item.name.lower() == 'hwinfo64.exe':
                return str(item.resolve())
    return str(candidate.resolve())


def configure_hwinfo(executable):
    """Update documented INI options; preserve comments, encoding and other settings."""
    executable = Path(executable).resolve()
    if not executable.is_file() or executable.name.lower() not in ('hwinfo64.exe', 'hwinfo32.exe', 'hwinfo.exe'):
        raise ValueError('Файл HWiNFO не найден или неверное имя EXE. Проверьте папку hwinfo.')
    ini = executable.with_suffix('.INI')
    if not ini.exists():
        other = executable.with_suffix('.ini')
        if other.exists(): ini = other
    original = ini.read_bytes() if ini.exists() else b''
    encoding = ('utf-16' if original.startswith((b'\xff\xfe', b'\xfe\xff')) else
                'utf-8-sig' if original.startswith(b'\xef\xbb\xbf') else
                'mbcs' if sys.platform == 'win32' else 'cp1252')
    text = original.decode(encoding)
    # Recover malformed one-line INIs with literal escaped line endings.
    if '\n' not in text and r'\n' in text:
        text = text.replace(r'\r\n', '\n').replace(r'\n', '\n')
    options = {'SensorsOnly': '1', 'MinimalizeSensors': '1', 'SensorsSM': '1'}
    lines = text.splitlines()
    inside = False
    found = set()
    output = []
    has_section = False
    for line in lines:
        section = re.match(r'\s*\[([^]]+)\]', line)
        if section:
            if inside:
                output.extend('%s=%s' % (key, value) for key, value in options.items() if key not in found)
            inside = section.group(1).strip().lower() == 'settings'
            if inside: has_section = True
        option = re.match(r'\s*([^=;#]+)\s*=', line) if inside else None
        key = next((k for k in options if option and k.lower() == option.group(1).strip().lower()), None)
        if key:
            if key not in found: output.append('%s=%s' % (key, options[key]))
            found.add(key)
        else:
            output.append(line)
    if not has_section:
        output.extend(['', '[Settings]'])
    if inside or not has_section:
        output.extend('%s=%s' % (key, value) for key, value in options.items() if key not in found)
    new = ('\r\n'.join(output) + '\r\n').encode(encoding)
    if new != original:
        if original:
            backup = ini.with_name(ini.name + '.ycruncher-backup')
            if not backup.exists(): backup.write_bytes(original)
        temporary = ini.with_name(ini.name + '.ycruncher-tmp')
        temporary.write_bytes(new)
        os.replace(str(temporary), str(ini))
    return str(ini)


class HWiNFOManager:
    """Own only a process we launched. Never stop a pre-existing HWiNFO."""
    def __init__(self):
        self.owned = None
        self.path = ''
        self.started_at = None
        self.elevated_handle = None

    def existing(self):
        for process in psutil.process_iter(['name']):
            if (process.info['name'] or '').lower() in ('hwinfo64.exe', 'hwinfo32.exe', 'hwinfo.exe'):
                return True
        return False

    def start(self, path):
        if sys.platform != 'win32':
            raise SensorUnavailable('Запуск HWiNFO доступен только в Windows')
        if self.existing():
            return 'HWiNFO уже запущен; использую его настройки. Включите Shared Memory Support, если данных нет.'
        self.path = str(Path(path).resolve())
        configure_hwinfo(self.path)
        try:
            proc = subprocess.Popen([self.path], cwd=str(Path(self.path).parent))
            self.owned = psutil.Process(proc.pid)
        except OSError as exc:
            if sys.platform != 'win32' or getattr(exc, 'winerror', None) != 740:
                raise
            self._start_elevated()
        self.started_at = time.monotonic()
        return 'HWiNFO запускается в режиме датчиков, свёрнутым в трей…'

    def _start_elevated(self):
        class ExecuteInfo(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG), ('hwnd', wintypes.HWND),
                        ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR),
                        ('lpParameters', wintypes.LPCWSTR), ('lpDirectory', wintypes.LPCWSTR),
                        ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                        ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR),
                        ('hkeyClass', wintypes.HKEY), ('dwHotKey', wintypes.DWORD),
                        ('hIcon', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
        shell.ShellExecuteExW.restype = wintypes.BOOL
        info = ExecuteInfo()
        info.cbSize, info.fMask = ctypes.sizeof(info), 0x40 | 0x100
        info.lpVerb, info.lpFile = 'runas', self.path
        info.lpDirectory, info.nShow = str(Path(self.path).parent), 7
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.elevated_handle = info.hProcess
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetProcessId.argtypes = [wintypes.HANDLE]
        kernel.GetProcessId.restype = wintypes.DWORD
        self.owned = psutil.Process(kernel.GetProcessId(info.hProcess))

    def running_owned(self):
        try:
            return self.owned is not None and self.owned.is_running() and self.owned.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return self.owned is not None and self.owned.is_running()

    def restart_due(self, now=None):
        return (self.started_at is not None and
                (time.monotonic() if now is None else now) - self.started_at >= 12 * 3600
                and self.running_owned())

    def stop_owned(self):
        process = self.owned
        if not self.running_owned():
            self._release_handle()
            self.owned = None
            return
        # WM_CLOSE lets HWiNFO finish its INI write before we configure a restart.
        if sys.platform == 'win32':
            user = ctypes.WinDLL('user32', use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            def close(hwnd, param):
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == process.pid:
                    user.PostMessageW(hwnd, 0x10, 0, 0)
                return True
            user.EnumWindows(callback_type(close), 0)
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            try:
                process.terminate()
            except psutil.AccessDenied:
                if not self.elevated_handle:
                    raise
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel.TerminateProcess.restype = wintypes.BOOL
                if not kernel.TerminateProcess(self.elevated_handle, 0):
                    raise ctypes.WinError(ctypes.get_last_error())
            process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        self.owned = None
        self.started_at = None
        self._release_handle()

    def _release_handle(self):
        if self.elevated_handle and sys.platform == 'win32':
            kernel = ctypes.WinDLL('kernel32')
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.elevated_handle)
        self.elevated_handle = None

    def restart(self, path):
        if self.existing() and not self.running_owned():
            raise RuntimeError('Этот экземпляр HWiNFO запущен отдельно. Закройте его вручную перед запуском из GUI.')
        self.stop_owned()
        return self.start(path)


class SensorService:
    """All process management and memory polling happen off the Tk thread."""
    def __init__(self, path='', auto_restart=True, diagnostic_path=None):
        self.diagnostic_path = Path(diagnostic_path) if diagnostic_path else None
        self._diagnostic_state = None
        self.path = path
        # HWiNFO shared-memory renewal is always enabled; there is no UI switch.
        self.auto_restart = True
        self._restart_required = False
        self._restart_retry_at = 0.0
        self.commands = queue.Queue()
        self.events = queue.Queue(maxsize=8)
        self.manager = HWiNFOManager()
        self.reader = SharedMemoryReader()
        self.quit = threading.Event()
        self.thread = threading.Thread(target=self._run, name='hwinfo-monitor', daemon=True)
        self.thread.start()

    def command(self, kind, value=None):
        self.commands.put((kind, value))

    def emit(self, kind, value):
        try: self.events.put_nowait((kind, value))
        except queue.Full:
            try: self.events.get_nowait()
            except queue.Empty: pass
            self.events.put_nowait((kind, value))

    def diagnostic(self, status, error='', rows=None):
        if self.diagnostic_path is None: return
        header = dict(self.reader.diagnostics)
        header.pop('poll_time', None)
        state = (status, error, repr(header), len(rows) if rows is not None else None)
        if state == self._diagnostic_state: return
        try:
            data = {'build': BUILD_ID, 'time': time.time(), 'status': status, 'error': error,
                'python': sys.version, 'executable': sys.executable, 'module': __file__,
                'hwinfo_path': self.path, 'shared_memory': dict(self.reader.diagnostics)}
            if rows is not None:
                data['parsed_count'] = len(rows)
                data['readings'] = [{'key': r.key, 'kind': r.kind, 'hardware': r.hardware_original,
                    'original': r.original, 'label': r.label, 'unit': r.unit, 'value': r.value} for r in rows]
            if error: data['traceback'] = traceback.format_exc()
            temporary = self.diagnostic_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            os.replace(str(temporary), str(self.diagnostic_path))
            self._diagnostic_state = state
        except (OSError, ValueError):
            pass

    def _run(self):
        launch_error = None
        if self.path:
            self.commands.put(('start', self.path))
        try:
            while not self.quit.is_set():
                try:
                    kind, value = self.commands.get_nowait()
                except queue.Empty:
                    kind = None
                if kind == 'quit': break
                try:
                    if kind in ('start', 'restart'):
                        launch_error = None
                        self.path = value
                        self.emit('notice', self.manager.restart(value) if kind == 'restart' else self.manager.start(value))
                        self._restart_required = False
                        self._restart_retry_at = 0.0
                        self.reader = SharedMemoryReader()
                    rows = self.reader.read()
                    launch_error = None
                    self.diagnostic('connected', rows=rows)
                    self.emit('snapshot', rows)
                except Exception as exc:
                    if kind in ('start', 'restart'):
                        launch_error = str(exc)
                    self.diagnostic('unavailable', error=launch_error or str(exc))
                    self.emit('unavailable', '%s · %s' % (BUILD_ID, launch_error or str(exc)))
                    if self.manager.restart_due():
                        self._restart_required = True
                    if self._restart_required and time.monotonic() >= self._restart_retry_at:
                        try:
                            self.emit('notice', self.manager.restart(self.path))
                            launch_error = None
                            self._restart_required = False
                            self._restart_retry_at = 0.0
                            self.reader = SharedMemoryReader()
                        except Exception as restart_error:
                            self._restart_retry_at = time.monotonic() + 60
                            self.emit('unavailable', 'Ошибка перезапуска HWiNFO; повтор через минуту: %s' % restart_error)
                self.quit.wait(poll_seconds(getattr(self.reader, 'polling_ms', DEFAULT_POLL_MS)))
        finally:
            try: self.manager.stop_owned()
            except Exception as exc: self.emit('notice', 'HWiNFO остался запущен: %s' % exc)

    def close(self):
        self.quit.set()
