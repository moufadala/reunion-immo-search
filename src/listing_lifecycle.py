"""Deterministic lifecycle state machine for source listings.

The module deliberately knows nothing about publication filters, photos or exports.
Only an authoritative observation made during a successful source run may make a
listing progress toward ``withdrawn``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256


class LifecycleState(str, Enum):
    ACTIVE = "active"
    MISSING_PENDING = "missing_pending"
    WITHDRAWN = "withdrawn"
    REAPPEARED = "reappeared"


class Observation(str, Enum):
    SOURCE_SEEN = "source_seen"
    SOURCE_MISSING = "source_missing"
    FILTER_HIDDEN = "filter_hidden"
    PHOTO_FAILURE = "photo_failure"
    EXPORT_OMITTED = "export_omitted"


NON_AUTHORITATIVE_OBSERVATIONS = {
    Observation.FILTER_HIDDEN,
    Observation.PHOTO_FAILURE,
    Observation.EXPORT_OMITTED,
}


@dataclass(frozen=True, slots=True)
class ListingLifecycle:
    source: str
    listing_id: str
    state: LifecycleState = LifecycleState.ACTIVE
    successful_missing_runs: int = 0
    last_processed_run_id: str | None = None
    processed_run_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    event_id: str
    source: str
    listing_id: str
    run_id: str
    from_state: LifecycleState
    to_state: LifecycleState
    reason: str


@dataclass(frozen=True, slots=True)
class TransitionResult:
    lifecycle: ListingLifecycle
    events: tuple[LifecycleEvent, ...] = field(default_factory=tuple)


def _event(
    lifecycle: ListingLifecycle,
    *,
    run_id: str,
    to_state: LifecycleState,
    reason: str,
) -> LifecycleEvent:
    identity = "\x1f".join(
        (
            lifecycle.source,
            lifecycle.listing_id,
            run_id,
            lifecycle.state.value,
            to_state.value,
            reason,
        )
    )
    return LifecycleEvent(
        event_id=sha256(identity.encode("utf-8")).hexdigest(),
        source=lifecycle.source,
        listing_id=lifecycle.listing_id,
        run_id=run_id,
        from_state=lifecycle.state,
        to_state=to_state,
        reason=reason,
    )


def apply_observation(
    lifecycle: ListingLifecycle,
    *,
    run_id: str,
    observation: Observation,
    source_run_succeeded: bool,
    withdrawal_after: int = 2,
) -> TransitionResult:
    """Apply one observation, returning new state plus an optional durable event.

    Replaying the same run is a no-op. Callers can therefore retry persistence
    safely; event identifiers are deterministic as a second line of defence.
    """
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")
    if withdrawal_after < 1:
        raise ValueError("withdrawal_after must be at least 1")
    if run_id == lifecycle.last_processed_run_id or run_id in lifecycle.processed_run_ids:
        return TransitionResult(lifecycle)

    if observation in NON_AUTHORITATIVE_OBSERVATIONS:
        return TransitionResult(lifecycle)

    # A failed/partial source run has no authority to say whether a listing is
    # present or absent. It must not consume the run id either: a successful retry
    # with the same id can still be applied later.
    if not source_run_succeeded:
        return TransitionResult(lifecycle)

    if observation is Observation.SOURCE_SEEN:
        if lifecycle.state is LifecycleState.WITHDRAWN:
            event = _event(
                lifecycle,
                run_id=run_id,
                to_state=LifecycleState.REAPPEARED,
                reason="seen_after_withdrawal",
            )
            updated = replace(
                lifecycle,
                state=LifecycleState.REAPPEARED,
                successful_missing_runs=0,
                last_processed_run_id=run_id,
                processed_run_ids=(*lifecycle.processed_run_ids, run_id),
            )
            return TransitionResult(updated, (event,))

        # ``reappeared`` is an observable state for one successful sighting. A
        # subsequent successful sighting settles it back to normal active state.
        target = LifecycleState.ACTIVE
        event = None
        if lifecycle.state is LifecycleState.MISSING_PENDING:
            event = _event(
                lifecycle,
                run_id=run_id,
                to_state=target,
                reason="seen_before_withdrawal",
            )
        updated = replace(
            lifecycle,
            state=target,
            successful_missing_runs=0,
            last_processed_run_id=run_id,
            processed_run_ids=(*lifecycle.processed_run_ids, run_id),
        )
        return TransitionResult(updated, (event,) if event else ())

    if observation is not Observation.SOURCE_MISSING:
        raise ValueError(f"unsupported observation: {observation!r}")

    missing_count = lifecycle.successful_missing_runs + 1
    target = (
        LifecycleState.WITHDRAWN
        if missing_count >= withdrawal_after
        else LifecycleState.MISSING_PENDING
    )
    if lifecycle.state is LifecycleState.WITHDRAWN:
        target = LifecycleState.WITHDRAWN

    event = None
    if target is not lifecycle.state:
        reason = (
            "missing_threshold_reached"
            if target is LifecycleState.WITHDRAWN
            else "first_successful_missing_run"
        )
        event = _event(lifecycle, run_id=run_id, to_state=target, reason=reason)

    updated = replace(
        lifecycle,
        state=target,
        successful_missing_runs=missing_count,
        last_processed_run_id=run_id,
        processed_run_ids=(*lifecycle.processed_run_ids, run_id),
    )
    return TransitionResult(updated, (event,) if event else ())
