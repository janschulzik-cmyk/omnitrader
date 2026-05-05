"""LLM Interface for Intelligence Module.

Calls the Hermes Agent LLM model (via OpenRouter API) for
strategic analysis, skill file generation, and natural language
command processing.
"""

import os
from typing import Dict, List, Optional

import httpx

from ..utils.logging_config import get_logger

logger = get_logger("intelligence.llm")


class LLMInterface:
    """Interface for calling the LLM via OpenRouter API."""

    def __init__(self, config: Dict = None):
        """Initialize the LLM interface.

        Args:
            config: Configuration with model and API key settings.
        """
        self.config = config or {}
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.model = self.config.get("model", "qwen/qwen3-35b-a3b")
        self.base_url = self.config.get("base_url", "https://openrouter.ai/api/v1")
        self.max_tokens = self.config.get("max_tokens", 4096)
        self.temperature = self.config.get("temperature", 0.7)

    def call_llm(
        self,
        prompt: str,
        system_prompt: str = None,
        max_tokens: int = None,
        temperature: float = None,
        stop_sequences: List[str] = None,
    ) -> Optional[str]:
        """Call the LLM with a prompt and return the response.

        Args:
            prompt: The user prompt text.
            system_prompt: Optional system prompt for context.
            max_tokens: Override max tokens for this call.
            temperature: Override temperature for this call.
            stop_sequences: Optional stop sequences.

        Returns:
            LLM response text, or None on failure.
        """
        if not self.api_key:
            logger.error("LLM API key not configured.")
            return None

        url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Omnitrader",
            "X-Title": "Omnitrader",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }

        if stop_sequences:
            payload["stop"] = stop_sequences

        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=120.0)
            response.raise_for_status()
            data = response.json()

            choices = data.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                return message.get("content", "")

            logger.warning("LLM response had no choices.")
            return None

        except httpx.HTTPError as e:
            logger.error("LLM API call failed: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error calling LLM: %s", e)
            return None

    def generate_skill_update(
        self,
        skill_name: str,
        current_skills: str,
        trade_summary: str,
        failure_patterns: str,
    ) -> Optional[str]:
        """Generate an updated skill file content via LLM.

        Args:
            skill_name: Name of the skill (striker, foundation, sleuth).
            current_skills: Current skill file content.
            trade_summary: Summary of recent trades and their outcomes.
            failure_patterns: Identified failure patterns from analysis.

        Returns:
            Updated skill file content, or None on failure.
        """
        system_prompt = (
            "You are a strategic AI advisor for an autonomous trading and "
            "investigation system called Omnitrader. Your task is to generate "
            "improved instruction files (skills) based on recent performance data. "
            "Output ONLY the new skill file content — no explanations, no markdown "
            "wrappers, just the raw skill text."
        )

        prompt = f"""
SKILL UPDATE REQUEST FOR: {skill_name}

CURRENT SKILL FILE:
{current_skills}

RECENT TRADE/ACTIVITY SUMMARY:
{trade_summary}

IDENTIFIED FAILURE PATTERNS:
{failure_patterns}

TASK:
Analyze the performance data and failure patterns above. Generate an
UPDATED version of the skill file that addresses the identified issues
and improves future decision-making.

Include:
- Refined entry/exit criteria
- New risk management rules
- Additional confirmation checks
- Any strategy modifications

Output ONLY the updated skill file content.
"""

        response = self.call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=4096,
            temperature=0.5,  # Lower temperature for more consistent outputs
        )

        if response:
            logger.info("Generated %s skill update (%d chars)", skill_name, len(response))
        else:
            logger.warning("Failed to generate %s skill update", skill_name)

        return response

    def process_natural_language_command(
        self,
        command: str,
        context: Dict = None,
    ) -> Optional[Dict]:
        """Process a natural language command from the user.

        Args:
            command: The user's natural language command.
            context: Optional system state context.

        Returns:
            Dict with action, parameters, and metadata.
        """
        system_prompt = (
            "You are the command processor for Omnitrader. The user will "
            "give you natural language commands. Respond with a JSON object "
            "containing: action (string), params (dict), confidence (0-1). "
            "Supported actions: status, pause, resume, balance, trade, "
            "scan, report, rebalance, submit, fund, withdraw."
        )

        context_str = ""
        if context:
            context_str = f"\n\nSYSTEM CONTEXT:\n{context}"

        prompt = f"User command: \"{command}\"{context_str}\n\nRespond with JSON."

        response = self.call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.1,  # Very low for deterministic parsing
        )

        if response:
            return self._parse_command_response(response)

        return None

    def analyze_weekly_results(
        self,
        results: Dict,
    ) -> Optional[str]:
        """Analyze weekly system results and generate improvement recommendations.

        Args:
            results: Dict with weekly results data.

        Returns:
            Text recommendations, or None on failure.
        """
        system_prompt = (
            "You are the strategic brain of Omnitrader. You analyze weekly "
            "performance data and recommend specific modifications to the "
            "system's strategies. Output clear, actionable recommendations."
        )

        prompt = f"""
WEEKLY RESULTS SUMMARY:

Striker Module:
{results.get('striker', {}).get('summary', 'No data')}

Foundation Module:
{results.get('foundation', {}).get('summary', 'No data')}

Sleuth Module:
{results.get('sleuth', {}).get('summary', 'No data')}

Capital Allocation:
{results.get('hydra', {}).get('summary', 'No data')}

TASK:
Based on these results, what specific changes should we make to
improve overall performance? Provide concrete recommendations for
each module.
"""

        return self.call_llm(prompt=prompt, system_prompt=system_prompt)

    def _parse_command_response(self, response: str) -> Optional[Dict]:
        """Parse the LLM's command response into a structured dict.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed command dict, or None on failure.
        """
        import json

        # Try to extract JSON from the response
        try:
            # Find JSON object in response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                parsed = json.loads(json_str)
                return {
                    "action": parsed.get("action", "unknown"),
                    "params": parsed.get("params", {}),
                    "confidence": float(parsed.get("confidence", 0.5)),
                }
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Failed to parse command response: %s", e)

        # Fallback: manual parsing
        command_lower = response.lower()
        if "status" in command_lower:
            return {"action": "status", "params": {}, "confidence": 0.9}
        elif "pause" in command_lower:
            return {"action": "pause", "params": {"target": command_lower}, "confidence": 0.8}
        elif "resume" in command_lower:
            return {"action": "resume", "params": {"target": command_lower}, "confidence": 0.8}
        elif "balance" in command_lower:
            return {"action": "balance", "params": {}, "confidence": 0.9}

        return None
