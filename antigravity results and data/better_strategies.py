import yfinance as yf
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint
import os

RESULTS_DIR = "."

def fetch_data(tickers, period="10y", interval="1d"):
    print(f"Fetching {period} of {interval} data for {tickers}...")
    data = yf.download(tickers, period=period, interval=interval, progress=False)
    # yfinance returns MultiIndex if multiple tickers. We want the 'Close' prices.
    if isinstance(data.columns, pd.MultiIndex):
        closes = data['Close'].dropna()
    else:
        closes = data[['Close']].dropna()
    return closes

def pairs_trading_strategy(data, ticker1, ticker2):
    print(f"\n--- Statistical Arbitrage (Pairs Trading): {ticker1} vs {ticker2} ---")
    
    # 60% Train, 40% Test split
    split_idx = int(len(data) * 0.6)
    train = data.iloc[:split_idx]
    test = data.iloc[split_idx:]
    
    print(f"Total data points: {len(data)}")
    print(f"Training on first 60% ({len(train)} points), Testing on remaining 40% ({len(test)} points)")
    
    # Check cointegration on Training Data
    score, pvalue, _ = coint(train[ticker1], train[ticker2])
    print(f"Training Cointegration p-value: {pvalue:.4f}")
    if pvalue > 0.05:
        print("Warning: The pair does not appear significantly cointegrated in the training set.")
    
    # Calculate hedge ratio using OLS on Training Data
    model = sm.OLS(train[ticker1], sm.add_constant(train[ticker2])).fit()
    hedge_ratio = model.params[ticker2]
    print(f"Calculated Hedge Ratio (Train): {hedge_ratio:.4f}")
    
    # Calculate spread on Train and Test
    train_spread = train[ticker1] - hedge_ratio * train[ticker2]
    test_spread = test[ticker1] - hedge_ratio * test[ticker2]
    
    # Calculate z-score of spread using Training mean and std
    train_mean = train_spread.mean()
    train_std = train_spread.std()
    
    test_zscore = (test_spread - train_mean) / train_std
    
    # Simple trading logic on Test Data
    # Buy spread when z-score < -2, Sell spread when z-score > 2
    longs = test_zscore < -2.0
    shorts = test_zscore > 2.0
    
    print(f"Test Set Trading Opportunities:")
    print(f" - Long spread signals (Z < -2): {longs.sum()}")
    print(f" - Short spread signals (Z > +2): {shorts.sum()}")
    
    # Save to CSV
    results = pd.DataFrame({
        ticker1: test[ticker1],
        ticker2: test[ticker2],
        'Spread': test_spread,
        'Z-Score': test_zscore,
        'Signal_Long': longs,
        'Signal_Short': shorts
    })
    filename = os.path.join(RESULTS_DIR, f"pairs_trading_{ticker1}_{ticker2}_results.csv")
    results.to_csv(filename)
    print(f"Results saved to {filename}")

def machine_learning_strategy(data, ticker):
    print(f"\n--- Machine Learning (Random Forest) for {ticker} ---")
    
    # Prepare features
    df = pd.DataFrame(data[ticker]).rename(columns={ticker: 'Close'})
    df['Returns'] = df['Close'].pct_change()
    df['SMA_10'] = df['Close'].rolling(window=10).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    df['Vol_10'] = df['Returns'].rolling(window=10).std()
    
    # Target: 1 if next day return is positive, 0 if negative
    df['Target'] = (df['Returns'].shift(-1) > 0).astype(int)
    
    df = df.dropna()
    
    features = ['Returns', 'SMA_10', 'SMA_50', 'Vol_10']
    X = df[features]
    y = df['Target']
    
    # 60% Train, 40% Test split
    split_idx = int(len(df) * 0.6)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    
    print(f"Training on first 60% ({len(X_train)} points), Testing on remaining 40% ({len(X_test)} points)")
    
    # Train the model
    clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    clf.fit(X_train, y_train)
    
    # Predict on Test Set
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    
    print(f"Test Accuracy: {acc:.4f}")
    print("\nClassification Report (Test Data):")
    print(classification_report(y_test, y_pred))
    
    # Save results
    results = X_test.copy()
    results['Actual'] = y_test
    results['Predicted'] = y_pred
    filename = os.path.join(RESULTS_DIR, f"ml_strategy_{ticker}_results.csv")
    results.to_csv(filename)
    print(f"Results saved to {filename}")


if __name__ == "__main__":
    print("=== Extracting Data & Running Better Strategies ===")
    print("Note on Intraday Data: Free public APIs (like yfinance) limit high-resolution intraday data (5m, 15m) to the last 60 days. For the 10-year requirement, we are using Daily bars here.")
    
    # 1. Pairs Trading on Equities (Coca-Cola vs Pepsi) - Daily 10 years
    coke_pepsi = fetch_data(['KO', 'PEP'], period="10y", interval="1d")
    pairs_trading_strategy(coke_pepsi, 'KO', 'PEP')
    
    # 2. Pairs Trading on FX (EUR/USD vs GBP/USD) - Daily 10 years
    fx_pairs = fetch_data(['EURUSD=X', 'GBPUSD=X'], period="10y", interval="1d")
    pairs_trading_strategy(fx_pairs, 'EURUSD=X', 'GBPUSD=X')
    
    # 3. Machine Learning on an Equity Index (S&P 500 ETF) - Daily 10 years
    spy = fetch_data(['SPY'], period="10y", interval="1d")
    machine_learning_strategy(spy, 'SPY')
    
    # 4. Machine Learning on 60 Days of 15-Minute Intraday Data (Maximum allowed by yfinance for free)
    print("\n--- Intraday Data Example (60 days max for free APIs) ---")
    spy_intraday = fetch_data(['SPY'], period="60d", interval="15m")
    machine_learning_strategy(spy_intraday, 'SPY')
