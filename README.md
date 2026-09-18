# Fabel 5 / Trade Assistant

A systematic trading research platform built for radical transparency. 

Instead of hiding flawed results to sell a strategy, this platform is designed to rigorously test published trading strategies and argue with its own conclusions. It actively flags stale data, mixed data, and overfitted numbers to provide a brutally honest assessment of trading models.

## Key Features
- **Rigorous Backtesting:** Tests 12 published strategies across 528 instruments.
- **Radical Transparency:** Caught 8 material bugs in standard trading models and proved that a standard buy-and-hold approach outperformed the absolute return of the tested strategies (though the strategies achieved ~60% less maximum drawdown).
- **Data Integrity:** Logs every price with its data feed and trading session to catch stale numbers immediately.

## Repository Structure

- `trade-assistant/`: The core backtesting engine, strategy definitions, and configuration files (`config.yaml`). Includes scripts for isolated backtesting and environment patching.
- `investor-pack/`: Contains the pitch deck (`Trade-Assistant-Investor-Deck.pptx`), full backtest results, email outreach templates, and scripts to regenerate the deck (`build_deck.js`, `write_slide_notes.py`).
- `antigravity results and data/`: Comprehensive CSV performance reports and isolated strategy data.
- `Fundraising_Plan.pdf`: Seed and Angel investor contact lists, network strategies, and outreach templates for raising a £2k-£10k to £1M seed round.

## Setup & Execution

The project relies on Python. A virtual environment (`venv`) is included in the workspace.

```bash
# Activate the virtual environment
source venv/bin/activate

# Run the backtests
cd trade-assistant
python run_csv_backtest.py
```

*Disclaimer: Research and decision-support software. Nothing in this repository constitutes financial advice, nor is it an offer or solicitation to buy or sell any security. Past behavior is not predictive.*
