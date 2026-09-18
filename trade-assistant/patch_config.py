import re

with open('config.yaml', 'r') as f:
    config = f.read()

# 1. Remove/Comment out strategy_priority
config = re.sub(r'(strategy_priority:\n(?:  - \w+\n)*)', r'# \1', config)

# 2. Update strict parameters globally in the strategies section where they exist
config = re.sub(r'stop_atr:\s*[0-9.]+', 'stop_atr: 2.0', config)
config = re.sub(r'reward_multiple:\s*[0-9.]+', 'reward_multiple: 1.5', config)
config = re.sub(r'max_vol_pct:\s*[0-9.]+', 'max_vol_pct: 80.0', config)

# 3. Loosen regimes
# Replace any regimes: [...] with all regimes
config = re.sub(r'regimes:\s*\[.*?\]', 'regimes: [TRENDING_UP, SIDEWAYS, VOLATILITY_SQUEEZE, TRENDING_DOWN]', config)

# 4. Enable composite so that priority list isn't needed
config = re.sub(r'composite:\n\s+enabled:\s*false', 'composite:\n  enabled: true', config)

with open('config.yaml', 'w') as f:
    f.write(config)
print("config.yaml patched successfully.")
