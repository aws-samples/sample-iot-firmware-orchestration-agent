"""Integration tests for pause counter escalation (H3).

Validates that:
- pause_count increments on each PAUSE iteration in the state machine
- After 3 pauses, state machine routes to ExecuteRollback
- Counter is independent of LLM output
"""

import json
import sys
from pathlib import Path

import pytest

# Add lambda directory to path for imports
_lambda_dir = str(Path(__file__).resolve().parent.parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)


@pytest.mark.integration
class TestPauseCounterStateMachineLogic:
    """Test state machine pause counter logic by simulating state transitions.

    These tests validate the ASL logic by verifying that the IncrementPauseCount
    and CheckRetryCount states produce the correct outputs given specific inputs.
    """

    def test_pause_count_initialized_to_zero(self):
        """Map Parameters initialize pause_count to 0."""
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        map_state = asl["States"]["ExecuteWaveIterator"]
        params = map_state["Parameters"]
        assert params["pause_count"] == 0

    def test_increment_pause_count_state_exists(self):
        """IncrementPauseCount state is defined in the iterator."""
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        iterator_states = asl["States"]["ExecuteWaveIterator"]["Iterator"]["States"]
        assert "IncrementPauseCount" in iterator_states

        increment_state = iterator_states["IncrementPauseCount"]
        assert increment_state["Type"] == "Pass"
        assert increment_state["Next"] == "CheckRetryCount"
        # Verify it uses States.MathAdd
        assert "States.MathAdd($.pause_count, 1)" in json.dumps(increment_state["Parameters"])

    def test_check_retry_count_reads_state_machine_counter(self):
        """CheckRetryCount reads $.pause_count (not $.assess_result.pause_count)."""
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        iterator_states = asl["States"]["ExecuteWaveIterator"]["Iterator"]["States"]
        check_state = iterator_states["CheckRetryCount"]

        assert check_state["Type"] == "Choice"
        choice = check_state["Choices"][0]
        assert choice["Variable"] == "$.pause_count"
        assert choice["NumericGreaterThanEquals"] == 3
        assert choice["Next"] == "ExecuteRollback"

    def test_decision_branch_pause_goes_to_increment(self):
        """PAUSE decision routes to IncrementPauseCount, not directly to CheckRetryCount."""
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        iterator_states = asl["States"]["ExecuteWaveIterator"]["Iterator"]["States"]
        decision_branch = iterator_states["DecisionBranch"]

        pause_choice = next(
            c for c in decision_branch["Choices"]
            if c.get("StringEquals") == "PAUSE"
        )
        assert pause_choice["Next"] == "IncrementPauseCount"

    def test_counter_independent_of_llm_output(self):
        """The state machine counter does not depend on LLM-returned pause_count."""
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        iterator_states = asl["States"]["ExecuteWaveIterator"]["Iterator"]["States"]

        # AssessWaveHealth ResultSelector should NOT include pause_count
        assess_state = iterator_states["AssessWaveHealth"]
        result_selector = assess_state["ResultSelector"]
        assert "pause_count" not in result_selector.get("pause_count.$", "")
        # Verify it captures failed_thing_names instead
        assert "failed_thing_names.$" in result_selector

    def test_pause_escalation_after_three_pauses(self):
        """Simulates 3 PAUSE iterations leading to rollback.

        Given pause_count starts at 0:
        - After 1st PAUSE: IncrementPauseCount -> pause_count=1 -> CheckRetryCount -> PauseAndRetry
        - After 2nd PAUSE: IncrementPauseCount -> pause_count=2 -> CheckRetryCount -> PauseAndRetry
        - After 3rd PAUSE: IncrementPauseCount -> pause_count=3 -> CheckRetryCount -> ExecuteRollback
        """
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        iterator_states = asl["States"]["ExecuteWaveIterator"]["Iterator"]["States"]
        check_state = iterator_states["CheckRetryCount"]

        threshold = check_state["Choices"][0]["NumericGreaterThanEquals"]
        default_next = check_state["Default"]

        # Simulate counter progression
        pause_count = 0
        for iteration in range(1, 4):
            pause_count += 1  # IncrementPauseCount
            if pause_count >= threshold:
                # Would go to ExecuteRollback
                assert iteration == 3, f"Expected rollback at iteration 3, got {iteration}"
                break
            else:
                # Would go to PauseAndRetry
                assert default_next == "PauseAndRetry"

        assert pause_count == 3

    def test_pause_and_retry_preserves_state(self):
        """PauseAndRetry goes back to AssessWaveHealth (preserving all state)."""
        asl_path = Path(__file__).resolve().parent.parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl = json.load(f)

        iterator_states = asl["States"]["ExecuteWaveIterator"]["Iterator"]["States"]
        pause_state = iterator_states["PauseAndRetry"]

        assert pause_state["Type"] == "Wait"
        assert pause_state["Next"] == "AssessWaveHealth"
        # Wait state preserves the entire state (including pause_count)
        # because it doesn't modify ResultPath
