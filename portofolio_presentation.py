import streamlit as st
import pandas as pd
import glob
import os
import altair as alt

st.title("💼 Portfolio Analytics Dashboard")

# Interactive Trigger Button
if st.button("🔄 Run Portfolio Integration & Generate Summary"):
    with st.spinner("Processing brokerage statement data..."):
        try:
            # 1. Execute your backend processing pipeline 
            from portofolio_backend import run_portfolio_workflow
            run_portfolio_workflow()
            
            # 2. Locate the newest master summary file generated
            output_dir = 'financial_data/output'
            summary_files = glob.glob(os.path.join(output_dir, 'Master_Account_Summary_*.csv'))
            
            if summary_files:
                latest_summary_file = max(summary_files, key=os.path.getmtime)
                master_df = pd.read_csv(latest_summary_file)
                
                st.success("Analysis Complete! Data synced across Schwab and Fidelity profiles.")
                
                # --- 1. CALCULATE GLOBAL OVERALL ALLOCATIONS ---
                overall_df = master_df.groupby('Allocation').agg({
                    'MarketValue': 'sum',
                    'AnnualIncome': 'sum'
                }).reset_index()

                total_mv = overall_df['MarketValue'].sum()
                
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
                    'AnnualIncome': overall_df['AnnualIncome'].sum(),
                    'Div Rate': (overall_df['AnnualIncome'].sum() / total_mv * 100) if total_mv > 0 else 0.0,
                    'Allocation %': 100.0
                }])
                overall_df = pd.concat([overall_df, overall_total], ignore_index=True)

                # --- 2. FORMAT AND DISPLAY MAIN SUMMARY ---
                st.subheader("📊 Overall Asset Allocation Strategy")
                
                formatted_overall = overall_df.copy()
                formatted_overall['MarketValue'] = formatted_overall['MarketValue'].map('${:,.0f}'.format)
                formatted_overall['AnnualIncome'] = formatted_overall['AnnualIncome'].map('${:,.0f}'.format)
                formatted_overall['Div Rate'] = formatted_overall['Div Rate'].map('{:.2f}%'.format)
                formatted_overall['Allocation %'] = formatted_overall['Allocation %'].map('{:.2f}%'.format)
                
                st.dataframe(formatted_overall, width='stretch', hide_index=True)

                # Optional visualization chart callout using Altair for a clean pie layout
                chart_df = overall_df[overall_df['Allocation'] != 'Total Portfolio']
                pie_chart = alt.Chart(chart_df).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta(field="MarketValue", type="quantitative"),
                    color=alt.Color(field="Allocation", type="nominal"),
                    tooltip=[
                        alt.Tooltip('Allocation:N', title='Asset Class'),
                        alt.Tooltip('MarketValue:Q', title='Value ($)', format=',.0f')
                    ]
                ).properties(
                    height=350
                )
                st.altair_chart(pie_chart, width='stretch')

                # --- 3. EXPANDABLE ACCOUNT-BY-ACCOUNT BREAKDOWNS ---
                st.write("") 
                with st.expander("🔍 Click to view detailed breakdown by Account Type"):
                    
                    # Group by account type and allocation
                    acct_df = master_df.groupby(['AccountType', 'Allocation']).agg({
                        'MarketValue': 'sum',
                        'AnnualIncome': 'sum'
                    }).reset_index()
                    
                    # Calculate yield and weight per account bucket
                    acct_df['Div Rate'] = (acct_df['AnnualIncome'] / acct_df['MarketValue'] * 100).fillna(0)
                    acct_df['Allocation %'] = (acct_df['MarketValue'] / total_mv * 100) if total_mv > 0 else 0.0
                    
                    # Reorder columns slightly for better readability
                    acct_df = acct_df[['AccountType', 'Allocation', 'MarketValue', 'AnnualIncome', 'Div Rate', 'Allocation %']]
                    
                    # Format sub-table
                    formatted_acct = acct_df.copy()
                    formatted_acct['MarketValue'] = formatted_acct['MarketValue'].map('${:,.0f}'.format)
                    formatted_acct['AnnualIncome'] = formatted_acct['AnnualIncome'].map('${:,.0f}'.format)
                    formatted_acct['Div Rate'] = formatted_acct['Div Rate'].map('{:.2f}%'.format)
                    formatted_acct['Allocation %'] = formatted_acct['Allocation %'].map('{:.2f}%'.format)
                    
                    st.dataframe(formatted_acct, width='stretch', hide_index=True)

            else:
                st.error("Workflow compiled successfully, but output summary file was not detected.")
        except Exception as e:
            st.error(f"Pipeline process disrupted: {e}")