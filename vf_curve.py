"""V/F curve plans; one iteration is a full pass over the selected tests."""
import json
import re
import struct
from pathlib import Path

MODES = {"fast": (60, ("VT3",)), "full": (90, ("BKT", "SFTv4", "VT3"))}
ITERATION = re.compile(r"^\s*Iteration\s*:\s*(\d+)\b", re.I)
FAILURE = re.compile(r"\b(?:failed|failure|fatal|exception)\b|^\s*(?:error\s*[:!]|ERROR\b)|ошибка", re.I)


def parse_l3_masks(data):
    """Parse x64 Windows CACHE_RELATIONSHIP, including Win7 GroupMask."""
    masks, offset = [], 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("Повреждённая запись топологии L3")
        relation, size = struct.unpack_from("<II", data, offset)
        if size < 8 or offset + size > len(data):
            raise ValueError("Повреждённый размер записи L3")
        if relation == 2 and data[offset + 8] == 3:
            if size < 56:
                raise ValueError("Неполная запись L3; требуется Windows x64")
            count = struct.unpack_from("<H", data, offset + 38)[0] or 1
            if size < 40 + 16 * count:
                raise ValueError("Неполная маска L3")
            cpus = set()
            for i in range(count):
                mask, group = struct.unpack_from("<QH", data, offset + 40 + 16 * i)
                cpus.update(group * 64 + bit for bit in range(64) if mask & (1 << bit))
            masks.append(cpus)
        offset += size
    return masks


def validate_ccds(cores, ccds):
    known = {c.index for c in cores}
    flattened = [i for ccd in ccds for i in ccd]
    if (not ccds or any(not ccd for ccd in ccds) or
            len(flattened) != len(set(flattened)) or set(flattened) != known):
        raise ValueError("CCD должны включать каждое физическое ядро ровно один раз")
    expected = 2 if len(cores) in (12, 16) else 1
    if len(ccds) != expected or any(len(ccd) < 2 or len(ccd) % 2 for ccd in ccds):
        raise ValueError("Нужны %d CCD с чётным числом ядер" % expected)
    return [sorted(ccd) for ccd in ccds]


def detect_ccds(cores, project_root):
    override = Path(project_root) / "ccd_layout.json"
    if override.is_file():
        return validate_ccds(cores, json.loads(override.read_text(encoding="utf-8-sig")))
    if len(cores) not in (12, 16):
        return validate_ccds(cores, [[c.index for c in cores]])
    from utils import _get_raw_core_information
    groups = []
    for mask in parse_l3_masks(_get_raw_core_information(2)):
        group = [c.index for c in cores if set(c.logical_processors).issubset(mask)]
        if group and group not in groups:
            groups.append(group)
    # L3 identifies a CCD on newer Ryzen. Older Ryzen exposes CCX caches;
    # do not silently assume that neighbouring CCX records belong to a CCD.
    try:
        return validate_ccds(cores, sorted(groups, key=lambda g: min(g)))
    except ValueError:
        raise ValueError("Не удалось однозначно определить два CCD по L3. "
                         "Создайте ccd_layout.json с двумя списками индексов "
                         "физических ядер (с нуля), например [[0, 1], [2, 3]].")


def build_plan(cores, ccds, mode):
    seconds, tests = MODES[mode]
    ccds = validate_ccds(cores, ccds)
    by_index = {c.index: c for c in cores}
    stages = []
    def add(label, indices, iterations):
        stages.append(dict(label=label, core_indices=list(indices),
                           cpus=sorted({cpu for i in indices for cpu in by_index[i].logical_processors}),
                           iterations=iterations, seconds=seconds, tests=tests,
                           duration=seconds * len(tests) * iterations))
    for core in sorted(cores, key=lambda c: c.index):
        add("Ядро %d" % (core.index + 1), [core.index], 1)
    for number, ccd in enumerate(ccds, 1):
        half = len(ccd) // 2
        add("CCD %d: первая половина" % number, ccd[:half], 2)
        add("CCD %d: вторая половина" % number, ccd[half:], 2)
    if len(cores) in (12, 16):
        for number, ccd in enumerate(ccds, 1):
            add("CCD %d: все ядра" % number, ccd, 5)
    add("Все ядра", sorted(by_index), 5)
    return stages


def format_stage_status(stage, completed_iterations, stage_index, stage_count):
    """Describe the V/F target in physical-core terms for the live status line."""
    tests = ' + '.join(stage['tests'])
    return '%s · %s · итерация %d/%d · этап %d/%d' % (
        stage['label'], tests, max(0, min(stage['iterations'], completed_iterations)),
        stage['iterations'], stage_index + 1, stage_count)


def format_cpu_scope(mode, selected_indices=()):
    if mode == 'all':
        return 'Нагрузка: все ядра'
    if mode == 'sequential':
        return 'Нагрузка: последовательная проверка ядер'
    cores = ', '.join(str(index + 1) for index in selected_indices)
    return 'Нагрузка: ядра %s' % (cores or 'не выбраны')


class IterationTracker:
    def __init__(self, target):
        self.target = target
        self.first = None
        self.last = None

    def feed(self, line):
        match = ITERATION.search(line)
        if not match:
            return False
        value = int(match.group(1))
        if self.first is None:
            self.first = value
        if self.last is not None and value < self.last:
            raise ValueError("Счётчик итераций y-cruncher сброшен")
        self.last = value
        return value - self.first >= self.target
