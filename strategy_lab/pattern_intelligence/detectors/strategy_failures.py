from __future__ import annotations

from decimal import Decimal

from ..models import DetectorInput, Finding, FindingGroup
from .common import field, finding

DETECTOR_VERSION = "1.0.0"


def detect(data: DetectorInput) -> list[Finding]:
    results: list[Finding] = []
    events = [event for event in data.context.replay_events if data.selected_start <= int(field(event, "candle_index", -1)) <= data.selected_end]
    trades = [trade for trade in data.context.trades if data.selected_start <= int(field(trade, "exit_candle_index", -1)) <= data.selected_end]
    selected_trade = data.context.selected_trade
    if selected_trade is not None:
        trades = [selected_trade]
    for trade in trades:
        entry = int(field(trade, "entry_candle_index"))
        exit_index = int(field(trade, "exit_candle_index"))
        entry_price = Decimal(str(field(trade, "raw_fill_price", field(trade, "effective_entry_price"))))
        exit_price = Decimal(str(field(trade, "raw_exit_price", field(trade, "effective_exit_price"))))
        pre_start = max(data.selected_start, entry - data.config.structure_window)
        move_low = min(item.low for item in data.candles[pre_start:entry + 1])
        move_high = max(item.high for item in data.candles[pre_start:entry + 1])
        move = move_high - move_low
        consumed = Decimal("0") if move == 0 else (entry_price - move_low) / move
        if consumed >= Decimal("0.70"):
            results.append(finding(data, "late_entry_v1", "Late Entry", FindingGroup.STRATEGY_BEHAVIOR, pre_start, entry,
                {"move_consumed_before_entry": consumed, "entry_price": entry_price, "prior_move_low": move_low, "prior_move_high": move_high},
                {"minimum_move_consumed": Decimal("0.70")}, ("entry occurred after at least 70% of the measured prior upswing",)))
        post_end = min(data.selected_end, exit_index + data.config.medium_momentum_window)
        post_high = max(item.high for item in data.candles[exit_index:post_end + 1])
        left = post_high / exit_price - 1
        if left >= Decimal("0.02"):
            results.append(finding(data, "early_exit_v1", "Early Exit", FindingGroup.STRATEGY_BEHAVIOR, exit_index, post_end,
                {"post_exit_favorable_move": left, "exit_price": exit_price, "post_exit_high": post_high}, {"minimum_post_exit_move": Decimal("0.02")},
                ("price advanced at least 2% within the configured post-exit window",)))
        trade_events = [event for event in events if entry <= int(field(event, "candle_index", -1)) <= exit_index]
        activated = any(field(event, "kind") == "profit_activation" for event in trade_events)
        if not activated:
            results.append(finding(data, "profit_mode_never_activated_v1", "Profit Mode Never Activated", FindingGroup.STRATEGY_BEHAVIOR, entry, exit_index,
                {"profit_activation_events": 0}, {"required_activation_events": 1}, ("no profit_activation replay event occurred during the trade",)))
        highest = Decimal(str(field(trade, "highest_price_during_trade", entry_price)))
        unrealized = highest / entry_price - 1
        realized = exit_price / entry_price - 1
        if unrealized - realized >= Decimal("0.02"):
            results.append(finding(data, "unrealized_profit_left_on_table_v1", "Unrealized Profit Left on Table", FindingGroup.STRATEGY_BEHAVIOR, entry, exit_index,
                {"maximum_unrealized_return": unrealized, "realized_price_return": realized, "difference": unrealized - realized},
                {"minimum_difference": Decimal("0.02")}, ("maximum unrealized return exceeded realized price return by at least 2%",)))
        if str(field(trade, "exit_reason", "")) in ("declining_closes", "ExitReason.DECLINING_CLOSES") and unrealized < Decimal("0.01"):
            results.append(finding(data, "declining_close_exit_before_meaningful_profit_v1", "Declining-Close Exit Before Meaningful Profit", FindingGroup.STRATEGY_BEHAVIOR, entry, exit_index,
                {"maximum_unrealized_return": unrealized}, {"meaningful_profit_pct": Decimal("0.01")},
                ("declining-close exit occurred before the trade reached 1% unrealized return",)))
        adverse = Decimal(str(field(trade, "lowest_price_during_trade", entry_price))) / entry_price - 1
        if adverse <= Decimal("-0.01") and realized < 0:
            results.append(finding(data, "stop_too_close_v1", "Stop Too Close", FindingGroup.STRATEGY_BEHAVIOR, entry, exit_index,
                {"maximum_adverse_excursion": adverse, "realized_price_return": realized}, {"reference_stop_pct": Decimal("0.01")},
                ("losing trade reached the configured 1% adverse reference",)))
        if adverse <= Decimal("-0.03"):
            results.append(finding(data, "stop_too_far_v1", "Stop Too Far", FindingGroup.STRATEGY_BEHAVIOR, entry, exit_index,
                {"maximum_adverse_excursion": adverse}, {"maximum_reference_adverse_excursion": Decimal("-0.03")},
                ("trade adverse excursion reached at least 3%",)))

    buy_limits = [event for event in events if field(event, "kind") == "buy_limit"]
    if len(buy_limits) >= 3:
        results.append(finding(data, "repeated_buy_limit_replacement_v1", "Repeated BUY-Limit Replacement", FindingGroup.STRATEGY_BEHAVIOR,
            int(field(buy_limits[0], "candle_index")), int(field(buy_limits[-1], "candle_index")), {"replacement_count": len(buy_limits) - 1},
            {"minimum_replacements": 2}, ("at least three BUY-limit placements occurred without an intervening selected fill",)))
    for event in events:
        if field(event, "kind") != "cancelled_order":
            continue
        index = int(field(event, "candle_index"))
        limit = Decimal(str(field(event, "price")))
        miss = (data.candles[index].low - limit) / limit
        if Decimal("0") < miss <= data.config.narrow_limit_miss_pct:
            results.append(finding(data, "narrowly_missed_buy_limit_v1", "Narrowly Missed BUY Limit", FindingGroup.STRATEGY_BEHAVIOR, index, index,
                {"limit_price": limit, "candle_low": data.candles[index].low, "miss_distance_pct": miss}, {"maximum_miss_distance_pct": data.config.narrow_limit_miss_pct},
                (f"candle low remained no more than {data.config.narrow_limit_miss_pct * 100}% above the resting BUY limit",)))
        follow_end = min(data.selected_end, index + data.config.missed_entry_horizon)
        following_move = max(item.high for item in data.candles[index:follow_end + 1]) / limit - 1
        if Decimal("0") < miss <= data.config.missed_entry_max_distance_pct and following_move >= data.config.missed_entry_follow_through_pct:
            results.append(finding(data, "missed_entry_v1", "Missed Entry", FindingGroup.STRATEGY_BEHAVIOR, index, follow_end,
                {"limit_price": limit, "candle_low": data.candles[index].low, "miss_distance_pct": miss, "following_favorable_move": following_move},
                {"maximum_miss_distance_pct": data.config.missed_entry_max_distance_pct, "minimum_following_move": data.config.missed_entry_follow_through_pct,
                 "forward_horizon": data.config.missed_entry_horizon},
                ("resting BUY limit was not touched", "miss distance was within the configured entry-offset reference", "price advanced at least 2% within eight candles")))

    curve = [Decimal(str(item)) for item in data.context.equity_curve]
    if curve:
        baseline = curve[0]
        below = next((index for index in range(data.selected_start, min(data.selected_end + 1, len(curve))) if curve[index] < baseline), None)
        if below is not None:
            results.append(finding(data, "capital_below_starting_value_v1", "Capital Falls Below Starting Value", FindingGroup.STRATEGY_BEHAVIOR, below, below,
                {"starting_value": baseline, "capital_value": curve[below], "drawdown_pct": curve[below] / baseline - 1}, {"starting_value": baseline},
                ("capital value < starting value",)))
            recovered = next((index for index in range(below + 1, min(data.selected_end + 1, len(curve))) if curve[index] >= baseline), None)
            if recovered is not None:
                results.append(finding(data, "capital_recovery_v1", "Capital Recovers Above Starting Value", FindingGroup.STRATEGY_BEHAVIOR, below, recovered,
                    {"starting_value": baseline, "recovered_value": curve[recovered], "recovery_candles": recovered - below}, {"starting_value": baseline},
                    ("capital returned to or above starting value after a drawdown",)))
    if data.context.strategy_return_pct is not None and data.context.buy_hold_return_pct is not None and data.context.strategy_return_pct < data.context.buy_hold_return_pct:
        results.append(finding(data, "strategy_underperforms_buy_hold_v1", "Strategy Underperforms Buy & Hold", FindingGroup.STRATEGY_BEHAVIOR, data.selected_start, data.selected_end,
            {"strategy_return_pct": data.context.strategy_return_pct, "buy_hold_return_pct": data.context.buy_hold_return_pct,
             "underperformance_pct_points": data.context.buy_hold_return_pct - data.context.strategy_return_pct}, {"minimum_outperformance": Decimal("0")},
            ("strategy selected-range return < buy-and-hold selected-range return",)))
    return results