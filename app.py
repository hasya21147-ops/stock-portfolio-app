import datetime
import streamlit as st
import yfinance as yf
import mstarpy as ms
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Portfolio Analyzer Pro", layout="wide")

# keywords to guess region from etf/fund name since yfinance often lacks country field
region_keywords = {
    "us":            ["U.S.", "US ", "S&P 500", "NASDAQ", "RUSSELL", "VUN", "XUS", "VSP",
                      "ZSP", "HXS", "AMERICAN", "UNITED STATES"],
    "canada":        ["CANADIAN", "CANADA", "TSX", "RBC CANADIAN", "TD CANADIAN", "FIDELITY CANADIAN"],
    "international": ["INTL", "INTERNATIONAL", "EMERGING", "VIU", "XEF", "XEC", "ZEM",
                      "EAFE", "GLOBAL", "WORLD"],
}

# manual overrides for canadian mutual funds
canadian_mutual_funds = {
    "RBF460": {"region": "canada",        "sector": "diversified"},   # RBC Select Balanced
    "RBF461": {"region": "canada",        "sector": "diversified"},   # RBC Select Growth
    "RBF556": {"region": "us",            "sector": "diversified"},   # RBC US Equity
    "TDB902": {"region": "us",            "sector": "diversified"},   # TD US Index
    "TDB909": {"region": "canada",        "sector": "diversified"},   # TD Canadian Index
    "TDB911": {"region": "international", "sector": "diversified"},   # TD International
    "MAW104": {"region": "international", "sector": "diversified"},   # Mawer Global Equity
    "MAW106": {"region": "canada",        "sector": "diversified"},   # Mawer Canadian Equity
    "CIB228": {"region": "canada",        "sector": "diversified"},   # CI Canadian Equity
}


def get_fund_overrides(ticker_symbol):
    # strip suffixes to match bare fund codes in the override table
    base = ticker_symbol.replace(".CF", "").replace(".TO", "").replace(".CN", "").replace(".VN", "")
    return canadian_mutual_funds.get(base, {})


def looks_like_canadian_fund(ticker_symbol):
    # bare alphanumeric codes like RBF460, TDB902 — no exchange suffix
    base = ticker_symbol.replace(".CF", "").replace(".CN", "")
    has_no_suffix = not any(ticker_symbol.endswith(s) for s in [".TO", ".V", ".TSX"])
    looks_like_fund_code = base.isalnum() and len(base) >= 5 and not base.isalpha()
    return has_no_suffix and looks_like_fund_code

