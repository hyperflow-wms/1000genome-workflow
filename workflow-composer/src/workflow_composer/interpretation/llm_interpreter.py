"""
LLM-based research question interpretation.
Uses LiteLLM + Instructor for provider-agnostic structured extraction.
"""
from __future__ import annotations

import warnings

# Suppress deprecation warnings from instructor's Google dependencies
# (we use LiteLLM, not the deprecated google.generativeai directly)
warnings.filterwarnings("ignore", category=FutureWarning, module="google")
warnings.filterwarnings("ignore", category=FutureWarning, module="instructor.providers.gemini")

try:
    import instructor
    import litellm
    from pydantic_settings import BaseSettings
    HAS_LLM_DEPS = True
except ImportError:
    HAS_LLM_DEPS = False
    BaseSettings = object  # Fallback for type hints

from ..core.models import ResearchIntent, GenomicRegion
from .skill_loader import load_skill_context


class LLMConfig(BaseSettings if HAS_LLM_DEPS else object):
    """LLM configuration from environment."""
    model: str = "gemini/gemini-2.0-flash"

    if HAS_LLM_DEPS:
        class Config:
            env_prefix = "WORKFLOW_COMPOSER_"


def get_client():
    """Get instructor-wrapped LiteLLM client."""
    if not HAS_LLM_DEPS:
        raise ImportError(
            "LLM dependencies not installed. "
            "Install with: pip install workflow-composer[llm]"
        )
    return instructor.from_litellm(litellm.completion)


def interpret_research_question(
    question: str,
    config: LLMConfig | None = None
) -> ResearchIntent:
    """
    Interpret a natural language research question into structured intent.

    Args:
        question: Natural language research question
        config: LLM configuration (uses defaults/env if not provided)

    Returns:
        Structured ResearchIntent
    """
    if not HAS_LLM_DEPS:
        raise ImportError(
            "LLM dependencies not installed. "
            "Install with: pip install workflow-composer[llm]"
        )

    if config is None:
        config = LLMConfig()

    client = get_client()
    skill_context = load_skill_context()

    system_prompt = f"""You are a genomics research workflow planning assistant.

Your task is to interpret research questions and extract structured parameters
for workflow generation.

{skill_context}

Based on the user's question, extract:
1. analysis_type: What kind of analysis is being requested?
2. populations: Which population(s) are involved?
3. chromosomes: Which chromosome(s) if specified (null if not)
4. regions: Which genomic region(s) if specified (null if not)
5. focus: What type of variants to focus on?

Use the mappings in the skill documents to translate natural language
to the correct codes and coordinates.
"""

    return client.chat.completions.create(
        model=config.model,
        response_model=ResearchIntent,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
    )
