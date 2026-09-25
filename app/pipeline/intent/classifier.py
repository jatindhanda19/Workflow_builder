import logging

from app.pipeline.intent.rules import classify_by_rules, fill_gaps
from app.pipeline.intent.schema import IntentResult
from app.core.llm import LLMError, StructuredLLM

logger = logging.getLogger(__name__)


def classify_intent(llm: StructuredLLM, message: str) -> IntentResult:
    """LLM classification; keyword rules fill only what the LLM left open, or everything if it failed."""
    rules = classify_by_rules(message)
    try:
        result = llm.generate(IntentResult, "intent", f"USER REQUEST:\n{message}")
    except LLMError:
        logger.warning("intent classification fell back to keyword rules")
        return rules
    return fill_gaps(result, rules)
