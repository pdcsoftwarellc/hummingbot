# Repository Guidance

When testing or evaluating a trading strategy in this repository, always print a concise stats block in the final report, regardless of the active chat or context.

Always show results for these three windows when the underlying backtest range supports them:

- YTD
- 1 Year
- Full

For each window, include these fields when available, using the same labels and comparable formatting:

- Starting Capital
- Backtest Timeframe
- Final Equity
- Total Return
- SPY Benchmark
- CAGR
- Win Rate
- Biggest Win per trade
- Biggest Loss per trade
- Average P&L per trade
- Avg Holding Time
- Max Drawdown
- Sharpe Ratio

If a metric is unavailable from the test output, explicitly mark it as `N/A` instead of omitting it.

When reporting multiple variants, clearly identify which result is the current baseline and which results are experimental sidecars. Do not silently promote an experimental module into the baseline just because it explains a recent move.

When leverage affects sizing, report the leverage assumption. Prefer confidence-scaled leverage in research tooling: the configured leverage should act as a cap reserved for high-confidence signals, not as an unconditional default.
