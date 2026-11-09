from typing import Literal
from datetime import datetime
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictBool, model_validator


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    secret: SecretStr
    symbol: Literal["EURUSD", "EUR/USD"]
    direction: Literal["BUY", "SELL", "WAIT", "INVALIDATION"]
    timeframe: str = Field(pattern=r"^(?:[1-9][0-9]{0,3}[DWM]?|[DWM])$")
    price: float = Field(gt=0)
    rsi: float = Field(ge=0, le=100)
    sma200: float = Field(gt=0)
    ema14: float = Field(gt=0)
    gmma_status: Literal["BULLISH", "BEARISH", "MIXED"]
    volume_status: Literal["INCREASING", "DECREASING", "NORMAL", "UNAVAILABLE"]
    # Version 1 remains accepted. A version 2 payload must include the full
    # new contract; optional values are not silently fabricated for old clients.
    schema_version: Literal[1, 2, 3] = 1
    scenario_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{1,160}$")
    gmma_short_spread: float | None = Field(default=None, ge=0)
    gmma_long_spread: float | None = Field(default=None, ge=0)
    trend_status: Literal["BULLISH", "BEARISH", "NEUTRAL"] | None = None
    signal_status: Literal["PROVISIONAL", "CONFIRMED", "INVALIDATED"] | None = None
    volume_type: Literal["TICK", "BASE", "QUOTE", "UNKNOWN", "UNAVAILABLE"] | None = None
    is_test: StrictBool
    signal_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,160}$")
    candle_time: AwareDatetime
    relative_volume: float | None = Field(default=None, ge=0)
    trend: Literal["BULLISH", "BEARISH", "NEUTRAL"] | None = None
    structure: Literal["HIGHER_HIGHS_LOWS", "LOWER_HIGHS_LOWS", "MIXED"] | None = None
    confirmation: Literal["CONFIRMED", "UNCONFIRMED"] | None = None
    event_type: Literal["SIGNAL", "INVALIDATION", "TRANSITION"] = "SIGNAL"
    invalidates_signal_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{1,160}$")

    @model_validator(mode="before")
    @classmethod
    def normalize_invalidation(cls, data):
        if isinstance(data, dict) and data.get("direction") == "INVALIDATION":
            data = dict(data)
            if data.get("event_type", "INVALIDATION") != "INVALIDATION":
                raise ValueError("direction INVALIDATION requiere event_type INVALIDATION")
            data["direction"] = "WAIT"
            data["event_type"] = "INVALIDATION"
            if "signal_status" not in data:
                data["signal_status"] = "INVALIDATED"
        return data

    @model_validator(mode="after")
    def check_invalidation(self):
        phase2 = {"gmma_short_spread", "gmma_long_spread", "trend_status", "signal_status"}
        if self.schema_version in (2, 3) or self.model_fields_set & phase2:
            if any(getattr(self, name) is None for name in phase2):
                raise ValueError("Fase 2 requiere gmma_short_spread, gmma_long_spread, trend_status y signal_status")
        if self.trend and self.trend_status and self.trend != self.trend_status:
            raise ValueError("trend y trend_status no pueden contradecirse")
        if self.confirmation and self.signal_status:
            expected = "CONFIRMED" if self.signal_status == "CONFIRMED" else "UNCONFIRMED"
            if self.confirmation != expected:
                raise ValueError("confirmation y signal_status no pueden contradecirse")
        if self.schema_version == 3 and not self.scenario_id:
            raise ValueError("La versión 3 requiere scenario_id")
        if self.event_type in ("INVALIDATION", "TRANSITION") and not self.invalidates_signal_id:
            raise ValueError("La invalidación o transición requiere invalidates_signal_id")
        if self.event_type == "TRANSITION":
            if self.schema_version != 3:
                raise ValueError("TRANSITION requiere schema_version 3")
            if self.signal_status != "CONFIRMED":
                raise ValueError("TRANSITION requiere un nuevo estado CONFIRMED")
            if self.invalidates_signal_id == self.signal_id:
                raise ValueError("Una transición no puede invalidarse a sí misma")
        if self.event_type == "SIGNAL" and self.invalidates_signal_id:
            raise ValueError("Use event_type INVALIDATION o TRANSITION")
        if self.event_type == "INVALIDATION" and self.signal_status not in (None, "INVALIDATED"):
            raise ValueError("Una invalidación debe tener signal_status INVALIDATED")
        if self.event_type == "SIGNAL" and self.signal_status == "INVALIDATED":
            raise ValueError("Use event_type INVALIDATION para signal_status INVALIDATED")
        if self.volume_status == "UNAVAILABLE" and self.volume_type not in (None, "UNAVAILABLE"):
            raise ValueError("Volumen no disponible requiere volume_type UNAVAILABLE")
        if self.volume_status == "UNAVAILABLE" and self.relative_volume is not None:
            raise ValueError("No proporcione volumen relativo cuando no hay volumen")
        if self.volume_type == "UNAVAILABLE" and self.volume_status != "UNAVAILABLE":
            raise ValueError("volume_type UNAVAILABLE requiere volume_status UNAVAILABLE")
        return self


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    signal_id: str
    symbol: str
    direction: str
    timeframe: str
    price: float
    payload: dict
    status: str
    received_at: datetime
    expires_at: datetime


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entered_at: AwareDatetime


class RecoveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["SYNC_LOCAL_POINTER", "CLEAR_LOCAL_POINTER"]
    is_test: StrictBool
    signal_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{1,160}$")
    rejected_signal_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{1,160}$")
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def validate_recovery(self):
        if self.action == "SYNC_LOCAL_POINTER" and not self.signal_id:
            raise ValueError("SYNC_LOCAL_POINTER requiere el signal_id aceptado que se va a sincronizar")
        if self.action == "CLEAR_LOCAL_POINTER" and self.signal_id:
            raise ValueError("CLEAR_LOCAL_POINTER no admite signal_id")
        if not self.reason.strip():
            raise ValueError("La recuperación requiere un motivo")
        return self


class RecoveryAuditDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["authorized", "rejected"]
    operator_id: str = Field(pattern=r"^[A-Za-z0-9_.@-]{1,100}$")
    scenario_id: str
    is_test: StrictBool
    action: Literal["SYNC_LOCAL_POINTER", "CLEAR_LOCAL_POINTER"]
    signal_id: str | None = None
    rejected_signal_id: str | None = None
    reason: str
    requested_at: AwareDatetime
    resolved_signal_id: str | None = None
    authorized_at: AwareDatetime | None = None
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "authorized" and (not self.authorized_at or self.rejection_reason):
            raise ValueError("Una recuperación autorizada requiere authorized_at y no puede tener rechazo")
        if self.outcome == "rejected" and (self.authorized_at or not self.rejection_reason):
            raise ValueError("Una recuperación rechazada requiere un motivo y no puede tener authorized_at")
        return self
