"""Mean Reversion Signal Generator for Striker Module.

Generates SHORT and LONG signals based on fear/greed spikes,
candlestick patterns, and volume anomalies.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from .news_monitor import NewsMonitor
from .trade_executor import TradeExecutor
from ..hydra import Hydra
from ..utils.db import get_session
from ..utils.logging_config import get_logger

logger = get_logger("striker.signal")


class MeanReversionSignalGenerator:
    """Generates mean-reversion trading signals based on market conditions."""

    def __init__(
        self,
        config: Dict = None,
        hydra: Hydra = None,
    ):
        """Initialize the signal generator.

        Args:
            config: Configuration dict with trading parameters. Supports:
                - Backtest section (from settings.yaml backtest:):
                  fear_spike_threshold, volume_multiplier,
                  min_candle_size_pct, require_wick_rejection
                - Legacy keys (spike_increase, min_candle_move_pct, etc.)
                  for backward compatibility.
            hydra: Hydra instance for capital pool management.

        Environment variable overrides (take priority):
            BACKTEST_FEAR_SPIKE_THRESHOLD, BACKTEST_VOLUME_MULTIPLIER,
            BACKTEST_MIN_CANDLE_SIZE_PCT, BACKTEST_REQUIRE_WICK_REJECTION
        """
        self.config = config or {}
        self.hydra = hydra

        # Load backtest section from config (settings.yaml backtest: key)
        bt_config = self.config.get("backtest", {})

        # Parse booleans flexibly (YAML true, env strings, etc.)
        def _parse_bool(val):
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes", "on")
            return bool(val)

        def _parse_env_float(key, default):
            env_val = os.environ.get(key)
            if env_val is not None:
                return float(env_val)
            return default

        def _parse_env_int(key, default):
            env_val = os.environ.get(key)
            if env_val is not None:
                return int(env_val)
            return default

        def _parse_env_bool(key, default):
            env_val = os.environ.get(key)
            if env_val is not None:
                return _parse_bool(env_val)
            return default

        # Map backtest config keys to attributes, with env var overrides,
        # then fall back to legacy config keys, then defaults.

        # Required fear spike threshold for SHORT signals
        self.required_spike_increase = _parse_env_float(
            "BACKTEST_FEAR_SPIKE_THRESHOLD",
            bt_config.get("fear_spike_threshold",
                          self.config.get("spike_increase", 30))
        )

        # Min volume multiplier
        self.min_volume_multiplier = _parse_env_float(
            "BACKTEST_VOLUME_MULTIPLIER",
            bt_config.get("volume_multiplier",
                          self.config.get("min_volume_multiplier", 2.0))
        )

        # Min candle move percentage
        self.min_candle_move_pct = _parse_env_float(
            "BACKTEST_MIN_CANDLE_SIZE_PCT",
            bt_config.get("min_candle_size_pct",
                          self.config.get("min_candle_move_pct", 6.0))
        )

        # Require wick rejection pattern
        self.require_wick_rejection = _parse_env_bool(
            "BACKTEST_REQUIRE_WICK_REJECTION",
            bt_config.get("require_wick_rejection", False)
        )

        # Trading pair configuration
        self.pair = self.config.get("trading_pair", "SOL/USDT")
        self.ohlcv_timeframe = self.config.get("ohlcv_timeframe", "15m")
        self.max_concurrent = self.config.get("max_concurrent_trades", 3)

        # Candle pattern thresholds (not overridden by backtest config)
        self.min_wick_ratio = self.config.get("min_wick_ratio", 0.3)

        # News monitor
        self.news_monitor = NewsMonitor(self.config.get("news", {}))

        # Exchange price source
        self.exchange_config = self.config.get("exchange", {})

        # Signal history for cooldown management
        self.last_signal: Optional[Dict] = None
        self.last_signal_time: Optional[datetime] = None
        self.signal_cooldown_minutes = self.config.get("signal_cooldown_minutes", 30)

    def get_current_price(self) -> float:
        """Get the current market price for the configured pair.

        Returns:
            Current market price, or 0 on failure.
        """
        from .trade_executor import TradeExecutor
        executor = TradeExecutor(self.exchange_config)
        return executor.get_market_price(self.pair)

    def fetch_ohlcv(self, limit: int = 100) -> List[List]:
        """Fetch OHLCV data for the configured pair.

        Args:
            limit: Number of candles to fetch.

        Returns:
            List of [timestamp, open, high, low, close, volume] arrays.
        """
        from .trade_executor import TradeExecutor
        executor = TradeExecutor(self.exchange_config)
        return executor.fetch_ohlcv(self.pair, self.ohlcv_timeframe, limit)

    def compute_volume_average(self, ohlcv: List[List], periods: int = 20) -> float:
        """Compute average volume over the last N periods.

        Args:
            ohlcv: OHLCV data.
            periods: Number of periods for average.

        Returns:
            Average volume.
        """
        if len(ohlcv) < periods:
            periods = len(ohlcv)

        volumes = [candle[5] for candle in ohlcv[:periods]]
        return sum(volumes) / len(volumes) if volumes else 0

    def detect_candle_pattern(
        self, current_candle: List
    ) -> Optional[str]:
        """Detect candlestick pattern for the current candle.

        Args:
            current_candle: [timestamp, open, high, low, close, volume]

        Returns:
            Pattern name or None.
        """
        _, open_price, high, low, close, volume = current_candle

        if open_price <= 0 or close <= 0 or high <= 0 or low <= 0:
            return None

        body = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        total_range = high - low

        if total_range == 0:
            return None

        upper_wick_ratio = upper_wick / total_range
        lower_wick_ratio = lower_wick / total_range

        # Doji: very small body relative to range
        if body / total_range < 0.1:
            return "DOJI"

        # Shooting star (bearish reversal): long upper wick, small body near low
        if upper_wick_ratio >= self.min_wick_ratio and lower_wick_ratio < 0.1:
            return "SHOOTING_STAR"

        # Hammer (bullish reversal): long lower wick, small body near high
        if lower_wick_ratio >= self.min_wick_ratio and upper_wick_ratio < 0.1:
            return "HAMMER"

        # Inverted hammer: long upper wick, body near low
        if upper_wick_ratio >= self.min_wick_ratio and body / total_range < 0.3:
            return "INVERTED_HAMMER"

        return None

    def check_volume_anomaly(
        self, current_candle: List, ohlcv: List[List]
    ) -> bool:
        """Check if current volume is anomalous compared to average.

        Args:
            current_candle: Current candle data.
            ohlcv: Historical OHLCV data.

        Returns:
            True if volume anomaly is detected.
        """
        current_volume = current_candle[5]
        avg_volume = self.compute_volume_average(ohlcv)

        if avg_volume <= 0:
            return False

        volume_ratio = current_volume / avg_volume
        return volume_ratio >= self.min_volume_multiplier

    def check_price_spike(self, current_candle: List) -> bool:
        """Check if the current candle shows a significant price spike.

        Args:
            current_candle: Current candle data.

        Returns:
            True if price moved significantly in one candle.
        """
        _, open_price, high, low, close, _ = current_candle

        if open_price <= 0:
            return False

        price_change_pct = abs(close - open_price) / open_price * 100
        return price_change_pct >= self.min_candle_move_pct

    def generate_signal(
        self, fear_score: float, spike_event: Optional[str] = None
    ) -> Optional[Dict]:
        """Generate a trading signal based on market conditions.

        Args:
            fear_score: Current fear score (0-100).
            spike_event: Detected spike event (FEAR_SPIKE or GREED_SPIKE).

        Returns:
            Signal dict with details, or None if no signal.
        """
        # Check cooldown
        if self.last_signal_time:
            elapsed = (datetime.utcnow() - self.last_signal_time).total_seconds() / 60
            if elapsed < self.signal_cooldown_minutes:
                logger.info(
                    "Signal cooldown active (%.1f min remaining). Skipping.",
                    self.signal_cooldown_minutes - elapsed,
                )
                return None

        # Check max concurrent trades via Hydra
        if self.hydra:
            current_trades = self._get_open_trade_count()
            if current_trades >= self.max_concurrent:
                logger.info(
                    "Max concurrent trades reached (%d/%d). Skipping.",
                    current_trades, self.max_concurrent,
                )
                return None

        if spike_event == "FEAR_SPIKE" and fear_score > 70:
            return self._generate_short_signal(fear_score)
        elif spike_event == "GREED_SPIKE" and fear_score < 30:
            return self._generate_long_signal(fear_score)

        # Fallback: direct fear/greed check without spike
        if fear_score > 80:
            return self._generate_short_signal(fear_score)
        elif fear_score < 20:
            return self._generate_long_signal(fear_score)

        logger.info("No trade signal: fear=%.2f, spike=%s", fear_score, spike_event)
        return None

    def _generate_short_signal(
        self, fear_score: float
    ) -> Optional[Dict]:
        """Generate a SHORT signal.

        Args:
            fear_score: Current fear score.

        Returns:
            Signal dict for SHORT trade.
        """
        ohlcv = self.fetch_ohlcv(limit=50)
        if not ohlcv or len(ohlcv) < 20:
            logger.warning("Insufficient OHLCV data for SHORT signal.")
            return None

        current_candle = ohlcv[0]  # Most recent candle
        price = current_candle[4]  # Close price

        if price <= 0:
            return None

        # Check for wick rejection pattern
        pattern = self.detect_candle_pattern(current_candle)
        volume_anomaly = self.check_volume_anomaly(current_candle, ohlcv)
        price_spike = self.check_price_spike(current_candle)

        # Wick rejection check: require long upper wick (rejection of highs)
        if self.require_wick_rejection:
            upper_wick = current_candle[2] - max(
                current_candle[1], current_candle[4]
            )
            body = abs(current_candle[4] - current_candle[1])
            candle_range = current_candle[2] - current_candle[3]
            if candle_range > 0:
                upper_wick_ratio = upper_wick / candle_range
            else:
                upper_wick_ratio = 0
            if upper_wick_ratio < self.min_wick_ratio:
                logger.info(
                    "SHORT: Wick rejection not met (upper_wick_ratio=%.3f < %.1f). Skipping.",
                    upper_wick_ratio, self.min_wick_ratio,
                )
                return None

        # Require at least a wick pattern OR volume anomaly
        if pattern is None and not volume_anomaly and not price_spike:
            logger.info(
                "SHORT: No confirming pattern (wick=%s, vol=%s, spike=%s)",
                pattern, volume_anomaly, price_spike,
            )
            return None

        # Calculate stop loss and take profit
        high = current_candle[2]
        stop_loss = high * 1.01  # 1% above high
        take_profit = price * 0.98  # 2% below entry (2:1 risk/reward)

        signal = {
            "signal_type": "SHORT",
            "pair": self.pair,
            "entry_price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": min(95.0, fear_score),
            "trigger": f"FEAR_SPIKE (fear={fear_score:.1f})",
            "fear_score": fear_score,
            "candle_pattern": pattern,
            "volume_anomaly": volume_anomaly,
            "volume_ratio": round(
                current_candle[5] / self.compute_volume_average(ohlcv), 2
            ) if self.compute_volume_average(ohlcv) > 0 else 0,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Analyst Bureau enrichment (fail-open: passes through on error)
        if os.environ.get("ANALYST_BUREAU_ENABLED", "false").lower() == "true":
            try:
                from ..analyst_bureau.signal_enrichment import enrich_signal
                from ..analyst_bureau.bureau_orchestrator import BureauOrchestrator
                bureau = BureauOrchestrator()
                signal = enrich_signal(signal, bureau)
                logger.info("Signal enriched by Analyst Bureau")
            except Exception as e:
                logger.warning("Analyst Bureau enrichment failed: %s", e)

        self.last_signal = signal
        self.last_signal_time = datetime.utcnow()
        logger.info("SHORT signal: pair=%s price=%.4f confidence=%.1f",
                     self.pair, price, signal["confidence"])
        return signal

    def _generate_long_signal(
        self, fear_score: float
    ) -> Optional[Dict]:
        """Generate a LONG signal.

        Args:
            fear_score: Current fear score (low = greed).

        Returns:
            Signal dict for LONG trade.
        """
        ohlcv = self.fetch_ohlcv(limit=50)
        if not ohlcv or len(ohlcv) < 20:
            logger.warning("Insufficient OHLCV data for LONG signal.")
            return None

        current_candle = ohlcv[0]
        price = current_candle[4]

        if price <= 0:
            return None

        pattern = self.detect_candle_pattern(current_candle)
        volume_anomaly = self.check_volume_anomaly(current_candle, ohlcv)
        price_spike = self.check_price_spike(current_candle)

        # Wick rejection check: require long lower wick (rejection of lows)
        if self.require_wick_rejection:
            lower_wick = min(
                current_candle[1], current_candle[4]
            ) - current_candle[3]
            candle_range = current_candle[2] - current_candle[3]
            if candle_range > 0:
                lower_wick_ratio = lower_wick / candle_range
            else:
                lower_wick_ratio = 0
            if lower_wick_ratio < self.min_wick_ratio:
                logger.info(
                    "LONG: Wick rejection not met (lower_wick_ratio=%.3f < %.1f). Skipping.",
                    lower_wick_ratio, self.min_wick_ratio,
                )
                return None

        if pattern is None and not volume_anomaly and not price_spike:
            logger.info(
                "LONG: No confirming pattern (wick=%s, vol=%s, spike=%s)",
                pattern, volume_anomaly, price_spike,
            )
            return None

        low = current_candle[3]
        stop_loss = low * 0.99  # 1% below low
        take_profit = price * 1.02  # 2% above entry (2:1 risk/reward)

        signal = {
            "signal_type": "LONG",
            "pair": self.pair,
            "entry_price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": min(95.0, 100 - fear_score),
            "trigger": f"GREED_SPIKE (fear={fear_score:.1f})",
            "fear_score": fear_score,
            "candle_pattern": pattern,
            "volume_anomaly": volume_anomaly,
            "volume_ratio": round(
                current_candle[5] / self.compute_volume_average(ohlcv), 2
            ) if self.compute_volume_average(ohlcv) > 0 else 0,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Analyst Bureau enrichment (fail-open: passes through on error)
        if os.environ.get("ANALYST_BUREAU_ENABLED", "false").lower() == "true":
            try:
                from ..analyst_bureau.signal_enrichment import enrich_signal
                from ..analyst_bureau.bureau_orchestrator import BureauOrchestrator
                bureau = BureauOrchestrator()
                signal = enrich_signal(signal, bureau)
                logger.info("Signal enriched by Analyst Bureau")
            except Exception as e:
                logger.warning("Analyst Bureau enrichment failed: %s", e)

        self.last_signal = signal
        self.last_signal_time = datetime.utcnow()
        logger.info("LONG signal: pair=%s price=%.4f confidence=%.1f",
                     self.pair, price, signal["confidence"])
        return signal

    def _get_open_trade_count(self) -> int:
        """Count currently open trades in the database.

        Returns:
            Number of trades with status PENDING or ACTIVE.
        """
        from ..utils.db import Trade
        session = get_session()
        try:
            count = session.query(Trade).filter(
                Trade.status.in_(["PENDING", "ACTIVE"])
            ).count()
            return count
        finally:
            session.close()

    def get_extreme_fear_greed_index(self) -> float:
        """Fetch the Crypto Fear & Greed Index from alternative.me.

        Returns:
            Fear & Greed score (0-100).
        """
        try:
            response = httpx.get(
                "https://api.alternative.me/fng/?limit=1",
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return int(data["data"][0]["value"])
        except Exception as e:
            logger.error("Failed to fetch Fear & Greed Index: %s", e)
            return 50  # Default to neutral
