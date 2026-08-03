"""
Load knowledge documents for LLM context.

The documents are split by owner (RFC-004 §2.3) into three directories:

- ``knowledge/domain/``   — population codes, region coordinates, research
  contexts, data sources, interpretation guidelines. Owned by the genomics
  curator; never engine-specific.
- ``knowledge/policy/``   — memory budgets, vCPU profiles, work-per-task
  guidance. Owned by whoever knows the target machine; never engine-specific.
- ``knowledge/backends/`` — how to invoke a given engine. Owned by the
  backend maintainer; engine-specific by definition.

Callers historically addressed a document by a flat basename (``SKILL_DIR /
"populations.md"``). ``SKILL_DIR`` keeps that basename-addressed shape as a
thin lookup over the split tree, so existing callers and tests do not need to
know which of the three directories a document now lives in.
"""
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = PACKAGE_DIR / "knowledge"

DOMAIN_DIR = KNOWLEDGE_DIR / "domain"
POLICY_DIR = KNOWLEDGE_DIR / "policy"
BACKENDS_DIR = KNOWLEDGE_DIR / "backends"

# Basename -> real location. "SKILL.md" is kept as a legacy alias for the
# HyperFlow tool manual it used to contain in full (it is now split three
# ways; the manual itself became knowledge/backends/hyperflow.md).
_LOCATIONS: dict[str, Path] = {
    "hyperflow.md": BACKENDS_DIR / "hyperflow.md",
    "SKILL.md": BACKENDS_DIR / "hyperflow.md",
    "individuals-parallelism.md": POLICY_DIR / "individuals-parallelism.md",
    "resource-policy.md": POLICY_DIR / "resource-policy.md",
    "interpretation.md": DOMAIN_DIR / "interpretation.md",
    "populations.md": DOMAIN_DIR / "populations.md",
    "genomic-regions.md": DOMAIN_DIR / "genomic-regions.md",
    "research-contexts.md": DOMAIN_DIR / "research-contexts.md",
    "data-sources.md": DOMAIN_DIR / "data-sources.md",
}


class _SkillDir:
    """Basename-addressed view over ``knowledge/{domain,policy,backends}``.

    Supports the one operation callers rely on, ``SKILL_DIR / filename``,
    and resolves to the document's real nested path. Unknown basenames fall
    back to a path directly under ``knowledge/`` (which simply won't exist,
    matching the old "not every SKILL_FILES entry has to exist" behaviour).
    """

    def __truediv__(self, filename: str) -> Path:
        return _LOCATIONS.get(filename, KNOWLEDGE_DIR / filename)

    def __repr__(self) -> str:
        return f"_SkillDir({KNOWLEDGE_DIR!r})"


SKILL_DIR = _SkillDir()

# Every known knowledge document, still used by mcp_server.py to expose the
# full set as browsable resources regardless of backend. `load_skill_context`
# below does NOT load all of these unconditionally -- see its docstring.
SKILL_FILES = [
    "hyperflow.md",
    "individuals-parallelism.md",
    "interpretation.md",
    "populations.md",
    "genomic-regions.md",
    "research-contexts.md",
    "data-sources.md",
    "resource-policy.md",
]


def load_skill_context(backend: str | None = None, *, include_policy: bool = False) -> str:
    """Compose the knowledge a given stage needs.

    Domain knowledge is always included. Policy and backend knowledge are
    opt-in, because each is useful at exactly one stage and inert at the
    others.

    ``include_policy`` adds ``knowledge/policy/`` -- memory budgets, vCPU
    profiles, work per task. It is off by default because the stage that calls
    this with defaults is interpretation, and a ``ResearchIntent`` carries no
    resource fields for that knowledge to inform: it made up 44% of the
    interpreter's context while unable to change its output. That is not
    merely wasted context. The Skills ablation records GPT-4.1-mini scoring
    8.7pp lower with the full document set than with vocabulary alone, its
    clarification accuracy falling from 53% to 13%, which is what surplus
    context does to this task. Whoever chooses parallelism wants these
    documents; the MCP server exposes them as resources for exactly that.

    With ``backend=None`` nothing under ``knowledge/backends/`` is loaded.
    This is the interpreter-isolation property from RFC-004 §2.4: the
    extracted ``ResearchIntent`` must not be influenced by which engine will
    eventually run it, so the interpretation call path must never pass a
    backend here.

    With a ``backend`` name, the one fragment that backend declares (its
    ``skill_fragment``, looked up through the backend registry so it always
    matches what `backends/__init__.py:get_backend` returns for 4.1/4.2
    registered backends) is appended after the domain/policy content.

    Raises:
        ValueError: if ``backend`` is given but not a registered backend name.
    """
    parts = []

    for filename in SKILL_FILES:
        filepath = SKILL_DIR / filename
        if filepath.is_relative_to(BACKENDS_DIR):
            continue  # engine-specific: never part of the default context
        if filepath.is_relative_to(POLICY_DIR) and not include_policy:
            continue  # sizing knowledge: only for the stage that sizes
        if filepath.exists():
            content = filepath.read_text()
            parts.append(f"# {filename}\n\n{content}")

    if backend is not None:
        # Import the backend's package (not just `..backends`) so its
        # module-level `register(...)` call has actually run -- registration
        # is a side effect of importing the specific backend, not of
        # importing the `backends` package itself.
        import importlib

        try:
            importlib.import_module(f"workflow_composer.backends.{backend}")
        except ModuleNotFoundError:
            pass  # no such backend package; get_backend below raises clearly
        from .. import backends

        fragment = backends.get_backend(backend).skill_fragment
        if fragment:
            filepath = BACKENDS_DIR / fragment
            if filepath.exists():
                content = filepath.read_text()
                parts.append(f"# {fragment}\n\n{content}")

    return "\n\n---\n\n".join(parts)


def get_skill_dir() -> Path:
    """Return the basename-addressed knowledge directory view."""
    return SKILL_DIR
