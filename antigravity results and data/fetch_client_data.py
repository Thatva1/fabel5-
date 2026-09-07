import yfinance as yf
import pandas as pd
import os

BASE_DIR = "/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/Client_Data_Export"
os.makedirs(BASE_DIR, exist_ok=True)

ASSET_CLASSES = {
    "US_Equities": ["SPY", "AAPL"],
    "UK_Equities": ["AZN.L", "HSBA.L"],
    "FX": ["EURUSD=X", "GBPUSD=X"],
    "Fixed_Income": ["TLT", "IEF"],
    "Derivatives_and_Commodities": ["CL=F", "GC=F"]  # Crude Oil and Gold Futures
}

TIMEFRAMES = {
    "Daily_10_Years": {"interval": "1d", "period": "10y"},
    "Intraday_30_Min": {"interval": "30m", "period": "60d"},
    "Intraday_15_Min": {"interval": "15m", "period": "60d"},
    "Intraday_5_Min": {"interval": "5m", "period": "60d"}
}

print("Starting comprehensive data extraction for client presentation...")

for asset_class, symbols in ASSET_CLASSES.items():
    class_dir = os.path.join(BASE_DIR, asset_class)
    os.makedirs(class_dir, exist_ok=True)
    
    for symbol in symbols:
        for tf_name, tf_params in TIMEFRAMES.items():
            print(f"Fetching {asset_class} -> {symbol} -> {tf_name}")
            try:
                data = yf.download(
                    symbol, 
                    interval=tf_params["interval"], 
                    period=tf_params["period"], 
                    progress=False
                )
                if not data.empty:
                    # Flatten MultiIndex columns if present
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = [col[0] for col in data.columns]
                    
                    filename = f"{symbol.replace('=', '_').replace('.', '_')}_{tf_name}.csv"
                    filepath = os.path.join(class_dir, filename)
                    data.to_csv(filepath)
                    print(f"  -> Saved {len(data)} rows to {filename}")
                else:
                    print(f"  -> No data found for {symbol} at {tf_name}")
            except Exception as e:
                print(f"  -> Failed to fetch {symbol} at {tf_name}: {e}")

print("\nData extraction complete! All files saved in: ", BASE_DIR)
