import streamlit as st
import pandas as pd
import glob
import os
import subprocess
import sys
import openpyxl
import altair as alt

st.set_page_config(
    page_title="Portfolio & Budget Dashboard",
    page_icon="💼",
    layout="wide"
)

# --- DIRECTORY CONFIG ---
FINANCIAL_DATA_INPUT = 'financial_data/input'
FINANCIAL_DATA_OUTPUT = 'financial_data/output'
EXPENSE_DATA_INPUT = 'expense_data/input'
EXPENSE_DATA_OUTPUT = 'expense_data/output'

def trigger_portfolio_pipeline():
    try:
        subprocess.run([sys.executable, "portfolio_backend.py"], check=True, capture_output=True)
        return True, "Success"
    except Exception as e:
        return False, str(e)

def trigger_expense_pipeline():
    try:
        subprocess.run([sys.executable, "expense_budget_backend.py"], check=True, capture_output=True)
        return True, "Success"
    except Exception as e:
        return False, str(e)

def auto_run_pipelines_if_needed():
    st.sidebar.markdown("### 🔍 Debug Logs")
    
    # Portfolio Check
    portfolio_inputs = glob.glob(os.path.join(FINANCIAL_DATA_INPUT, '*'))
    portfolio_outputs = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, 'Master_Account_Summary_*.csv'))
    
    st.sidebar.write(f"Portfolio In: {len(portfolio_inputs)} files")
    st.sidebar.write(f"Portfolio Out: {len(portfolio_outputs)} files")
    
    if portfolio_inputs and not portfolio_outputs:
        st.sidebar.warning("Auto-running Portfolio pipeline...")
        success, msg = trigger_portfolio_pipeline()
        if success:
            st.rerun()

    # Expense Check
    expense_inputs = glob.glob(os.path.join(EXPENSE_DATA_INPUT, '*'))
    expense_outputs = glob.glob(os.path.join(EXPENSE_DATA_OUTPUT, 'Yearly_budget.xlsx'))
    
    st.sidebar.write(f"Expense In: {len(expense_inputs)} files")
    st.sidebar.write(f"Expense Out: {len(expense_outputs)} files")

    if expense_inputs and not expense_outputs:
        st.sidebar.warning("Auto-running Expense pipeline...")
        success, msg = trigger_expense_pipeline()
        if success:
            st.rerun()

# Run the debug-checked pipeline logic
auto_run_pipelines_if_needed()

tab_inv, tab_exp = st.tabs(["💼 Investment Portfolio", "📊 Cash Flow & Expense"])

with tab_inv:
    st.header("Portfolio Asset Allocation")
    summary_files = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, 'Master_Account_Summary_*.csv'))
    
    if summary_files:
        latest_file = max(summary_files, key=os.path.getmtime)
        # ENGINE='python' IS MANDATORY FOR STABILITY
        df = pd.read_csv(latest_file, engine='python')
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No Portfolio summary found. Please sync data.")

with tab_exp:
    st.header("Expense Tracking")
    # Add your expense rendering logic here using the same engine='python' pattern
    st.write("Expense content loading...")

with st.sidebar:
    st.markdown("---")
    if st.button("🔄 Manual Sync All"):
        trigger_portfolio_pipeline()
        trigger_expense_pipeline()
        st.rerun()