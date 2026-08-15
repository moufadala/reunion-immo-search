import pytest

from src.listing_lifecycle import (
    LifecycleState,
    ListingLifecycle,
    Observation,
    apply_observation,
)


def apply(state, run_id, observation, succeeded=True):
    return apply_observation(
        state,
        run_id=run_id,
        observation=observation,
        source_run_succeeded=succeeded,
    )


def test_two_successful_source_absences_are_required_for_withdrawal():
    initial = ListingLifecycle(source="portal", listing_id="42")

    first = apply(initial, "run-1", Observation.SOURCE_MISSING)
    second = apply(first.lifecycle, "run-2", Observation.SOURCE_MISSING)

    assert first.lifecycle.state is LifecycleState.MISSING_PENDING
    assert first.lifecycle.successful_missing_runs == 1
    assert first.events[0].reason == "first_successful_missing_run"
    assert second.lifecycle.state is LifecycleState.WITHDRAWN
    assert second.lifecycle.successful_missing_runs == 2
    assert second.events[0].reason == "missing_threshold_reached"


def test_failed_source_run_never_counts_as_an_absence():
    initial = ListingLifecycle(source="portal", listing_id="42")

    result = apply(initial, "run-1", Observation.SOURCE_MISSING, succeeded=False)

    assert result.lifecycle == initial
    assert result.events == ()


@pytest.mark.parametrize(
    "observation",
    [
        Observation.FILTER_HIDDEN,
        Observation.PHOTO_FAILURE,
        Observation.EXPORT_OMITTED,
    ],
)
def test_downstream_failures_and_filters_never_change_lifecycle(observation):
    pending = ListingLifecycle(
        source="portal",
        listing_id="42",
        state=LifecycleState.MISSING_PENDING,
        successful_missing_runs=1,
    )

    result = apply(pending, "run-2", observation)

    assert result.lifecycle == pending
    assert result.events == ()


def test_seen_listing_cancels_pending_withdrawal():
    pending = ListingLifecycle(
        source="portal",
        listing_id="42",
        state=LifecycleState.MISSING_PENDING,
        successful_missing_runs=1,
    )

    result = apply(pending, "run-2", Observation.SOURCE_SEEN)

    assert result.lifecycle.state is LifecycleState.ACTIVE
    assert result.lifecycle.successful_missing_runs == 0
    assert result.events[0].reason == "seen_before_withdrawal"


def test_withdrawn_listing_reappears_then_settles_active():
    withdrawn = ListingLifecycle(
        source="portal",
        listing_id="42",
        state=LifecycleState.WITHDRAWN,
        successful_missing_runs=2,
    )

    appeared = apply(withdrawn, "run-3", Observation.SOURCE_SEEN)
    active = apply(appeared.lifecycle, "run-4", Observation.SOURCE_SEEN)

    assert appeared.lifecycle.state is LifecycleState.REAPPEARED
    assert appeared.events[0].reason == "seen_after_withdrawal"
    assert active.lifecycle.state is LifecycleState.ACTIVE
    assert active.events == ()


def test_same_run_replay_is_an_exact_no_op_and_event_id_is_deterministic():
    initial = ListingLifecycle(source="portal", listing_id="42")
    first = apply(initial, "run-1", Observation.SOURCE_MISSING)
    replay = apply(first.lifecycle, "run-1", Observation.SOURCE_MISSING)
    independent_retry = apply(initial, "run-1", Observation.SOURCE_MISSING)

    assert replay.lifecycle == first.lifecycle
    assert replay.events == ()
    assert independent_retry.events[0].event_id == first.events[0].event_id

def test_old_run_replay_after_a_newer_run_is_still_a_no_op():
    initial = ListingLifecycle(source="portal", listing_id="42")
    first = apply(initial, "run-1", Observation.SOURCE_MISSING)
    seen = apply(first.lifecycle, "run-2", Observation.SOURCE_SEEN)
    replay = apply(seen.lifecycle, "run-1", Observation.SOURCE_MISSING)

    assert replay.lifecycle == seen.lifecycle
    assert replay.events == ()


def test_failed_attempt_does_not_block_successful_retry_with_same_run_id():
    initial = ListingLifecycle(source="portal", listing_id="42")
    failed = apply(initial, "run-1", Observation.SOURCE_MISSING, succeeded=False)
    retry = apply(failed.lifecycle, "run-1", Observation.SOURCE_MISSING)

    assert retry.lifecycle.state is LifecycleState.MISSING_PENDING


def test_withdrawn_state_is_stable_across_more_successful_absences():
    withdrawn = ListingLifecycle(
        source="portal",
        listing_id="42",
        state=LifecycleState.WITHDRAWN,
        successful_missing_runs=2,
    )

    result = apply(withdrawn, "run-3", Observation.SOURCE_MISSING)

    assert result.lifecycle.state is LifecycleState.WITHDRAWN
    assert result.events == ()


@pytest.mark.parametrize("withdrawal_after", [0, -1])
def test_invalid_withdrawal_threshold_is_rejected(withdrawal_after):
    with pytest.raises(ValueError):
        apply_observation(
            ListingLifecycle(source="portal", listing_id="42"),
            run_id="run-1",
            observation=Observation.SOURCE_MISSING,
            source_run_succeeded=True,
            withdrawal_after=withdrawal_after,
        )
