"""
Tests for interpreter isolation (RFC-004 section 4.4, property from section 2.4).

`interpret_research_question` must extract a `ResearchIntent` from the
question text and domain knowledge alone, never from a backend's
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


def test_load_skill_context_with_backend_appends_rather_than_substitutes():
    # The backend fragment is appended, not substituted: everything the
    # backend-free call would return is still present.
    default_context = load_skill_context()
    backend_context = load_skill_context(backend="hyperflow")
    assert default_context in backend_context


# ---------------------------------------------------------------------------
# The other direction. Asserting only that backend text is absent would pass
# just as happily if the interpreter received no knowledge at all, which is
# exactly how the knowledge layer could disappear unnoticed: intent extraction
# would silently fall back to the model's parametric knowledge -- the paper's
# S0 condition, 44% full-match against 83% with the vocabulary documents -- and
# every remaining test would still be green, because the ones that read these
# files skip when they are missing rather than failing.
#
# These anchors are deliberately concrete. A population code and a GRCh37
# coordinate cannot be satisfied by an empty string, a stray heading, or a
# directory that exists but holds nothing.
# ---------------------------------------------------------------------------

KNOWLEDGE_ANCHORS = [
    ("GBR", "a population code from populations.md"),
    ("BRCA1", "a gene name from genomic-regions.md"),
    ("43044295", "BRCA1's GRCh37 start coordinate"),
    ("HLA", "a named region"),
]


@pytest.mark.parametrize("anchor,description", KNOWLEDGE_ANCHORS)
def test_domain_knowledge_reaches_the_skill_context(anchor, description):
    context = load_skill_context()
    assert anchor in context, (
        f"{anchor!r} ({description}) is missing from the skill context. "
        "The knowledge layer is not reaching intent interpretation."
    )


@pytest.mark.skipif(
    not llm_interpreter.HAS_LLM_DEPS,
    reason="instructor/litellm not installed; interpret_research_question is unusable without them",
)
@pytest.mark.parametrize("anchor,description", KNOWLEDGE_ANCHORS)
def test_interpreter_prompt_carries_domain_knowledge(monkeypatch, anchor, description):
    """The anchors must survive all the way into the prompt, not merely load."""
    stub_client = _CapturingClient()
    monkeypatch.setattr(llm_interpreter, "get_client", lambda: stub_client)

    llm_interpreter.interpret_research_question("Compare BRCA1 variants in British individuals.")

    prompt = stub_client.completions.system_prompt
    assert prompt is not None, "the interpreter never issued a request"
    assert anchor in prompt, (
        f"{anchor!r} ({description}) never reached the system prompt, so the "
        "model is interpreting on parametric knowledge alone."
    )


# ---------------------------------------------------------------------------
# Resource sizing is for RESOLVE, not for the stage that reads a research
# question. A ResearchIntent has no resource fields, so these implementation
# details cannot change the interpreter's output -- they can only crowd the
# vocabulary the interpretation actually depends on. There is no longer a
# knowledge document that carries them into a prompt at all (capacity is
# computed deterministically -- see docs/CAPACITY-IMPLEMENTATION-PLAN.md section
# 3.C and docs/capacity-model.md); this guards that they do not reappear by
# some other route, such as domain knowledge growing a resource-sizing
# section.
# ---------------------------------------------------------------------------

POLICY_MARKERS = [
    ("mem_budget_mb", "a ComputeEnvironment field (core/environment.py)"),
    ("engine_reserve", "the engine's core reservation"),
    ("recommend_parallelism", "the sizing entry point in core/parallelism.py"),
]


@pytest.mark.parametrize("marker,description", POLICY_MARKERS)
def test_policy_is_absent_from_the_default_context(marker, description):
    assert marker not in load_skill_context(), (
        f"{marker!r} ({description}) reached the interpretation context, which "
        "cannot act on it."
    )


@pytest.mark.skipif(
    not llm_interpreter.HAS_LLM_DEPS,
    reason="instructor/litellm not installed; interpret_research_question is unusable without them",
)
def test_interpreter_prompt_carries_no_policy(monkeypatch):
    stub_client = _CapturingClient()
    monkeypatch.setattr(llm_interpreter, "get_client", lambda: stub_client)

    llm_interpreter.interpret_research_question("Compare BRCA1 variants in British individuals.")

    prompt = stub_client.completions.system_prompt
    for marker, description in POLICY_MARKERS:
        assert marker not in prompt, (
            f"{marker!r} ({description}) reached the system prompt"
        )
