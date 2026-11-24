"""The Python agent still gives the answers in agent/testdata/conformance.json, the cases the Go
agent is tested against too (docs/develop/agent-protocol.md)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import agent_conformance


def test_conformance_file_is_current():
    assert agent_conformance.OUT.read_text() == agent_conformance.render(), \
        "the Python agent and agent/testdata/conformance.json disagree: run python tools/agent_conformance.py"
