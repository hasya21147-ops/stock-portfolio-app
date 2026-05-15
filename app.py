import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px

# page config
st.set_page_config(page_title="Portfolio Analyzer Pro", layout="wide")

def analyze_portfolio_with_api(holdings_list):
    region_summary = {"canada": 0.0, "us": 0.0, "international": 0.0}
    sector_summary = {}
    stock_exposure = {} 
    total_val = 0.0

    # fx rate
    try:
        usdcad_rate = yf.Ticker("CAD=X").fast_info['last_price']
    except:
        usdcad_rate = 1.40 # default fallback
        st.sidebar.warning("FX Error: Using fallback rate 1.40")

    for entry in holdings_list:
        if not entry.strip(): continue
        try:
            # parse input "TICKER(QTY)"
            ticker_symbol = entry.split('(')[0].upper().strip()
            quantity = int(entry.split('(')[1].replace(')', ''))
            
            # fetch data
            ticker_obj = yf.Ticker(ticker_symbol)
            info = ticker_obj.info
            price = ticker_obj.fast_info['last_price']
            currency = info.get("currency", "USD")

            # currency conversion
            price_cad = price * usdcad_rate if currency == "USD" else price
            value_cad = price_cad * quantity
            total_val += value_cad
            
            quote_type = info.get("quoteType", "Unknown")
            
            # unpack ETF/MF holdings if possible, otherwise treat as single stock
            holdings_found = False
            if quote_type in ["ETF", "MUTUALFUND"]:
                try:
                    # attempt to get top holdings data
                    df_holdings = ticker_obj.funds_data.top_holdings
                    if df_holdings is not None and not df_holdings.empty:
                        holdings_found = True
                        for _, row in df_holdings.iterrows():
                            # holdings data usually provides holding name and holding percent
                            h_name = row.get('Symbol', row.get('Holding Name', 'Unknown'))
                            h_weight = row.get('Holding Percent', 0)
                            exposure_val = value_cad * h_weight
                            stock_exposure[h_name] = stock_exposure.get(h_name, 0) + exposure_val
                        
                        # add a remaining holdings category for the rest of the ETF
                        total_weight_known = df_holdings['Holding Percent'].sum()
                        if total_weight_known < 1.0:
                            rem_val = value_cad * (1.0 - total_weight_known)
                            stock_exposure[f"Other ({ticker_symbol})"] = stock_exposure.get(f"Other ({ticker_symbol})", 0) + rem_val
                except:
                    holdings_found = False # Fallback if funds_data fails

            if not holdings_found:
                stock_exposure[ticker_symbol] = stock_exposure.get(ticker_symbol, 0) + value_cad

            # region splitting logic
            if ".TO" in ticker_symbol or ".V" in ticker_symbol:
                name = info.get("longName", "").upper()
                if any(x in name for x in ["U.S.", "US ", "S&P 500", "NASDAQ", "VUN"]):
                    region_tag = "us"
                elif any(x in name for x in ["INTL", "INTERNATIONAL", "EMERGING", "VIU", "XEF"]):
                    region_tag = "international"
                else:
                    region_tag = "canada"
            else:
                country = info.get("country", "Unknown")
                if country == "United States":
                    region_tag = "us"
                elif country == "Canada":
                    region_tag = "canada"
                else:
                    region_tag = "international"
            region_summary[region_tag] += value_cad
            
            # sector splitting logic
            weights = info.get("sectorWeightings", [])
            if quote_type in ["ETF", "MUTUALFUND"] and weights:
                # Weights is often a list of single-key dicts
                for weight_dict in weights:
                    for s_name, s_perc in weight_dict.items():
                        sector_val = value_cad * s_perc
                        clean_s_name = s_name.replace('_', ' ').lower()
                        sector_summary[clean_s_name] = sector_summary.get(clean_s_name, 0) + sector_val
            else:
                s_name = info.get("sector", "Unknown").lower()
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
        portfolio = user_input.strip().split('\n')
        regions, sectors, stocks, total = analyze_portfolio_with_api(portfolio)

    st.divider()
    st.header(f"Total Portfolio Value: ${total:,.2f} CAD")

    # regions and sectors
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Regional Allocation")
        region_df = pd.DataFrame([{"Region": r.title(), "Value": v} for r, v in regions.items()])
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
    stock_df = pd.DataFrame(stock_list).sort_values(by="Value", ascending=False).head(15) # Top 15

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
