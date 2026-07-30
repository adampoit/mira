# pyright: standard

"""Provider selection for Mira's review pipeline."""

from __future__ import annotations

from mira.config import MiraConfig
from mira.llm import create_llm
from mira.llm.base import LLMProviderProtocol


def create_review_llms(
    config: MiraConfig,
) -> tuple[LLMProviderProtocol, LLMProviderProtocol]:
    """Create the review and indexing providers used by the existing pipeline."""
    from mira.dashboard.models_config import llm_config_for

    review_config = llm_config_for("review", config.llm)
    indexing_config = llm_config_for("indexing", config.llm)
    if config.review.engine == "pi_agent":
        from mira.llm.pi_provider import PiLLMProvider

        return (
            PiLLMProvider(
                model=review_config.model,
                thinking_level=review_config.reasoning_effort or "off",
            ),
            PiLLMProvider(
                model=indexing_config.model,
                thinking_level=indexing_config.reasoning_effort or "off",
            ),
        )
    return create_llm(review_config), create_llm(indexing_config)
