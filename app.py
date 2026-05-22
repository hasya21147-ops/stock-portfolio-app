import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px

# page config
st.set_page_config(page_title="Portfolio Analyzer Pro", layout="wide")

# region name keywords for etf detection
REGION_NAME_KEYWORDS = {
    "us":            ["U.S.", "US ", "S&P 500", "NASDAQ", "RUSSELL", "VUN", "XUS", "VSP", "ZSP", "HXS"],
    "international": ["INTL", "INTERNATIONAL", "EMERGING", "VIU", "XEF", "XEC", "ZEM", "EAFE"],
}

def detect_region_for_etf(ticker_symbol, long_name, market):
    # use name keywords and market field instead of absent country field
    name_upper = long_name.upper()
    for region, keywords in REGION_NAME_KEYWORDS.items():
        if any(kw in name_upper or kw in ticker_symbol for kw in keywords):
            return region

    # yfinance returns market like "ca_market", "us_market"
    if market:
        if "ca" in market:
            return "canada"
        if "us" in market:
            return "us"

    return "international"  # safer default than "canada" for unknown etfs


def parse_sector_weightings(weights_raw):
    # sectorWeightings can be a list of single-key dicts OR a plain dict
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
    # real yfinance columns are camelCase and symbol is often the index, not a column
    try:
        df = ticker_obj.funds_data.top_holdings
        if df is None or df.empty:
            return None

        df.columns = [c.strip() for c in df.columns]

        # reset index if symbol is stored there
        if df.index.name and "symbol" in df.index.name.lower():
            df = df.reset_index()

        # flexible column matching in case yfinance changes names slightly
        weight_col = next(
            (c for c in df.columns if "percent" in c.lower() or "weight" in c.lower()),
            None,
        )
        symbol_col = next(
            (c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()),
            None,
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


def analyze_portfolio_with_api(holdings_list):
    region_summary = {"canada": 0.0, "us": 0.0, "international": 0.0}
    sector_summary = {}
    stock_exposure = {}
    total_val = 0.0

    # fx rate
    try:
        usdcad_rate = yf.Ticker("CAD=X").fast_info["last_price"]
    except Exception:
        usdcad_rate = 1.38  # default fallback
        st.sidebar.warning("FX Error: Using fallback rate 1.38")

    for entry in holdings_list:
        if not entry.strip():
            continue
        try:
            # parse input "TICKER(QTY)"
            ticker_symbol = entry.split("(")[0].upper().strip()
            quantity = int(entry.split("(")[1].replace(")", ""))

            # fetch data
            ticker_obj = yf.Ticker(ticker_symbol)
            info = ticker_obj.info
            price = ticker_obj.fast_info["last_price"]
            currency = info.get("currency", "USD")

            # currency conversion
            price_cad = price * usdcad_rate if currency == "USD" else price
            value_cad = price_cad * quantity
            total_val += value_cad

            quote_type = info.get("quoteType", "EQUITY")
            long_name = info.get("longName", ticker_symbol)
            market = info.get("market")  # e.g. "ca_market", "us_market"
            is_fund = quote_type in ("ETF", "MUTUALFUND")

            # unpack etf/mf holdings if possible, otherwise treat as single stock
            holdings_unpacked = False
            if is_fund:
                df_h = get_top_holdings(ticker_obj)
                if df_h is not None and not df_h.empty:
                    holdings_unpacked = True
                    for _, row in df_h.iterrows():
                        h_sym = str(row.get("symbol", "Unknown")).strip()
                        h_pct = float(row.get("holdingPercent", 0))
                        stock_exposure[h_sym] = stock_exposure.get(h_sym, 0) + value_cad * h_pct

                    # add remaining holdings category for the rest of the etf
                    known_pct = df_h["holdingPercent"].sum()
                    if known_pct < 1.0:
                        rem_key = f"Other ({ticker_symbol})"
                        stock_exposure[rem_key] = (
                            stock_exposure.get(rem_key, 0) + value_cad * (1.0 - known_pct)
                        )

            if not holdings_unpacked:
                stock_exposure[ticker_symbol] = stock_exposure.get(ticker_symbol, 0) + value_cad

            # region splitting logic
            if is_fund:
                region_tag = detect_region_for_etf(ticker_symbol, long_name, market)
            else:
                country = info.get("country", "")
                if country == "United States":
                    region_tag = "us"
                elif country == "Canada":
                    region_tag = "canada"
                else:
                    region_tag = "international"
            region_summary[region_tag] += value_cad

            # sector splitting logic
            weights_raw = info.get("sectorWeightings", [])
            sector_weights = parse_sector_weightings(weights_raw) if is_fund else {}

            if sector_weights:
                for s_name, s_perc in sector_weights.items():
                    sector_summary[s_name] = sector_summary.get(s_name, 0) + value_cad * s_perc
            else:
                # individual stock, or etf where yfinance returned no sector data
                s_name = (info.get("sector") or "unknown").lower()
                sector_summary[s_name] = sector_summary.get(s_name, 0) + value_cad

        except Exception as e:
            st.error(f"Could not process {entry}: {e}")

    return region_summary, sector_summary, stock_exposure, total_val


# streamlit UI
st.title("Portfolio Exposure Analyzer")

user_input = st.text_area("Enter Holdings: TICKER(QTY) - one per line",
                          value="VFV.TO(50)\nTD.TO(100)\nMSFT(20)\nXEF.TO(100)")

if st.button("Analyze Portfolio"):
    with st.spinner("Fetching data from Yahoo Finance..."):
        portfolio = [line for line in user_input.strip().split("\n") if line.strip()]
        regions, sectors, stocks, total = analyze_portfolio_with_api(portfolio)

    st.divider()
    st.header(f"Total Portfolio Value: ${total:,.2f} CAD")

    # regions and sectors
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Regional Allocation")
        region_df = pd.DataFrame([{"Region": r.title(), "Value": v} for r, v in regions.items() if v > 0])
        fig_reg = px.pie(region_df, values='Value', names='Region', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
        st.plotly_chart(fig_reg, use_container_width=True)

    with col2:
        st.subheader("Sector Exposure")
        sector_df = pd.DataFrame([{"Sector": s.title(), "Value": v} for s, v in sectors.items() if v > 0])
        fig_sec = px.pie(sector_df, values='Value', names='Sector', hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe)
        st.plotly_chart(fig_sec, use_container_width=True)

    # stock exposure
    st.divider()
    st.subheader("Top Individual Stock Exposure")

    # process stock data for chart
    stock_list = [{"Ticker": t, "Value": v, "Percent": (v/total)*100} for t, v in stocks.items()]
    stock_df = pd.DataFrame(stock_list).sort_values(by="Value", ascending=False).head(15)  # top 15

    fig_stocks = px.bar(
        stock_df,
        x="Ticker",
        y="Value",
        text="Percent",
        labels={"Value": "Value (CAD)"},
        color="Value",
        color_continuous_scale="Viridis"
    )
    fig_stocks.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
    st.plotly_chart(fig_stocks, use_container_width=True)

    # table
    with st.expander("View Full Exposure Table"):
        st.table(pd.DataFrame(stock_list).sort_values(by="Value", ascending=False))
