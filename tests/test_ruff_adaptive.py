"""RuffAgent's expand_context resolution -- the regret signal the adaptive
controller learns from. Tested in isolation, no network."""

import dataclasses
from pathlib import Path

from agent.ruff import RuffAgent, TurnRecord
from segpilot.compressor import ARMS
from segpilot.config import SegpilotConfig
from segpilot.policy.adaptive import AdaptiveController
from segpilot.policy.shadow import ShadowStore


class _StubCompressor:
    """Only `.shadow` is exercised by _resolve_expand_context."""

    def __init__(self, shadow):
        self.shadow = shadow


class _StubTask:
    """Only `.instruction` (used by RuffAgent.__init__ to seed messages) is
    exercised here -- _resolve_expand_context never touches the task."""

    instruction = "irrelevant for this test"


def _agent(*, controller=None, shadow=None):
    arm = ARMS["adaptive"] if controller is not None else ARMS["stock"]
    if controller is not None:
        arm = dataclasses.replace(arm, controller=controller)
    return RuffAgent(
        workdir=Path("."), task=_StubTask(), config=SegpilotConfig.load(), arm=arm,
        compressor=_StubCompressor(shadow), upstream=None,
        session_path=Path("unused.jsonl"), verbose=False,
    )


def test_resolves_known_ref_and_returns_original():
    shadow = ShadowStore()
    shadow.put("abc123", "the original long text", kind="file_read")
    agent = _agent(controller=AdaptiveController(), shadow=shadow)
    record = TurnRecord(turn=1)

    output = agent._resolve_expand_context("abc123", record)

    assert output == "the original long text"
    assert record.regrets == 1


def test_unknown_ref_returns_error_text_without_crashing():
    shadow = ShadowStore()
    agent = _agent(controller=AdaptiveController(), shadow=shadow)
    record = TurnRecord(turn=1)

    output = agent._resolve_expand_context("does-not-exist", record)

    assert "no content found" in output
    assert record.regrets == 0   # not a resolved regret if there was nothing to regret


def test_regret_is_attributed_to_the_shadow_entrys_kind_not_current_state():
    shadow = ShadowStore()
    shadow.put("id1", "original A", kind="log_output")
    controller = AdaptiveController()
    agent = _agent(controller=controller, shadow=shadow)
    record = TurnRecord(turn=1)

    agent._resolve_expand_context("id1", record)

    snap = controller.snapshot()
    assert snap["log_output"]["regrets"] == 1
    assert "file_read" not in snap


def test_resolving_regret_can_trigger_backoff_after_escalation():
    """End-to-end of the loop this exists for: escalate a bucket, then watch
    enough expand_context calls push it back to conservative."""
    shadow = ShadowStore()
    controller = AdaptiveController()
    for _ in range(5):
        controller.record_compression("file_read")   # escalates: 0/5 regret
    assert controller.should_route("file_read") is True

    shadow.put("r1", "orig", kind="file_read")
    agent = _agent(controller=controller, shadow=shadow)
    record = TurnRecord(turn=6)
    for _ in range(3):
        # same ref repeatedly resolved -- simplification, still exercises the
        # counting/threshold logic identically to three distinct regrets
        agent._resolve_expand_context("r1", record)

    assert controller.should_route("file_read") is False   # backed off
    assert record.regrets == 3


def test_missing_shadow_store_does_not_crash():
    agent = _agent(controller=AdaptiveController(), shadow=None)
    record = TurnRecord(turn=1)
    output = agent._resolve_expand_context("anything", record)
    assert "no content found" in output
    assert record.regrets == 0