def get_morningstar_nav(ticker_symbol):
    # try yfinance with .CF suffix first 
    base = ticker_symbol.replace(".CF", "").replace(".TO", "").replace(".CN", "")
    try:
        t = yf.Ticker(base + ".CF")
        info = t.info
        price = info.get("navPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        if price and float(price) > 0:
            return float(price), info.get("longName", base)
    except Exception:
        pass

    # fallback: manually maintained NAVs for funds yfinance can't get
    # update these periodically — canadian mutual fund NAVs change slowly
    manual_navs = {
        "TDB902": (45.21, "TD US Index - e"),
        "TDB909": (32.18, "TD Canadian Index - e"),
        "TDB911": (22.60, "TD International Index - e"),
        "RBF460": (19.43, "RBC Select Balanced"),
        "RBF461": (21.10, "RBC Select Growth"),
        "RBF556": (18.75, "RBC US Equity"),
        "MAW104": (55.32, "Mawer Global Equity"),
        "MAW106": (41.87, "Mawer Canadian Equity"),
        "CIB228": (14.22, "CI Canadian Equity"),
    }
    if base in manual_navs:
        st.info(f"Using cached NAV for {base} — yfinance unavailable. Update manual_navs periodically.")
        return manual_navs[base]

    st.warning(f"NAV lookup failed for {ticker_symbol} — add it to manual_navs.")
    return None, None

def resolve_ticker(ticker_symbol):
    # try bare symbol first, then common canadian suffixes
    candidates = [ticker_symbol, ticker_symbol + ".CF", ticker_symbol + ".TO", ticker_symbol + ".CN"]
    for sym in candidates:
        t = yf.Ticker(sym)
        info = t.info
        qt = info.get("quoteType", "")
        if qt in ("MUTUALFUND", "ETF", "EQUITY") and (info.get("regularMarketPrice") or info.get("navPrice")):
            return t, info, sym
    t = yf.Ticker(ticker_symbol)
    return t, t.info, ticker_symbol


def get_price(ticker_obj, info):
    # navPrice first for mutual funds, then standard fields, then history fallback
    for field in ("navPrice", "regularMarketPrice", "previousClose", "ask", "bid"):
        price = info.get(field)
        if price and price > 0:
            return price
    try:
        hist = ticker_obj.history(period="5d")
        if not hist.empty:
            return hist["Close"].dropna().iloc[-1]
    except Exception:
        pass
    return None


def get_currency(info, ticker_symbol):
    currency = info.get("currency")
    if currency:
        return currency
    market = info.get("market", "")
    if "ca" in market or ticker_symbol.endswith((".TO", ".CF", ".CN", ".VN")):
        return "CAD"
    return "USD"


def detect_region_for_etf(ticker_symbol, long_name, market):
    # check name keywords first, then market field, then suffix
    name_upper = long_name.upper()
    for region, keywords in region_keywords.items():
        if any(kw in name_upper or kw in ticker_symbol for kw in keywords):
            return region
    if market:
        if "ca" in market:
            return "canada"
        if "us" in market:
            return "us"
    if ticker_symbol.endswith((".TO", ".CF")):
        return "canada"
    return "international"


def parse_sector_weightings(weights_raw):
    # sector weightings can be a list of single-key dicts OR a plain dict
    result = {}
    if isinstance(weights_raw, list):
        for item in weights_raw:
            if isinstance(item, dict):
                for k, v in item.items():
                    result[k.replace("_", " ").lower()] = v
    elif isinstance(weights_raw, dict):
        for k, v in weights_raw.items():
            result[k.replace("_", " ").lower()] = v
    return result


def get_top_holdings(ticker_obj):
    try:
        df = ticker_obj.funds_data.top_holdings
        if df is None or df.empty:
            return None

        df.columns = [c.strip() for c in df.columns]

        if df.index.name and "symbol" in df.index.name.lower():
            df = df.reset_index()

        weight_col = next(
            (c for c in df.columns if "percent" in c.lower() or "weight" in c.lower()), None
        )
        symbol_col = next(
            (c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()), None
        )
        if weight_col is None:
            return None
        if symbol_col is None and df.index.name:
            df = df.reset_index()
            symbol_col = df.columns[0]

        df = df.rename(columns={weight_col: "holdingPercent"})
        if symbol_col:
            df = df.rename(columns={symbol_col: "symbol"})
        else:
            df["symbol"] = df.index

        return df[["symbol", "holdingPercent"]].dropna()
    except Exception:
        return None


def get_portfolio_history(holdings_list, usdcad_rate, regions_snapshot, sectors_snapshot, total_snapshot):
    # regions_snapshot / sectors_snapshot are today's % splits — we apply them to each ticker's history
    # this is an approximation; exact historical splits aren't available without paid data
    ticker_meta = {}   # ticker → {quantity, multiplier, region_tag, sector_tag}

    for entry in holdings_list:
        if not entry.strip():
            continue
        try:
            ticker_symbol = entry.split("(")[0].upper().strip()
            quantity = int(entry.split("(")[1].replace(")", ""))

            # skip canadian mutual fund codes — no daily NAV history in yfinance
            if looks_like_canadian_fund(ticker_symbol):
                continue

            t = yf.Ticker(ticker_symbol)
            info = t.info
            currency = get_currency(info, ticker_symbol)
            multiplier = usdcad_rate if currency == "USD" else 1.0
            ticker_meta[ticker_symbol] = {"qty": quantity, "mult": multiplier, "obj": t}
        except Exception:
            continue

    if not ticker_meta:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # fetch all histories in one batch call — much faster, avoids partial gaps causing drops
    syms = list(ticker_meta.keys())
    raw = yf.download(syms, period="1y", auto_adjust=True, progress=False)["Close"]

    # yfinance returns a Series (not df) when only one ticker
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(name=syms[0])

    # forward-fill then back-fill to patch missing days (holidays, gaps) — fixes the random drops
    raw = raw.ffill().bfill()

    # build per-ticker value series
    value_df = pd.DataFrame(index=raw.index)
    for sym, meta in ticker_meta.items():
        if sym not in raw.columns:
            continue
        value_df[sym] = raw[sym] * meta["qty"] * meta["mult"]

    if value_df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    total_hist = value_df.sum(axis=1).to_frame("total")

    # region history — scale total by today's region % split each day
    region_hist = pd.DataFrame(index=total_hist.index)
    for r, v in regions_snapshot.items():
        if v > 0 and total_snapshot > 0:
            region_hist[r.title()] = total_hist["total"] * (v / total_snapshot)

    # sector history — same approach
    sector_hist = pd.DataFrame(index=total_hist.index)
    for s, v in sectors_snapshot.items():
        if v > 0 and total_snapshot > 0:
            sector_hist[s.title()] = total_hist["total"] * (v / total_snapshot)

    return total_hist, region_hist, sector_hist


def analyze_portfolio_with_api(holdings_list):
    region_summary = {"canada": 0.0, "us": 0.0, "international": 0.0}
    sector_summary = {}
    stock_exposure = {}
    total_val = 0.0

    try:
        usdcad_rate = yf.Ticker("CAD=X").fast_info["last_price"]
    except Exception:
        usdcad_rate = 1.38
        st.sidebar.warning("FX Error: Using fallback rate 1.38")

    for entry in holdings_list:
        if not entry.strip():
            continue
        try:
            ticker_symbol = entry.split("(")[0].upper().strip()
            quantity = int(entry.split("(")[1].replace(")", ""))
            overrides = get_fund_overrides(ticker_symbol)

            # canadian mutual fund codes → try morningstar first, yfinance as fallback
            price = None
            long_name = ticker_symbol
            info = {}
            ticker_obj = None

            if looks_like_canadian_fund(ticker_symbol):
                price, ms_name = get_morningstar_nav(ticker_symbol)
                if price:
                    long_name = ms_name or ticker_symbol
                    currency = "CAD"
                    quote_type = "MUTUALFUND"
                    market = "ca_market"

            if price is None:
                ticker_obj, info, resolved_sym = resolve_ticker(ticker_symbol)
                price = get_price(ticker_obj, info)
                long_name = info.get("longName", ticker_symbol)
                currency = get_currency(info, resolved_sym)
                quote_type = info.get("quoteType", "EQUITY")
                market = info.get("market")

            if price is None:
                st.warning(f"Could not get price for {ticker_symbol} — skipping.")
                continue

            price_cad = price * usdcad_rate if currency == "USD" else price
            value_cad = price_cad * quantity
            total_val += value_cad
            is_fund = quote_type in ("ETF", "MUTUALFUND")

            # unpack etf and mf holdings
            holdings_unpacked = False
            if is_fund and ticker_obj is not None:
                df_h = get_top_holdings(ticker_obj)
                if df_h is not None and not df_h.empty:
                    holdings_unpacked = True
                    for _, row in df_h.iterrows():
                        h_sym = str(row.get("symbol", "Unknown")).strip()
                        h_pct = float(row.get("holdingPercent", 0))
                        stock_exposure[h_sym] = stock_exposure.get(h_sym, 0) + value_cad * h_pct
                    known_pct = df_h["holdingPercent"].sum()
                    if known_pct < 1.0:
                        rem_key = f"Other ({ticker_symbol})"
                        stock_exposure[rem_key] = (
                            stock_exposure.get(rem_key, 0) + value_cad * (1.0 - known_pct)
                        )
                else:
                    st.info(f"{ticker_symbol}: No holdings data available — shown as a single position.")

            if not holdings_unpacked:
                stock_exposure[ticker_symbol] = stock_exposure.get(ticker_symbol, 0) + value_cad

            # for region prefer manual override, then auto-detect
            if overrides.get("region"):
                region_tag = overrides["region"]
            elif is_fund:
                region_tag = detect_region_for_etf(ticker_symbol, long_name, market or "")
            else:
                country = info.get("country", "")
                if country == "United States":
                    region_tag = "us"
                elif country == "Canada":
                    region_tag = "canada"
                else:
                    region_tag = "international"
            region_summary[region_tag] += value_cad

            # for sector prefer manual override, then sectorWeightings, then single stock field
            if overrides.get("sector"):
                s_name = overrides["sector"]
                sector_summary[s_name] = sector_summary.get(s_name, 0) + value_cad
            else:
                weights_raw = info.get("sectorWeightings", [])
                sector_weights = parse_sector_weightings(weights_raw) if is_fund else {}
                if sector_weights:
                    for s_name, s_perc in sector_weights.items():
                        sector_summary[s_name] = sector_summary.get(s_name, 0) + value_cad * s_perc
                else:
                    s_name = (info.get("sector") or "unknown").lower()
                    sector_summary[s_name] = sector_summary.get(s_name, 0) + value_cad

        except Exception as e:
            st.error(f"Could not process {entry}: {e}")

    return region_summary, sector_summary, stock_exposure, total_val, usdcad_rate


# website UI

st.title("Portfolio Exposure Analyzer")

user_input = st.text_area(
    "Enter Holdings: TICKER(QTY) or FUNDCODE(QTY) — one per line",
    value="VFV.TO(50)\nTD.TO(100)\nMSFT(20)\nXEF.TO(100)\nRBF460(200)\nTDB902(150)", height=500)

if st.button("Analyze Portfolio", type = "primary"):
    with st.spinner("Fetching data..."):
        portfolio = [line for line in user_input.strip().split("\n") if line.strip()]
        regions, sectors, stocks, total, usdcad_rate = analyze_portfolio_with_api(portfolio)

    st.divider()
    st.header(f"Total Portfolio Value: ${total:,.2f} CAD")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Regional Allocation")
        region_df = pd.DataFrame([{"Region": r.title(), "Value": v} for r, v in regions.items() if v > 0])
        fig_reg = px.pie(region_df, values="Value", names="Region", hole=0.4,
                         color_discrete_sequence=px.colors.qualitative.Pastel)
        st.plotly_chart(fig_reg, use_container_width=True)

    with col2:
        st.subheader("Sector Exposure")
        sector_df = pd.DataFrame([{"Sector": s.title(), "Value": v} for s, v in sectors.items() if v > 0])
        fig_sec = px.pie(sector_df, values="Value", names="Sector", hole=0.4,
                         color_discrete_sequence=px.colors.qualitative.Safe)
        st.plotly_chart(fig_sec, use_container_width=True)

    st.divider()
    st.subheader("Portfolio Value Over Time (1Y)")

    total_hist, region_hist, sector_hist = get_portfolio_history(portfolio, usdcad_rate, regions, sectors, total)

    if not total_hist.empty:
        col_h1, col_h2 = st.columns(2)

        with col_h1:
            st.markdown("**By Region**")
            # melt so total + each region are separate lines
            region_plot = region_hist.copy()
            region_plot["Total"] = total_hist["total"]
            region_plot.index.name = "Date"
            region_plot = region_plot.reset_index().melt("Date", var_name="Series", value_name="Value (CAD)")
            fig_rh = px.line(region_plot, x="Date", y="Value (CAD)", color="Series",
                             color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig_rh, use_container_width=True)

        with col_h2:
            st.markdown("**By Sector**")
            sector_plot = sector_hist.copy()
            sector_plot["Total"] = total_hist["total"]
            sector_plot.index.name = "Date"
            sector_plot = sector_plot.reset_index().melt("Date", var_name="Series", value_name="Value (CAD)")
            fig_sh = px.line(sector_plot, x="Date", y="Value (CAD)", color="Series",
                             color_discrete_sequence=px.colors.qualitative.Safe)
            st.plotly_chart(fig_sh, use_container_width=True)
    else:
        st.info("No historical data available for these holdings.")

    st.divider()
    st.subheader("Top Individual Stock Exposure")

    stock_list = [{"Ticker": t, "Value": v, "Percent": (v / total) * 100} for t, v in stocks.items()]
    stock_df = pd.DataFrame(stock_list).sort_values(by="Value", ascending=False).head(15)

    fig_stocks = px.bar(
        stock_df,
        x="Ticker",
        y="Value",
        text="Percent",
        labels={"Value": "Value (CAD)"},
        color="Value",
        color_continuous_scale="sunset",
    )
    fig_stocks.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    st.plotly_chart(fig_stocks, use_container_width=True)

    with st.expander("View Full Exposure Table"):
        st.table(pd.DataFrame(stock_list).sort_values(by="Value", ascending=False))
