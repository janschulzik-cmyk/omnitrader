"""Intelligence Module: LLM interface and self-improvement learning loop.

Uses the Hermes Agent model (via OpenRouter) to analyze outcomes
and generate skill file updates for continuous improvement.
"""

from .llm_interface import LLMInterface
from .learning_loop import LearningLoop

__all__ = ["LLMInterface", "LearningLoop"]
