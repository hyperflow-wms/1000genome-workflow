"""
Tests for interpreter isolation (RFC-004 section 4.4, property from section 2.4).

`interpret_research_question` must extract a `ResearchIntent` from the
question text and domain/policy knowledge alone, never from a backend's
"how to invoke this engine" fragment under `knowledge/backends/`. Otherwise
the extracted intent could be influenced by which engine will eventually run
it, and engine independence would be asserted rather than checkable.

This is a regression guard, not a formality: it must fail the moment someone
wires a `backend=` argument (or otherwise injects backend-fragment text) into
the `load_skill_context()` call inside `interpret_research_question`. To make
that failure certain, the tests assert on the *actual text* the interpreter
receives -- every file under `knowledge/backends/` must be entirely absent
from it -- rather than on the `backend` argument value alone, since asserting
only "backend was called with None" would not catch a change that inlines
backend text some other way.

No LLM call is made: `interpret_research_question` is exercised with the
`instructor` client replaced by a stub that captures the system prompt it is
given and returns a canned `ResearchIntent` instead of calling out to a
model. `load_skill_context` itself needs no such stubbing -- it does no I/O
beyond reading local knowledge files.
"""
from __future__ import annotations

import pytest

from workflow_composer.core.models import ResearchIntent
from workflow_composer.interpretation import llm_interpreter
from workflow_composer.interpretation.skill_loader import (
    BACKENDS_DIR,
    load_skill_context,
)


def _backend_fragment_files() -> list:
    files = sorted(BACKENDS_DIR.glob("*.md"))
    assert files, "expected at least one fragment under knowledge/backends/ to test against"
    return files


class _CapturingCompletions:
    """Stands in for `instructor`'s `client.chat.completions`.

    Records the system prompt it was given instead of contacting a model,
    and returns a fixed, valid `ResearchIntent`.
    """

    def __init__(self):
        self.system_prompt: str | None = None

    def create(self, *, model, response_model, messages):
        for message in messages:
            if message["role"] == "system":
                self.system_prompt = message["content"]
        return ResearchIntent(analysis_type="population_comparison", populations=["EUR", "AFR"])


class _CapturingClient:
    def __init__(self):
        self.completions = _CapturingCompletions()

    @property
    def chat(self):
        return self


# ---------------------------------------------------------------------------
# The property from RFC-004 section 2.4: no backend text reaches the
# interpreter, ever, regardless of which backend will eventually run the plan.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not llm_interpreter.HAS_LLM_DEPS,
    reason="instructor/litellm not installed; interpret_research_question is unusable without them",
)
def test_interpreter_receives_no_backend_fragment_text(monkeypatch):
    stub_client = _CapturingClient()
    monkeypatch.setattr(llm_interpreter, "get_client", lambda: stub_client)

    llm_interpreter.interpret_research_question(
        "Compare EUR and AFR populations for the BRCA1 region"
    )

    assert stub_client.completions.system_prompt is not None
    for fragment_file in _backend_fragment_files():
        fragment_text = fragment_file.read_text()
        assert fragment_text not in stub_client.completions.system_prompt, (
            f"{fragment_file.name} content leaked into the interpreter's "
            f"system prompt -- interpret_research_question must call "
            f"load_skill_context() with no backend"
        )


def test_load_skill_context_default_excludes_every_backend_fragment():
    context = load_skill_context()
    for fragment_file in _backend_fragment_files():
        fragment_text = fragment_file.read_text()
        assert fragment_text not in context, (
            f"{fragment_file.name} appeared in load_skill_context() with no "
            f"backend argument"
        )


# ---------------------------------------------------------------------------
# The complementary property: asking for a backend by name does append its
# fragment, so the isolation above is a deliberate default, not a bug that
# happens to drop all knowledge/backends/ content.
# ---------------------------------------------------------------------------

def test_load_skill_context_with_backend_includes_its_fragment():
    context = load_skill_context(backend="hyperflow")
    fragment_text = (BACKENDS_DIR / "hyperflow.md").read_text()
    assert fragment_text in context


def test_load_skill_context_with_backend_still_includes_domain_and_policy():
    # The backend fragment is appended, not substituted: everything the
    # backend-free call would return is still present.
    default_context = load_skill_context()
    backend_context = load_skill_context(backend="hyperflow")
    assert default_context in backend_context
