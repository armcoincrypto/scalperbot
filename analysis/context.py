"""
MODULE 7: CONTEXT ANALYZER
===========================
Adds the MISSING LAYER to displacement analysis.

Instead of: "Do displacements continue or reverse?"
We ask: "UNDER WHICH CONDITIONS do displacements reverse?"

Context Dimensions:
1. LOCATION - Where did displacement happen (extreme vs mid-range)
2. SPEED - Velocity of the move (fast stop-run vs slow acceptance)
3. FOLLOW-THROUGH - Did price fail to continue after displacement
4. LIQUIDITY - Was there a liquidity sweep before/with displacement

This is where real edges hide.
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class ContextAnalyzer:
    """
    Adds context to displacement events.

    Markets move for only 3 real reasons:
    1. Liquidity grab
    2. Positioning unwind
    3. New information (news / session open)

    This module captures the CONDITIONS around each displacement.
    """

    def __init__(self):
        # Session times (UTC)
        self.sessions = {
            'asian': (0, 8),      # 00:00 - 08:00 UTC
            'london': (8, 16),    # 08:00 - 16:00 UTC
            'newyork': (13, 21),  # 13:00 - 21:00 UTC
        }

        # Follow-through detection parameters
        self.followthrough_minutes = 15  # Time to wait for continuation
        self.compression_threshold = 0.5  # Reduced volatility = compression

    def get_location_context(self, df: pd.DataFrame, displacement_idx: int,
                              lookback_hours: int = 24) -> Dict:
        """
        Determine WHERE the displacement occurred relative to key levels.

        80% of reversals happen near extremes, not mid-range.

        Returns:
            {
                'location': 'EXTREME_HIGH' | 'EXTREME_LOW' | 'MID_RANGE',
                'distance_from_high_pct': float,
                'distance_from_low_pct': float,
                'near_session_extreme': bool,
                'session': 'asian' | 'london' | 'newyork'
            }
        """
        if displacement_idx >= len(df):
            return {'location': 'UNKNOWN'}

        current_row = df.iloc[displacement_idx]
        current_price = current_row['close']
        current_time = df.index[displacement_idx]

        # Get lookback data
        lookback_start = current_time - timedelta(hours=lookback_hours)
        lookback_data = df[df.index >= lookback_start]

        if len(lookback_data) < 10:
            return {'location': 'UNKNOWN', 'reason': 'insufficient_data'}

        # Previous day/session high-low
        period_high = lookback_data['high'].max()
        period_low = lookback_data['low'].min()
        period_range = period_high - period_low

        if period_range == 0:
            return {'location': 'UNKNOWN', 'reason': 'no_range'}

        # Position within range (0 = low, 1 = high)
        position_in_range = (current_price - period_low) / period_range

        # Distance from extremes (percentage)
        dist_from_high_pct = ((period_high - current_price) / period_high) * 100
        dist_from_low_pct = ((current_price - period_low) / period_low) * 100

        # Classify location
        if position_in_range >= 0.85:
            location = 'EXTREME_HIGH'
        elif position_in_range <= 0.15:
            location = 'EXTREME_LOW'
        else:
            location = 'MID_RANGE'

        # Determine current session
        hour = current_time.hour if hasattr(current_time, 'hour') else current_time.to_pydatetime().hour
        session = 'off_hours'
        for sess_name, (start, end) in self.sessions.items():
            if start <= hour < end:
                session = sess_name
                break

        # Check if near session extreme (within 0.2% of session high/low)
        # Get session data
        session_start = current_time.replace(hour=self.sessions.get(session, (0, 24))[0], minute=0, second=0)
        session_data = df[(df.index >= session_start) & (df.index <= current_time)]

        near_session_extreme = False
        if len(session_data) > 5:
            session_high = session_data['high'].max()
            session_low = session_data['low'].min()
            near_high = abs(current_price - session_high) / session_high < 0.002
            near_low = abs(current_price - session_low) / session_low < 0.002
            near_session_extreme = near_high or near_low

        return {
            'location': location,
            'position_in_range': round(position_in_range, 3),
            'distance_from_high_pct': round(dist_from_high_pct, 3),
            'distance_from_low_pct': round(dist_from_low_pct, 3),
            'near_session_extreme': near_session_extreme,
            'session': session,
            'period_high': period_high,
            'period_low': period_low
        }

    def get_velocity_context(self, df: pd.DataFrame, displacement_idx: int,
                             lookback_candles: int = 5) -> Dict:
        """
        Measure SPEED of the displacement.

        Fast moves = likely stop runs (liquidity grab)
        Slow moves = likely acceptance (real demand/supply)

        Returns:
            {
                'velocity': float (pct per minute),
                'speed_type': 'FAST_STOPRUN' | 'SLOW_ACCEPTANCE' | 'NORMAL',
                'candles_to_form': int,
                'avg_body_ratio': float
            }
        """
        if displacement_idx < lookback_candles:
            return {'speed_type': 'UNKNOWN'}

        current_row = df.iloc[displacement_idx]
        lookback_start = max(0, displacement_idx - lookback_candles)
        recent_data = df.iloc[lookback_start:displacement_idx + 1]

        if len(recent_data) < 2:
            return {'speed_type': 'UNKNOWN'}

        # Calculate move size and time
        start_price = recent_data.iloc[0]['open']
        end_price = current_row['close']
        move_pct = abs((end_price - start_price) / start_price) * 100

        # Time in minutes (assuming 1m candles)
        minutes = len(recent_data)

        # Velocity = % move per minute
        velocity = move_pct / minutes if minutes > 0 else 0

        # Average body ratio in the move
        bodies = abs(recent_data['close'] - recent_data['open'])
        ranges = recent_data['high'] - recent_data['low']
        avg_body_ratio = (bodies / ranges.replace(0, np.nan)).mean()

        # Classify speed
        # Fast: > 0.1% per minute with large bodies
        # Slow: < 0.03% per minute with small bodies
        if velocity > 0.08 and avg_body_ratio > 0.6:
            speed_type = 'FAST_STOPRUN'
        elif velocity < 0.03 and avg_body_ratio < 0.4:
            speed_type = 'SLOW_ACCEPTANCE'
        else:
            speed_type = 'NORMAL'

        return {
            'velocity': round(velocity, 4),
            'speed_type': speed_type,
            'candles_to_form': minutes,
            'move_pct': round(move_pct, 3),
            'avg_body_ratio': round(avg_body_ratio, 3) if not np.isnan(avg_body_ratio) else 0
        }

    def get_followthrough_context(self, df: pd.DataFrame, displacement_idx: int,
                                   direction: str, wait_minutes: int = 15) -> Dict:
        """
        Detect FOLLOW-THROUGH FAILURE.

        Instead of predicting reversals, wait for failure:
        - Big displacement
        - Price tries to continue
        - FAILS to make new high/low
        - Starts compressing

        This is much higher probability than immediate fade.

        Returns:
            {
                'followthrough': 'SUCCEEDED' | 'FAILED' | 'COMPRESSING' | 'PENDING',
                'new_extreme_made': bool,
                'compression_ratio': float,
                'failure_candles': int (how many candles before failure was clear)
            }
        """
        future_start = displacement_idx + 1
        future_end = min(displacement_idx + wait_minutes + 1, len(df))

        if future_end <= future_start:
            return {'followthrough': 'PENDING', 'reason': 'no_future_data'}

        disp_row = df.iloc[displacement_idx]
        future_data = df.iloc[future_start:future_end]

        if len(future_data) < 5:
            return {'followthrough': 'PENDING', 'reason': 'insufficient_future_data'}

        # Get displacement extreme
        if direction == 'BULLISH':
            disp_extreme = disp_row['high']
            new_extreme_made = future_data['high'].max() > disp_extreme
            continuation_price = future_data['high'].max()
        else:
            disp_extreme = disp_row['low']
            new_extreme_made = future_data['low'].min() < disp_extreme
            continuation_price = future_data['low'].min()

        # Check for compression (reduced volatility)
        disp_range = disp_row['high'] - disp_row['low']
        future_ranges = future_data['high'] - future_data['low']
        avg_future_range = future_ranges.mean()
        compression_ratio = avg_future_range / disp_range if disp_range > 0 else 1

        # Determine follow-through status
        if new_extreme_made:
            # Price continued in direction
            followthrough = 'SUCCEEDED'
            failure_candles = 0
        elif compression_ratio < self.compression_threshold:
            # Price compressing after failed attempt
            followthrough = 'COMPRESSING'
            # Find when compression started
            failure_candles = 0
            for i, rng in enumerate(future_ranges):
                if rng < disp_range * self.compression_threshold:
                    failure_candles = i + 1
                    break
        else:
            # Failed to continue but not compressing yet
            followthrough = 'FAILED'
            failure_candles = len(future_data)

        return {
            'followthrough': followthrough,
            'new_extreme_made': new_extreme_made,
            'compression_ratio': round(compression_ratio, 3),
            'failure_candles': failure_candles,
            'disp_extreme': disp_extreme,
            'continuation_extreme': continuation_price
        }

    def check_liquidity_confluence(self, displacement_time: datetime,
                                    liquidity_events: List[Dict],
                                    window_minutes: int = 5) -> Dict:
        """
        Check if displacement occurred WITH a liquidity sweep.

        Displacement WITH sweep → likely reversal (stop run complete)
        Displacement WITHOUT sweep → different behavior (real move?)

        Returns:
            {
                'has_liquidity_sweep': bool,
                'sweep_type': 'EQUAL_HIGH' | 'EQUAL_LOW' | None,
                'sweep_before_disp': bool,
                'sweep_after_disp': bool,
                'time_to_sweep_minutes': int
            }
        """
        if not liquidity_events:
            return {'has_liquidity_sweep': False}

        # Convert displacement time if needed
        if isinstance(displacement_time, str):
            if displacement_time.isdigit():
                displacement_time = pd.to_datetime(int(displacement_time), unit='ms', utc=True)
            else:
                displacement_time = pd.to_datetime(displacement_time, utc=True)
        elif isinstance(displacement_time, (int, float)):
            displacement_time = pd.to_datetime(int(displacement_time), unit='ms', utc=True)

        window = timedelta(minutes=window_minutes)

        # Find nearby liquidity events
        nearby_sweeps = []
        for event in liquidity_events:
            event_time = event.get('timestamp')
            if not event_time:
                continue

            # Parse event time
            if isinstance(event_time, str):
                if event_time.isdigit():
                    event_time = pd.to_datetime(int(event_time), unit='ms', utc=True)
                else:
                    event_time = pd.to_datetime(event_time, utc=True)
            elif isinstance(event_time, (int, float)):
                event_time = pd.to_datetime(int(event_time), unit='ms', utc=True)
            else:
                continue

            time_diff = event_time - displacement_time
            if abs(time_diff) <= window:
                nearby_sweeps.append({
                    'event': event,
                    'time_diff_minutes': time_diff.total_seconds() / 60,
                    'before': time_diff.total_seconds() < 0
                })

        if not nearby_sweeps:
            return {'has_liquidity_sweep': False}

        # Get closest sweep
        closest = min(nearby_sweeps, key=lambda x: abs(x['time_diff_minutes']))

        return {
            'has_liquidity_sweep': True,
            'sweep_type': closest['event'].get('sweep_type', 'UNKNOWN'),
            'sweep_before_disp': closest['before'],
            'sweep_after_disp': not closest['before'],
            'time_to_sweep_minutes': round(closest['time_diff_minutes'], 1),
            'num_nearby_sweeps': len(nearby_sweeps)
        }

    def analyze_displacement_context(self, df: pd.DataFrame, displacement: Dict,
                                      liquidity_events: List[Dict] = None) -> Dict:
        """
        Full context analysis for a single displacement.

        Combines all context dimensions into one analysis.
        """
        # Find displacement index in dataframe
        disp_time = displacement.get('timestamp')
        if isinstance(disp_time, str) and disp_time.isdigit():
            disp_time = pd.to_datetime(int(disp_time), unit='ms', utc=True)
        elif isinstance(disp_time, (int, float)):
            disp_time = pd.to_datetime(int(disp_time), unit='ms', utc=True)
        else:
            disp_time = pd.to_datetime(disp_time, utc=True)

        # Find closest index
        time_diffs = abs(df.index - disp_time)
        disp_idx = time_diffs.argmin()

        direction = displacement.get('direction', 'BULLISH')

        # Get all context dimensions
        location = self.get_location_context(df, disp_idx)
        velocity = self.get_velocity_context(df, disp_idx)
        followthrough = self.get_followthrough_context(df, disp_idx, direction)
        liquidity = self.check_liquidity_confluence(
            disp_time,
            liquidity_events or []
        )

        # Combine into full context
        context = {
            'displacement_id': displacement.get('id'),
            'symbol': displacement.get('symbol'),
            'timestamp': str(disp_time),
            'direction': direction,
            'displacement_pct': displacement.get('displacement_pct'),
            'location': location,
            'velocity': velocity,
            'followthrough': followthrough,
            'liquidity': liquidity
        }

        # Add composite signals
        context['signals'] = self._generate_signals(location, velocity, followthrough, liquidity)

        return context

    def _generate_signals(self, location: Dict, velocity: Dict,
                          followthrough: Dict, liquidity: Dict) -> Dict:
        """
        Generate trading signals based on context combination.
        """
        signals = {
            'fade_signal': False,
            'continuation_signal': False,
            'wait_signal': False,
            'signal_strength': 0,
            'reasons': []
        }

        # FADE SIGNALS (trade against displacement)
        fade_reasons = []

        # Location at extreme + fast move = likely stop run
        if location.get('location') in ['EXTREME_HIGH', 'EXTREME_LOW']:
            if velocity.get('speed_type') == 'FAST_STOPRUN':
                fade_reasons.append('EXTREME_LOCATION + FAST_STOPRUN')
                signals['signal_strength'] += 2

        # Liquidity sweep + displacement = stop run complete
        if liquidity.get('has_liquidity_sweep') and liquidity.get('sweep_before_disp'):
            fade_reasons.append('LIQUIDITY_SWEEP_BEFORE')
            signals['signal_strength'] += 2

        # Follow-through failure = reversal confirmed
        if followthrough.get('followthrough') in ['FAILED', 'COMPRESSING']:
            fade_reasons.append(f"FOLLOWTHROUGH_{followthrough.get('followthrough')}")
            signals['signal_strength'] += 1

        # Near session extreme
        if location.get('near_session_extreme'):
            fade_reasons.append('NEAR_SESSION_EXTREME')
            signals['signal_strength'] += 1

        if fade_reasons:
            signals['fade_signal'] = True
            signals['reasons'] = fade_reasons

        # CONTINUATION SIGNALS (trade with displacement)
        cont_reasons = []

        # Mid-range + slow move = real acceptance
        if location.get('location') == 'MID_RANGE':
            if velocity.get('speed_type') == 'SLOW_ACCEPTANCE':
                cont_reasons.append('MID_RANGE + SLOW_ACCEPTANCE')
                signals['signal_strength'] -= 1  # Reduce fade signal

        # Follow-through succeeded
        if followthrough.get('followthrough') == 'SUCCEEDED':
            cont_reasons.append('FOLLOWTHROUGH_SUCCEEDED')
            signals['signal_strength'] -= 2

        # No liquidity sweep = might be real move
        if not liquidity.get('has_liquidity_sweep'):
            if velocity.get('speed_type') != 'FAST_STOPRUN':
                cont_reasons.append('NO_SWEEP + NOT_STOPRUN')
                signals['signal_strength'] -= 1

        if cont_reasons and not fade_reasons:
            signals['continuation_signal'] = True
            signals['reasons'] = cont_reasons

        # WAIT SIGNAL (unclear)
        if not signals['fade_signal'] and not signals['continuation_signal']:
            signals['wait_signal'] = True
            signals['reasons'] = ['CONTEXT_UNCLEAR']

        return signals


# Test if run directly
if __name__ == "__main__":
    print("Context Analyzer module loaded")
    print("Use analyze_displacement_context() to add context to displacements")
