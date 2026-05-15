import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px

# page config
st.set_page_config(page_title="portfolio analyzer", layout="wide")

def analyze_portfolio_with_api(holdings_list):
    region_summary = {"canada": 0.0, "us": 0.0, "international": 0.0}
    sector_summary = {}
    total_val = 0.0

    # fx rate
    try:
        usdcad_rate = yf.Ticker("CAD=X").fast_info['last_price']
    except:
        usdcad_rate = 1.38
        st.sidebar.warning("fx error: using 1.38")

    for entry in holdings_list:
        try:
            # input
            ticker_symbol = entry.split('(')[0].upper().strip()
            quantity = int(entry.split('(')[1].replace(')', ''))
            
            # data
            ticker_obj = yf.Ticker(ticker_symbol)
            info = ticker_obj.info
            price = ticker_obj.fast_info['last_price']
            currency = info.get("currency", "USD")

            # currency conversion
            price_cad = price * usdcad_rate if currency == "USD" else price
            value_cad = price_cad * quantity
            total_val += value_cad
            
            # investment type
            quote_type = info.get("quoteType", "Unknown")
            
            # region logic
            if ".TO" in ticker_symbol or ".V" in ticker_symbol:
                # heuristic: check if cad etf holds us/intl assets
                name = info.get("longName", "").upper()
                if any(x in name for x in ["U.S.", "US ", "S&P 500", "NASDAQ"]):
                    region_tag = "us"
                elif any(x in name for x in ["INTL", "INTERNATIONAL", "EMERGING", "VIU"]):
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
            
            # sector split logic
            weights = info.get("sectorWeightings", [])
            if quote_type in ["ETF", "MUTUALFUND"] and weights:
                # handle list of dicts
                for weight_dict in weights:
                    for s_name, s_perc in weight_dict.items():
                        sector_val = value_cad * s_perc
                        clean_s_name = s_name.replace('_', ' ').lower()
                        sector_summary[clean_s_name] = sector_summary.get(clean_s_name, 0) + sector_val
            else:
                # standard stocks
                s_name = info.get("sector", "unknown").lower()
                sector_summary[s_name] = sector_summary.get(s_name, 0) + value_cad

            # region summarize
            region_summary[region_tag] += value_cad
            
        except Exception as e:
            st.error(f"could not process {entry}: {e}")

    return region_summary, sector_summary, total_val

# streamlit ui
st.title("portfolio analyzer")

# input area
user_input = st.text_area("enter holdings like TICKER(QTY) - one per line", 
                         value="VFV.TO(50)\nTD.TO(100)\nMSFT(20)\nXEF.TO(100)")

if st.button("analyze"):
    portfolio = user_input.strip().split('\n')
    regions, sectors, total = analyze_portfolio_with_api(portfolio)

    # print
    st.header("final results")
    st.subheader(f"total portfolio: ${total:,.2f} CAD")

    col1, col2 = st.columns(2)

    with col1:
        st.write("### regions")
        region_data = []
        for r, val in regions.items():
            perc = (val / total) * 100 if total > 0 else 0
            region_data.append({"region": r, "value": val, "percent": perc})
            st.write(f"{r}: ${val:,.2f} ({perc:.1f}%)")
        
        # simple pie chart
        fig_reg = px.pie(region_data, values='value', names='region', hole=0.3)
        st.plotly_chart(fig_reg, use_container_width=True)

    with col2:
        st.write("### sectors")
        sector_data = []
        # sort sectors by value so the biggest are on top
        sorted_sectors = dict(sorted(sectors.items(), key=lambda item: item[1], reverse=True))
        for s, val in sorted_sectors.items():
            perc = (val / total) * 100 if total > 0 else 0
            if perc > 0.1: # hide tiny fractions
                sector_data.append({"sector": s, "value": val, "percent": perc})
                st.write(f"{s}: ${val:,.2f} ({perc:.1f}%)")
        
        # simple pie chart
        fig_sec = px.pie(sector_data, values='value', names='sector', hole=0.3)
        st.plotly_chart(fig_sec, use_container_width=True)