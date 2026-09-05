import threading


class TestTimer:
    def __init__(self, minutes, on_tick=None, on_finish=None, on_stop=None):
        try:
            minutes = float(minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("Длительность теста должна быть числом.") from exc

        if minutes <= 0:
            raise ValueError("Длительность теста должна быть больше 0 минут.")

        self.total_seconds = max(1, round(minutes * 60))
        self.remaining = self.total_seconds
        self.on_tick = on_tick
        self.on_finish = on_finish
        self.on_stop = on_stop
        self.running = False
        self.thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.running:
                return False
            self.running = True
            self._stop_event.clear()
            self.thread = threading.Thread(
                target=self._run,
                name="y-cruncher-test-timer",
                daemon=True,
            )
            self.thread.start()

        self._notify_tick()
        return True

    def stop(self):
        with self._lock:
            if not self.running:
                return False
            self.running = False
            self._stop_event.set()

        if self.on_stop:
            self.on_stop()
        return True

    def _run(self):
        while not self._stop_event.wait(1.0):
            with self._lock:
                if not self.running:
                    return
                self.remaining = max(0, self.remaining - 1)
                finished = self.remaining == 0

            self._notify_tick()

            if finished:
                with self._lock:
                    self.running = False
                if self.on_finish:
                    self.on_finish()
                return

    def _notify_tick(self):
        if not self.on_tick:
            return
        with self._lock:
            remaining = self.remaining
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.on_tick(hours, minutes, seconds)

    def is_running(self):
        with self._lock:
            return self.running

    def get_remaining(self):
        with self._lock:
            return self.remaining

    def get_progress(self):
        with self._lock:
            if self.total_seconds <= 0:
                return 0.0
            return (self.total_seconds - self.remaining) / self.total_seconds
