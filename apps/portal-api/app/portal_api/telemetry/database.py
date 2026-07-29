"""Database-to-telemetry binding without changing domain service signatures."""

from __future__ import annotations

from weakref import WeakKeyDictionary

from sqlalchemy.engine import Engine

from portal_api.telemetry.metrics import TelemetryRecorder

_ENGINE_TELEMETRY: WeakKeyDictionary[Engine, TelemetryRecorder] = WeakKeyDictionary()


def bind_engine_telemetry(engine: Engine, telemetry: TelemetryRecorder) -> None:
    _ENGINE_TELEMETRY[engine] = telemetry


def telemetry_for_engine(engine: Engine) -> TelemetryRecorder | None:
    return _ENGINE_TELEMETRY.get(engine)
