"""GUI-owned extrema, independent of HWiNFO sessions and its min/max fields."""
import json
import math
import os
from pathlib import Path
from sensors import cpu_rows, voltage_role, usable_voltage, cpu_load_rows, memory_temperature_rows, whea_rows


class SensorHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.records = {}
        self.error = ''
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('sensors'), dict):
                raise ValueError('Неверный формат истории датчиков')
            for key, entry in data['sensors'].items():
                if (isinstance(key, str) and isinstance(entry, dict) and
                        isinstance(entry.get('identity'), list) and len(entry['identity']) == 4 and
                        isinstance(entry['identity'][0], str) and isinstance(entry['identity'][1], str) and
                        isinstance(entry['identity'][2], int) and isinstance(entry['identity'][3], str) and
                        all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 1e9
                            for v in (entry.get('min'), entry.get('max'))) and entry['min'] <= entry['max']):
                    self.records[key] = entry
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.error = 'Не удалось загрузить историю датчиков: %s' % exc
            # Preserve a malformed file before a subsequent update replaces it.
            if self.path.is_file():
                try:
                    backup = self.path.with_name(self.path.name + '.invalid-backup')
                    if not backup.exists(): backup.write_bytes(self.path.read_bytes())
                except OSError:
                    pass

    @staticmethod
    def identity(row):
        return [row.hardware_original, row.original, row.kind, row.unit.lower()]

    def extrema(self, row):
        entry = self.records.get(row.key)
        if entry is None or entry['identity'] != self.identity(row):
            return math.nan, math.nan
        return entry['min'], entry['max']

    def update(self, rows):
        clocks, temps = cpu_rows(rows)
        voltage = [r for r in rows if voltage_role(r) and usable_voltage(r)]
        changed = False
        for row in temps + clocks + cpu_load_rows(rows) + voltage + memory_temperature_rows(rows) + whea_rows(rows):
            if not math.isfinite(row.value) or abs(row.value) > 1e9:
                continue
            identity = self.identity(row)
            entry = self.records.get(row.key)
            if entry is None or entry['identity'] != identity:
                self.records[row.key] = {'identity': identity, 'min': row.value, 'max': row.value}
                changed = True
            else:
                low, high = min(entry['min'], row.value), max(entry['max'], row.value)
                if (low, high) != (entry['min'], entry['max']):
                    entry['min'], entry['max'] = low, high
                    changed = True
        if changed or self.error:
            self.save()

    def _write(self, records):
        temporary = self.path.with_name(self.path.name + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as handle:
                json.dump({'version': 1, 'sensors': records}, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self.path))
        finally:
            if temporary.exists(): temporary.unlink()

    def save(self):
        try:
            self._write(self.records)
            self.error = ''
        except OSError as exc:
            self.error = 'Не удалось сохранить sensor_history.json: %s' % exc

    def reset(self):
        # Do not report success or clear RAM if writing the empty JSON failed.
        self._write({})
        self.records = {}
        self.error = ''
