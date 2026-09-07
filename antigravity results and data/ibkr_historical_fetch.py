import asyncio
import os
import pandas as pd
from datetime import datetime
from ib_insync import *
import time

BASE_DIR = "/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/IBKR_Client_Data"
os.makedirs(BASE_DIR, exist_ok=True)

# Define the assets we want to pull
CONTRACTS = {
    "US_Equities": [
        Stock('SPY', 'SMART', 'USD'),
        Stock('AAPL', 'SMART', 'USD')
    ],
    "UK_Equities": [
        Stock('AZN', 'SMART', 'GBP'), # London exchange
        Stock('HSBA', 'SMART', 'GBP')
    ],
    "FX": [
        Forex('EURUSD'),
        Forex('GBPUSD')
    ],
    # Fixed Income and Futures require specific expiry or local symbols, 
    # but we can try continuous futures if IBKR supports it for the user's data package.
    "Commodity_Futures": [
        ContFuture('CL', 'NYMEX'),
        ContFuture('GC', 'COMEX')
    ]
}

def fetch_10_years_5min(ib, contract, asset_class):
    print(f"\\n--- Fetching 10 Years of 5-Minute Data for {contract.symbol} ---")
    ib.qualifyContracts(contract)
    
    # 10 years = 120 months. We request 1 month at a time to avoid IBKR limits on 5-min bars.
    all_bars = []
    end_date = ''
    
    for i in range(120): # 120 months = 10 years
        print(f"Requesting month {i+1}/120 for {contract.symbol}...")
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_date,
                durationStr='1 M',
                barSizeSetting='5 mins',
                whatToShow='TRADES' if not isinstance(contract, Forex) else 'MIDPOINT',
                useRTH=False,
                formatDate=1
            )
            
            if not bars:
                print(f"No more data returned for {contract.symbol}. Stopping early.")
                break
                
            df = util.df(bars)
            all_bars.append(df)
            
            # The next end_date is the date of the very first bar in this batch
            end_date = bars[0].date
            
            # Sleep to prevent IBKR Pacing Violation (Max 60 requests per 10 mins)
            # 120 requests per symbol = we must pace ourselves.
            time.sleep(12) 
            
        except Exception as e:
            print(f"Error fetching data or Pacing Violation: {e}")
            print("Sleeping for 60 seconds to reset pacing limits...")
            time.sleep(60)
            
    if all_bars:
        final_df = pd.concat(all_bars).drop_duplicates(subset=['date']).sort_values(by='date')
        
        class_dir = os.path.join(BASE_DIR, asset_class)
        os.makedirs(class_dir, exist_ok=True)
        filename = os.path.join(class_dir, f"{contract.symbol}_5m_10years.csv")
        final_df.to_csv(filename, index=False)
        print(f"Successfully saved {len(final_df)} rows to {filename}")
    else:
        print(f"Failed to fetch any data for {contract.symbol}")

def main():
    print("Connecting to IBKR...")
    ib = IB()
    try:
        # Connect to TWS or IB Gateway (Default ports: 7496 for live, 7497 for paper)
        # Using clientId=999 to not conflict with your trade-assistant
        ib.connect('127.0.0.1', 7497, clientId=999)
        print("Connected Successfully!")
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Please ensure TWS or IB Gateway is running and API access is enabled on port 7497.")
        return

    for asset_class, contracts in CONTRACTS.items():
        for contract in contracts:
            fetch_10_years_5min(ib, contract, asset_class)
            
    ib.disconnect()
    print("\\nAll requested data extraction complete!")

if __name__ == '__main__':
    main()
