import pytest

from packages.orchestration.dag import DependencyGraph, GraphError
from packages.orchestration.snapshots import freeze_snapshot
from packages.orchestration.state import StateError, transition_revision, transition_task


def test_dag_cycle_and_unknown_dependencies_are_rejected():
    with pytest.raises(GraphError, match="CYCLE"):
        DependencyGraph({"a": ["b"], "b": ["a"]})
    with pytest.raises(GraphError, match="UNKNOWN"):
        DependencyGraph({"a": ["missing"]})


def test_dag_transitive_staleness_and_deterministic_topology():
    graph = DependencyGraph({"quote": ["bom"], "rtl": ["contract"], "bom": ["contract"], "smoke": ["rtl", "bom"], "contract": [], "uart": []})
    assert graph.topological_order() == ["contract", "bom", "quote", "rtl", "smoke", "uart"]
    assert graph.invalidate(["contract"]) == {"contract": ["contract"], "bom": ["contract"], "quote": ["contract"], "rtl": ["contract"], "smoke": ["contract"]}
    assert graph.rebuild_plan(["bom"]) == ["bom", "quote", "smoke"]


def test_revision_verification_requires_current_valid_independent_pass():
    with pytest.raises(StateError):
        transition_revision("VERIFYING", "VERIFIED", blocking_evidence=[])
    with pytest.raises(StateError):
        transition_revision("VERIFYING", "VERIFIED", blocking_evidence=[{"status": "PASS", "valid": False, "independent": True}])
    assert transition_revision("VERIFYING", "VERIFIED", blocking_evidence=[{"status": "PASS", "valid": True, "independent": True}]) == "VERIFIED"
    with pytest.raises(StateError):
        transition_revision("BUILDING", "RELEASED")
    with pytest.raises(StateError):
        transition_revision("WAITING_APPROVAL", "FROZEN")


def test_cancel_and_lost_tasks_do_not_invent_stopped_process():
    with pytest.raises(StateError):
        transition_task("CANCEL_REQUESTED", "CANCELLED")
    assert transition_task("CANCEL_REQUESTED", "CANCELLED", process_stopped=True) == "CANCELLED"
    with pytest.raises(StateError):
        transition_task("LOST", "QUEUED", reconciled=True)
    assert transition_task("LOST", "QUEUED", reconciled=True, safe_to_retry=True) == "QUEUED"


def test_snapshot_is_detached_and_hash_is_stable():
    value = {"revision_id": "r", "models": [{"version": "1"}]}
    snap = freeze_snapshot(value)
    value["models"][0]["version"] = "2"
    assert snap.to_dict()["models"][0]["version"] == "1"
    assert snap.sha256 == freeze_snapshot({"models": [{"version": "1"}], "revision_id": "r"}).sha256
