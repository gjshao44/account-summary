import streamlit as st
import pandas as pd
import glob
import os
import altair as alt

# This CSS overrides Streamlit's default behavior of centering charts
st.markdown("""
    <style>
    div[data-testid="stAltairChart"] {
        text-align: left !important;
        margin-left: 0 !important;
        display: block !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("💼 Portfolio Analytics Dashboard")

# --- DATA LOADING LOGIC (Runs on every page load) ---
output_dir = 'financial_data/output'
summary_files = glob.glob(os.path.join(output_dir, 'Master_Account_Summary_*.csv'))
latest_summary_file = max(summary_files, key=os.path.getmtime) if summary_files else None

# --- REFRESH BUTTON ---
if st.button("🔄 Run Portfolio Integration & Generate Summary"):
    with st.spinner("Processing brokerage statement data..."):
        try:
            # 1. Execute your backend processing pipeline 
            from portfolio_backend import run_portfolio_workflow
            run_portfolio_workflow()
            st.rerun() # Refresh the page to show new data immediately
        except Exception as e:
            st.error(f"Pipeline process disrupted: {e}")

# --- DISPLAY LOGIC (Runs automatically if file exists) ---
if latest_summary_file:
    master_df = pd.read_csv(latest_summary_file)
    
    # Calculate Gain
    master_df['Gain'] = master_df['MarketValue'] - master_df['CostBasis']
    # --- 1. CALCULATE GLOBAL OVERALL ALLOCATIONS ---
    overall_df = master_df.groupby('Allocation').agg({
        'MarketValue': 'sum',
        'Gain': 'sum',
        'AnnualIncome': 'sum'
    }).reset_index()

    total_mv = overall_df['MarketValue'].sum()
    total_gain = overall_df['Gain'].sum()
    
    # Calculate implied Yield % for the high-level rows
    overall_df['Div Rate'] = (overall_df['AnnualIncome'] / overall_df['MarketValue'] * 100).fillna(0)
    
    if total_mv > 0:
        overall_df['Allocation %'] = (overall_df['MarketValue'] / total_mv) * 100
    else:
        overall_df['Allocation %'] = 0.0

    # Append Overall Total Summary Row
    overall_total = pd.DataFrame([{
        'Allocation': 'Total Portfolio',
        'MarketValue': total_mv,
        'Gain': total_gain,
        'AnnualIncome': overall_df['AnnualIncome'].sum(),
        'Div Rate': (overall_df['AnnualIncome'].sum() / total_mv * 100) if total_mv > 0 else 0.0,
        'Allocation %': 100.0
    }])
    overall_df = pd.concat([overall_df, overall_total], ignore_index=True)

    # --- 2. FORMAT AND DISPLAY MAIN SUMMARY ---
    st.subheader("📊 Portfolio Performance & Composition Overview")
        
    formatted_overall = overall_df.copy()
    formatted_overall['MarketValue'] = formatted_overall['MarketValue'].map('${:,.0f}'.format)
    formatted_overall['Gain'] = formatted_overall['Gain'].map('${:,.0f}'.format)
    formatted_overall['AnnualIncome'] = formatted_overall['AnnualIncome'].map('${:,.0f}'.format)
    formatted_overall['Div Rate'] = formatted_overall['Div Rate'].map('{:.2f}%'.format)
    formatted_overall['Allocation %'] = formatted_overall['Allocation %'].map('{:.2f}%'.format)
    # Then update your formatting block to include it
    
    
    st.dataframe(formatted_overall, width='stretch', hide_index=True)

    # Optional visualization chart callout using Altair for a clean pie layout
    chart_df = overall_df[overall_df['Allocation'] != 'Total Portfolio']
    # Define standard color encoding
    color_encoding = alt.Color("Allocation:N", title="Asset Class")
    pie_value = alt.Chart(chart_df).mark_arc(innerRadius=50).encode(
        theta=alt.Theta(field="MarketValue", type="quantitative"),
        color=color_encoding,
        tooltip=[
            alt.Tooltip('Allocation:N', title='Asset Class'),
            alt.Tooltip('MarketValue:Q', title='Value ($)', format=',.0f')
        ]
    ).properties(title="Market Value", height=280, width=280)

    pie_income = alt.Chart(chart_df).mark_arc(innerRadius=50).encode(
        theta=alt.Theta(field="AnnualIncome", type="quantitative"),
        color=color_encoding,
        tooltip=[
            alt.Tooltip('Allocation:N', title='Asset Class'),
            alt.Tooltip('AnnualIncome:Q', title='Value ($)', format=',.0f')
        ]
    ).properties(title="Annual Income", height=280, width=280)

    # Concatenate horizontally for shared legend
    combined_charts = (pie_value | pie_income).resolve_scale(color='shared')
    st.altair_chart(combined_charts, width='stretch')


    # --- 3. EXPANDABLE ACCOUNT-BY-ACCOUNT BREAKDOWNS ---
    st.write("") 
    with st.expander("🔍 Click to view detailed breakdown by Account Type"):
        
        # Group by account type and allocation
        acct_df = master_df.groupby(['AccountType', 'Allocation']).agg({
            'MarketValue': 'sum',
            'Gain': 'sum',
            'AnnualIncome': 'sum'
        }).reset_index()
        
        # Calculate yield and weight per account bucket
        # NOTE: this is each row's share of the FULL portfolio (not % within its AccountType),
        # so rows under a given AccountType won't sum to 100% - that's intentional, it shows
        # each holding's overall portfolio weight. Labeled accordingly below.
        acct_df['Div Rate'] = (acct_df['AnnualIncome'] / acct_df['MarketValue'] * 100).fillna(0)
        acct_df['% of Portfolio'] = (acct_df['MarketValue'] / total_mv * 100) if total_mv > 0 else 0.0
        
        # Reorder columns slightly for better readability
        acct_df = acct_df[['AccountType', 'Allocation', 'MarketValue', 'Gain', 'AnnualIncome', 'Div Rate', '% of Portfolio']]
        
        # Format sub-table
        formatted_acct = acct_df.copy()
        formatted_acct['MarketValue'] = formatted_acct['MarketValue'].map('${:,.0f}'.format)
        formatted_acct['Gain'] = formatted_acct['Gain'].map('${:,.0f}'.format)
        formatted_acct['AnnualIncome'] = formatted_acct['AnnualIncome'].map('${:,.0f}'.format)
        formatted_acct['Div Rate'] = formatted_acct['Div Rate'].map('{:.2f}%'.format)
        formatted_acct['% of Portfolio'] = formatted_acct['% of Portfolio'].map('{:.2f}%'.format)
        
        st.dataframe(formatted_acct, width='stretch', hide_index=True)

else:
    st.info("No summary data found. Click the button above to generate your portfolio report.")