"""Learning Loop for Intelligence Module.

Implements the post-trade reflection and periodic skill file
generation for self-improvement. Runs after trade closures
and on a scheduled weekly basis.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional

from .llm_interface import LLMInterface
from ..utils.db import Trade, get_session, log_system_event
from ..utils.logging_config import get_logger

logger = get_logger("intelligence.learning")


class LearningLoop:
    """Manages the self-improvement learning loop."""

    def __init__(self, config: Dict = None):
        """Initialize the learning loop.

        Args:
            config: Configuration with paths and thresholds.
        """
        self.config = config or {}
        self.llm = LLMInterface(self.config.get("llm", {}))
        self.skills_dir = self.config.get("skills_dir", "config/skills")
        self.min_trades_for_analysis = self.config.get(
            "min_trades_for_analysis", 5
        )
        self.skill_files = {
            "striker": os.path.join(self.skills_dir, "striker_skill.txt"),
            "foundation": os.path.join(self.skills_dir, "foundation_skill.txt"),
            "sleuth": os.path.join(self.skills_dir, "sleuth_skill.txt"),
        }

    def post_trade_reflection(self, trade: Trade, outcome: Dict) -> None:
        """Record a post-trade reflection for analysis.

        Args:
            trade: The closed Trade record.
            outcome: Dict with PnL, duration, and outcome details.
        """
        session = get_session()
        try:
            reflection_data = {
                "trade_id": trade.id,
                "pair": trade.pair,
                "side": trade.side,
                "entry_price": trade.entry_price,
                "exit_price": outcome.get("exit_price", 0),
                "pnl": outcome.get("pnl", 0),
                "pnl_pct": outcome.get("pnl_pct", 0),
                "duration_seconds": outcome.get("duration_seconds", 0),
                "trigger_fear_score": trade.trigger_fear_score,
                "trigger_headline": trade.trigger_headline,
                "candle_pattern": trade.candle_pattern,
                "volume_anomaly": trade.volume_anomaly,
                "outcome": outcome.get("outcome", "UNKNOWN"),
                "timestamp": datetime.utcnow().isoformat(),
            }

            # Append to trade reflections file
            reflections_file = os.path.join(self.skills_dir, "trade_reflections.jsonl")
            os.makedirs(os.path.dirname(reflections_file), exist_ok=True)

            with open(reflections_file, "a") as f:
                f.write(json.dumps(reflection_data) + "\n")

            logger.info(
                "Trade reflection recorded: trade_id=%s pnl=%.2f%% outcome=%s",
                trade.id, outcome.get("pnl_pct", 0), outcome.get("outcome", "UNKNOWN"),
            )

        except Exception as e:
            logger.error("Failed to record trade reflection: %s", e)
        finally:
            session.close()

    def run_periodic_analysis(self) -> Dict:
        """Run periodic analysis of trade performance.

        Checks if enough trades have accumulated to warrant LLM analysis,
        generates skill updates, and writes new skill files.

        Returns:
            Dict with analysis results.
        """
        session = get_session()
        try:
            # Count closed trades since last analysis
            from ..utils.db import TradeReflection
            count = session.query(TradeReflection).count()
            if count < self.min_trades_for_analysis:
                logger.info(
                    "Not enough trades for analysis (%d/%d). Skipping.",
                    count, self.min_trades_for_analysis,
                )
                return {"status": "INSUFFICIENT_DATA", "trades_count": count}

            # Load recent reflections
            reflections = self._load_recent_reflections()

            # Analyze performance
            analysis = self._analyze_reflections(reflections)

            # Generate skill updates via LLM
            updates = {}
            for skill_name in ["striker", "foundation", "sleuth"]:
                skill_update = self._generate_skill_update_for_module(
                    skill_name, analysis
                )
                if skill_update:
                    updates[skill_name] = skill_update

            # Write updated skill files
            for skill_name, content in updates.items():
                self._write_skill_file(skill_name, content)

            # Clear processed reflections
            self._clear_processed_reflections()

            logger.info("Periodic analysis complete. Generated %d skill updates.", len(updates))

            return {
                "status": "COMPLETE",
                "trades_analyzed": len(reflections),
                "skill_updates_generated": len(updates),
                "updates": list(updates.keys()),
            }

        except Exception as e:
            logger.error("Failed to run periodic analysis: %s", e)
            return {"status": "ERROR", "error": str(e)}
        finally:
            session.close()

    def _load_recent_reflections(self, limit: int = 100) -> List[Dict]:
        """Load recent trade reflections from file.

        Args:
            limit: Maximum number of reflections to load.

        Returns:
            List of reflection dicts.
        """
        reflections_file = os.path.join(self.skills_dir, "trade_reflections.jsonl")
        reflections = []

        if not os.path.exists(reflections_file):
            return reflections

        with open(reflections_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        reflections.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping invalid reflection line")

        # Return most recent N reflections
        return reflections[-limit:]

    def _analyze_reflections(self, reflections: List[Dict]) -> Dict:
        """Analyze trade reflections for patterns.

        Args:
            reflections: List of reflection dicts.

        Returns:
            Analysis dict with summary and failure patterns.
        """
        if not reflections:
            return {"summary": "No reflections to analyze.", "failure_patterns": "None"}

        total = len(reflections)
        wins = [r for r in reflections if r.get("outcome") == "WIN"]
        losses = [r for r in reflections if r.get("outcome") == "LOSS"]
        breakeven = [r for r in reflections if r.get("outcome") == "BREAKEVEN"]

        total_pnl = sum(r.get("pnl", 0) for r in reflections)
        avg_pnl = total_pnl / total if total > 0 else 0
        win_rate = len(wins) / total if total > 0 else 0

        # Analyze failure patterns
        failure_patterns = self._identify_failure_patterns(reflections)

        analysis = {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "average_pnl": round(avg_pnl, 2),
            "failure_patterns": failure_patterns,
            "summary": (
                f"{total} trades analyzed: {len(wins)} wins, {len(losses)} losses, "
                f"win rate {win_rate:.1%}, total PnL ${total_pnl:.2f}"
            ),
        }

        logger.info("Trade analysis: %s", analysis["summary"])
        return analysis

    def _identify_failure_patterns(self, reflections: List[Dict]) -> str:
        """Identify common patterns among losing trades.

        Args:
            reflections: List of reflection dicts.

        Returns:
            String describing failure patterns.
        """
        losses = [r for r in reflections if r.get("outcome") == "LOSS"]

        if not losses:
            return "No losing trades identified."

        pattern_counts = {}
        for loss in losses:
            # Track patterns by trigger, candle, and volume
            trigger = loss.get("trigger_fear_score", "unknown")
            pattern = loss.get("candle_pattern", "unknown")
            has_vol = loss.get("volume_anomaly", False)

            key = f"fear_{trigger}_pattern_{pattern}_vol={has_vol}"
            pattern_counts[key] = pattern_counts.get(key, 0) + 1

        # Sort by frequency
        sorted_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)

        patterns_text = "\n".join(
            f"  - Count {count}: {key.replace('_', ' ')}"
            for key, count in sorted_patterns[:5]
        )

        return f"{len(losses)} losses with the following common patterns:\n{patterns_text}"

    def _generate_skill_update_for_module(
        self,
        skill_name: str,
        analysis: Dict,
    ) -> Optional[str]:
        """Generate an updated skill file for a specific module.

        Args:
            skill_name: Module name (striker, foundation, sleuth).
            analysis: Analysis dict from _analyze_reflections.

        Returns:
            Updated skill content, or None on failure.
        """
        current_skill = self._read_current_skill(skill_name)
        failure_patterns = analysis.get("failure_patterns", "None identified")

        skill_update = self.llm.generate_skill_update(
            skill_name=skill_name,
            current_skills=current_skill,
            trade_summary=analysis.get("summary", ""),
            failure_patterns=failure_patterns,
        )

        return skill_update

    def _read_current_skill(self, skill_name: str) -> str:
        """Read the current skill file content.

        Args:
            skill_name: Module name.

        Returns:
            Current skill file content, or placeholder if not found.
        """
        skill_path = self.skill_files.get(skill_name, "")
        if os.path.exists(skill_path):
            with open(skill_path, "r") as f:
                return f.read()
        return f"# {skill_name} skill file (not yet generated)\n"

    def _write_skill_file(self, skill_name: str, content: str) -> bool:
        """Write updated content to a skill file.

        Args:
            skill_name: Module name.
            content: New skill file content.

        Returns:
            True if written successfully.
        """
        skill_path = self.skill_files.get(skill_name, "")
        if not skill_path:
            return False

        os.makedirs(os.path.dirname(skill_path), exist_ok=True)

        with open(skill_path, "w") as f:
            f.write(content)

        logger.info("Wrote %s skill update (%d chars)", skill_name, len(content))
        return True

    def _clear_processed_reflections(self) -> None:
        """Clear processed reflections from the reflections file.

        Keeps only recent unprocessed reflections.
        """
        reflections_file = os.path.join(self.skills_dir, "trade_reflections.jsonl")
        if not os.path.exists(reflections_file):
            return

        # Keep only the last 50 reflections
        with open(reflections_file, "r") as f:
            lines = f.readlines()

        with open(reflections_file, "w") as f:
            f.writelines(lines[-50:])

        logger.info("Cleared processed reflections, keeping last 50")

    def generate_weekly_report(self) -> Optional[str]:
        """Generate a weekly performance report via LLM.

        Returns:
            Report text, or None on failure.
        """
        session = get_session()
        try:
            # Gather data
            trades = session.query(Trade).all()
            reflections = self._load_recent_reflections()
            analysis = self._analyze_reflections(reflections) if reflections else {}

            results = {
                "striker": {
                    "summary": analysis.get("summary", "No striker data"),
                },
                "foundation": {
                    "summary": "Foundation: Check dividend and politician trade logs",
                },
                "sleuth": {
                    "summary": "Sleuth: Check bounty submission and scanner logs",
                },
                "hydra": {
                    "summary": "Hydra: Check pool balances and allocations",
                },
            }

            return self.llm.analyze_weekly_results(results)

        except Exception as e:
            logger.error("Failed to generate weekly report: %s", e)
            return None
        finally:
            session.close()
