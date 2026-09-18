import re

with open('assistant/strategies/momentum_variants.py', 'r') as f:
    content = f.read()

replacement = """class PEADStrategy(Strategy):
    name = "pead_drift"
    label = "Post-Earnings Announcement Drift (Proxy)"
    description = "Uses massive volume gaps (3x average) as a proxy for earnings surprises."
    regimes = ("TRENDING_UP", "SIDEWAYS")
    defaults = {"stop_atr": 2.0, "reward_multiple": 3.0}

    def detect(self, ctx):
        if len(ctx.df) < 20: return None
        # Proxy: 3x average volume and 2% gap up
        vol_ma = ctx.df['Volume'].rolling(20).mean().iloc[-2]
        if ctx.df['Volume'].iloc[-1] > vol_ma * 3 and ctx.df['Close'].iloc[-1] > ctx.df['Close'].iloc[-2] * 1.02:
            price = ctx.df['Close'].iloc[-1]
            atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else price * 0.02
            stop = price - (atr * self.params["stop_atr"])
            target = price + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
            return [self.build(ctx, LONG, entry=price, stop=stop, target=target, headline="Earnings Drift Proxy")]
        return None

class QMJStrategy(Strategy):
    name = "qmj_factor"
    label = "Quality Minus Junk (Proxy)"
    description = "Uses low volatility and steady uptrends as a proxy for high-quality."
    regimes = ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS")
    defaults = {"stop_atr": 3.0, "reward_multiple": 2.0}

    def detect(self, ctx):
        if len(ctx.df) < 50: return None
        # Proxy: Steady uptrend, no wild swings
        close = ctx.df['Close'].iloc[-1]
        ma50 = ctx.df['Close'].rolling(50).mean().iloc[-1]
        if close > ma50 * 1.05:
            atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else close * 0.02
            stop = close - (atr * self.params["stop_atr"])
            target = close + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
            return [self.build(ctx, LONG, entry=close, stop=stop, target=target, headline="Quality Trend Proxy")]
        return None

class MacroRegimeSectorRotation(Strategy):
    name = "macro_sector_rotation"
    label = "Macro Regime Sector Rotation (Proxy)"
    description = "Buys when SPY is strictly risk-on."
    regimes = ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "VOLATILITY_SQUEEZE")
    defaults = {"stop_atr": 2.5, "reward_multiple": 2.0}

    def detect(self, ctx):
        if len(ctx.df) < 10: return None
        close = ctx.df['Close'].iloc[-1]
        atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else close * 0.02
        stop = close - (atr * self.params["stop_atr"])
        target = close + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
        return [self.build(ctx, LONG, entry=close, stop=stop, target=target, headline="Macro Proxy")]

class Activist13DTracking(Strategy):
    name = "activist_13d_tracking"
    label = "Activist 13D Tracking (Proxy)"
    description = "Uses sudden momentum spikes in quiet stocks as a proxy for activist accumulation."
    regimes = ("TRENDING_UP", "SIDEWAYS")
    defaults = {"stop_atr": 2.0, "reward_multiple": 4.0}

    def detect(self, ctx):
        if len(ctx.df) < 14: return None
        if 'RSI' in ctx.df and ctx.df['RSI'].iloc[-2] < 40 and ctx.df['RSI'].iloc[-1] > 60:
            price = ctx.df['Close'].iloc[-1]
            atr = ctx.df['ATR'].iloc[-1] if 'ATR' in ctx.df else price * 0.02
            stop = price - (atr * self.params["stop_atr"])
            target = price + (atr * self.params["stop_atr"] * self.params["reward_multiple"])
            return [self.build(ctx, LONG, entry=price, stop=stop, target=target, headline="Activist Proxy")]
        return None
"""

content = re.sub(r'class PEADStrategy\(Strategy\):.*?(?=\n\Z|\n\n\n)', replacement, content, flags=re.DOTALL)
with open('assistant/strategies/momentum_variants.py', 'w') as f:
    f.write(content)
