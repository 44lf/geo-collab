"""Command entry point for the standalone Collector Consumer process.

Container command:

    python -m server.app.modules.collector.consumer_cli run

Readiness command:

    python -m server.app.modules.collector.consumer_cli check
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TextIO, cast

from server.app.modules.collector.consumer_runtime import (
    ConsumerConfig,
    ConsumerProcessor,
    ConsumerRuntime,
    EventSink,
    SqlAlchemyConsumerQueue,
)

ConsumerMode = str
RuntimeFactory = Callable[[ConsumerConfig, EventSink], ConsumerRuntime]


class ConsumerStartupError(RuntimeError):
    """The standalone Consumer cannot be wired to its production dependencies."""


class JsonEventSink:
    """One compact JSON object per line, suitable for container stdout capture."""

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def __call__(self, event: Mapping[str, object]) -> None:
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            print(encoded, file=self._stream, flush=True)


def load_consumer_settings(
    environ: Mapping[str, str],
    *,
    hostname: str | None = None,
    pid: int | None = None,
) -> ConsumerConfig:
    """Pure environment-to-config translation used by Docker and unit tests."""

    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    resolved_pid = pid if pid is not None else os.getpid()
    default_worker_id = f"{resolved_hostname}-{resolved_pid}"
    worker_id = environ.get("GEO_COLLECTOR_CONSUMER_WORKER_ID", default_worker_id)
    batch_size = _read_int(
        environ,
        "GEO_COLLECTOR_CONSUMER_BATCH_SIZE",
        default=10,
    )
    poll_seconds = _read_float(
        environ,
        "GEO_COLLECTOR_CONSUMER_POLL_SECONDS",
        default=5.0,
    )
    lease_seconds = _read_float(
        environ,
        "GEO_COLLECTOR_CONSUMER_LEASE_SECONDS",
        default=300.0,
    )
    try:
        return ConsumerConfig(
            worker_id=worker_id,
            batch_size=batch_size,
            poll_interval=timedelta(seconds=poll_seconds),
            lease_duration=timedelta(seconds=lease_seconds),
        )
    except ValueError as exc:
        if "batch_size" in str(exc):
            raise ValueError("GEO_COLLECTOR_CONSUMER_BATCH_SIZE must be between 1 and 100") from exc
        if "poll_interval" in str(exc):
            raise ValueError(
                "GEO_COLLECTOR_CONSUMER_POLL_SECONDS must be between 0 and 3600"
            ) from exc
        if "lease_duration" in str(exc):
            raise ValueError(
                "GEO_COLLECTOR_CONSUMER_LEASE_SECONDS must be between 1 and 3600"
            ) from exc
        raise


def build_consumer_command(mode: ConsumerMode = "run") -> tuple[str, ...]:
    """Return the copy/paste-safe command used by Compose or a Docker healthcheck."""

    if mode not in {"run", "once", "check"}:
        raise ValueError("mode must be one of: run, once, check")
    return (
        "python",
        "-m",
        "server.app.modules.collector.consumer_cli",
        mode,
    )


def build_shutdown_handler(
    stop_event: threading.Event,
    event_sink: EventSink,
    *,
    worker_id: str,
) -> Callable[[int, object], None]:
    """Build a signal callback without installing global handlers during tests."""

    def request_shutdown(signum: int, frame: object) -> None:
        del frame
        if not stop_event.is_set():
            event_sink(
                {
                    "occurred_at": datetime.now(UTC)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                    "level": "info",
                    "event": "shutdown_requested",
                    "stage": "consumer",
                    "component": "collector-consumer",
                    "component_version": "1",
                    "worker_id": worker_id,
                    "signal": signum,
                }
            )
        stop_event.set()

    return request_shutdown


def build_default_runtime(config: ConsumerConfig, event_sink: EventSink) -> ConsumerRuntime:
    """Late-bind DB and processing modules so pure CLI helpers stay import-safe."""

    try:
        processing_module = importlib.import_module(
            "server.app.modules.collector.consumer_processing"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "server.app.modules.collector.consumer_processing":
            raise ConsumerStartupError(
                "consumer_processing.process_claimed_transfer is not available"
            ) from exc
        raise
    processor = getattr(processing_module, "process_claimed_transfer", None)
    if not callable(processor):
        raise ConsumerStartupError("consumer_processing.process_claimed_transfer is not available")

    from server.app.db.session import SessionLocal

    return ConsumerRuntime(
        config=config,
        queue=SqlAlchemyConsumerQueue(SessionLocal),
        processor=cast(ConsumerProcessor, processor),
        event_sink=event_sink,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runtime_factory: RuntimeFactory = build_default_runtime,
) -> int:
    parser = argparse.ArgumentParser(description="Run the standalone GEO Collector Consumer")
    parser.add_argument(
        "mode",
        nargs="?",
        default="run",
        choices=("run", "once", "check"),
    )
    args = parser.parse_args(argv)
    environment = os.environ if environ is None else environ

    try:
        config = load_consumer_settings(environment)
    except ValueError as exc:
        print(f"invalid consumer configuration: {exc}", file=sys.stderr)
        return 2

    event_stream = sys.stderr if args.mode == "check" else sys.stdout
    event_sink = JsonEventSink(event_stream)
    try:
        runtime = runtime_factory(config, event_sink)
    except ConsumerStartupError as exc:
        print(f"consumer startup failed: {exc}", file=sys.stderr)
        return 2

    if args.mode == "check":
        snapshot = runtime.check_readiness()
        print(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
        return 0 if bool(snapshot.get("ready")) else 1

    stop_event = threading.Event()
    handler = build_shutdown_handler(
        stop_event,
        event_sink,
        worker_id=config.worker_id,
    )
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    if args.mode == "once":
        report = runtime.run_cycle(stop_event=stop_event)
        return 1 if report.errors else 0

    runtime.run_forever(stop_event=stop_event)
    return 0


def _read_int(environ: Mapping[str, str], name: str, *, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _read_float(environ: Mapping[str, str], name: str, *, default: float) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


if __name__ == "__main__":
    raise SystemExit(main())
