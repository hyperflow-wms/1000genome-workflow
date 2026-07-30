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
    """LLM configuration from environment.

    Override the model with ``WORKFLOW_COMPOSER_MODEL`` (see the ``env_prefix``
    below), or per-invocation with ``--model``. The default is a floating alias
    rather than a pinned version: Google retires dated Gemini models, and a
    retired default fails every interpretation with a 404 until someone edits
    this line.
    """
    model: str = "gemini/gemini-flash-latest"

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
    return instructor.from_litellm(litellm.completion, mode=instructor.Mode.JSON_SCHEMA)


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
3. chromosomes: Which chromosome(s) if explicitly specified by number (null if not).
   Do NOT set chromosomes when a gene or region name is mentioned — use regions instead.
4. regions: If the user mentions a gene name (e.g., BRCA1, TP53, CFTR) or a named
   genomic region (e.g., HLA), look up its chromosome and coordinates in the
   genomic-regions.md table above and return the full GenomicRegion with name,
   chromosome, start, and end. This is REQUIRED whenever a gene or region name
   appears in the question.
5. focus: What type of variants to focus on?

IMPORTANT: When a gene name like BRCA1 or HLA is mentioned, you MUST populate
the regions field with the corresponding coordinates from the genomic-regions
table. Never leave regions as null when a known gene or region is referenced.

6. clarification_needed: If the question is too vague, missing critical parameters
   (e.g., no population specified, ambiguous scope), or contains invalid/unrecognizable
   terms that cannot be mapped to valid 1000 Genomes codes, set clarification_needed=True
   and explain what is missing or invalid in clarification_reason.
   Still extract whatever parameters you CAN identify — but flag the gap.
"""

    return client.chat.completions.create(
        model=config.model,
        response_model=ResearchIntent,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
    )
