import logging
import threading


logger = logging.getLogger("runner")


class ResourceMonitor:
    """실행 컨테이너의 Docker stats를 한 번만 소비해 peak를 수집한다."""

    def __init__(self, container) -> None:
        self.container = container
        self.memory_peak_bytes: int | None = None
        self.pids_peak: int | None = None
        self.error: Exception | None = None
        self._thread: threading.Thread | None = None
        self._stream = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._collect,
            name="runner-resource-monitor",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: float = 1.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def stop(self, timeout: float = 1.5) -> bool:
        self._stop_event.set()
        stream = self._stream
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                logger.warning(
                    "event=resource_monitor_close_error error=%s",
                    exc,
                )
        self.join(timeout=timeout)
        stopped = self._thread is None or not self._thread.is_alive()
        if not stopped:
            logger.warning("event=resource_monitor_stop_timeout")
        return stopped

    def sample_once(self) -> None:
        """짧은 실행을 위해 시작 직후의 stats를 보조로 수집한다."""

        try:
            stats = self.container.stats(stream=False, one_shot=True)
            if isinstance(stats, dict):
                self._record(stats)
        except Exception as exc:
            logger.warning(
                "event=resource_monitor_initial_sample_error error=%s",
                exc,
            )

    def _record(self, stats: dict) -> None:
        memory_stats = stats.get("memory_stats") or {}
        usage = memory_stats.get("usage")
        if isinstance(usage, int):
            if (
                self.memory_peak_bytes is None
                or usage > self.memory_peak_bytes
            ):
                self.memory_peak_bytes = usage

        pids_stats = stats.get("pids_stats") or {}
        current = pids_stats.get("current")
        if isinstance(current, int):
            if self.pids_peak is None or current > self.pids_peak:
                self.pids_peak = current

    def _collect(self) -> None:
        stream = None
        try:
            # 초기 sample도 monitor thread에서 수집해 timeout/output 감시를
            # Docker stats 응답 시간과 분리한다.
            self.sample_once()
            if self._stop_event.is_set():
                return

            stream = self.container.stats(stream=True, decode=True)
            self._stream = stream
            for stats in stream:
                if self._stop_event.is_set():
                    break
                self._record(stats)
        except Exception as exc:
            self.error = exc
            logger.warning(
                "event=resource_monitor_error error=%s",
                exc,
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.warning(
                        "event=resource_monitor_close_error error=%s",
                        exc,
                    )
            self._stream = None
