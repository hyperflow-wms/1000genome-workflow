"""
Load skills documents for LLM context.
"""
from pathlib import Path

# Skills are inside the package (src/workflow_composer/skills/)
SKILL_DIR = Path(__file__).parent.parent / "skills"

SKILL_FILES = [
    "SKILL.md",
    "populations.md",
    "genomic-regions.md",
    "research-contexts.md",
    "data-sources.md",
    "resource-policy.md",
]


def load_skill_context() -> str:
    """Load all skill documents as a single context string."""
    parts = []

    for filename in SKILL_FILES:
        filepath = SKILL_DIR / filename
        if filepath.exists():
            content = filepath.read_text()
            parts.append(f"# {filename}\n\n{content}")

    return "\n\n---\n\n".join(parts)


def get_skill_dir() -> Path:
    """Return path to skills directory."""
    return SKILL_DIR
