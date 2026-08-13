import streamlit as st
import pandas as pd
import glob
import os
import re
import altair as alt
from datetime import datetime
import openpyxl
import subprocess
import shutil
import sys
import faulthandler

from financial_paths import INPUT_DIR as FINANCIAL_DATA_INPUT, OUTPUT_DIR as FINANCIAL_DATA_OUTPUT, BASE_DIR as FINANCIAL_DATA_BASE

# Enable C-level crash tracing for the main Streamlit process
faulthandler.enable()

# Configure the page first before rendering any elements
st.set_page_config(
    page_title="Portfolio & Budget Analytics Dashboard",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for strict left-alignment of elements and styling of metric cards
st.markdown("""
    <style>
    /* Pin all Altair charts to the left edge of their containers */
    div[data-testid="stAltairChart"] {
        text-align: left !important;
        margin-left: 0 !important;
        display: block !important;
    }
    
    /* Global metric design refinements */
    div[data-testid="stMetricValue"] {
        font-size: 2rem !important;
        font-weight: 700 !important;
    }
    
    /* Make custom styling containers for alerts */
    .alert-card {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
        border-left: 5px solid;
    }
    .alert-warning {
        background-color: rgba(255, 193, 7, 0.1);
        border-left-color: #ffc107;
    }
    .alert-danger {
        background-color: rgba(220, 53, 69, 0.1);
        border-left-color: #dc3545;
    }
    .alert-info {
        background-color: rgba(13, 110, 253, 0.1);
        border-left-color: #0d6efd;
    }

    /* Calmer color for primary buttons (e.g. "Sync All") instead of Streamlit's
       default bright red - scoped to this element only, doesn't touch the
       app-wide theme (so light/dark mode still follows the user's setting). */
    button[data-testid="stBaseButton-primary"] {
        background-color: #0d6efd !important;
        border-color: #0d6efd !important;
    }
    button[data-testid="stBaseButton-primary"]:hover,
    button[data-testid="stBaseButton-primary"]:focus:not(:active) {
        background-color: #0b5ed7 !important;
        border-color: #0b5ed7 !important;
        color: #ffffff !important;
    }
    </style>
""", unsafe_allow_html=True)

# Column-config helpers so tables stay numeric under the hood (correct header-click
# sorting in st.dataframe) instead of pre-formatted into display strings.
def dollar_col(label=None):
    return st.column_config.NumberColumn(label, format="$%,.0f")


def percent_col(label=None):
    """For values already on a 0-100 scale (e.g. 45.2 meaning 45.2%)."""
    return st.column_config.NumberColumn(label, format="%.2f%%")


def fraction_percent_col(label=None):
    """For values stored as a fraction (e.g. 0.452 meaning 45.2%) - auto-scales by 100."""
    return st.column_config.NumberColumn(label, format="percent")


def trigger_portfolio_pipeline():
    """Runs the investment pipeline backend in an isolated subprocess, capturing segmentation faults cleanly."""
    try:
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "portfolio_backend.py"],
            capture_output=True,
            text=True,
            check=True
        )
        return True, "Portfolio analytics pipeline finished successfully!"
    except subprocess.CalledProcessError as e:
        # Check specifically for Segmentation Fault exit codes (Unix/Linux/Mac)
        if e.returncode in [-11, 139]:
            return False, f"🚨 **SEGMENTATION FAULT DETECTED IN BACKEND** 🚨\n\nStack Trace:\n{e.stderr}"
        
        error_msg = e.stderr if e.stderr else e.stdout
        return False, f"Portfolio Pipeline process disrupted:\n{error_msg}"
    except Exception as e:
        return False, f"Portfolio Pipeline process disrupted: {e}"

def trigger_expense_pipeline():
    """Runs the expense/budget spreadsheet aggregation backend."""
    try:
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "expense_budget_backend.py"],
            capture_output=True,
            text=True,
            check=True
        )
        return True, "Expense aggregation pipeline finished successfully!"
    except subprocess.CalledProcessError as e:
        if e.returncode in [-11, 139]:
            return False, f"🚨 **SEGMENTATION FAULT DETECTED IN BACKEND** 🚨\n\nStack Trace:\n{e.stderr}"

        error_msg = e.stderr if e.stderr else e.stdout
        return False, f"Expense Pipeline process disrupted (Code {e.returncode}):\n{error_msg}"
    except Exception as e:
        return False, f"Expense Pipeline process disrupted: {e}"

def trigger_portfolio_history_pipeline():
    """Runs the multi-year portfolio history backend (Ending Value/Cash Flow/Gain/Return),
    recomputing only the current year's column."""
    try:
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "portfolio_history_backend.py"],
            capture_output=True,
            text=True,
            check=True
        )
        return True, "Multi-year portfolio history updated successfully!"
    except subprocess.CalledProcessError as e:
        if e.returncode in [-11, 139]:
            return False, f"🚨 **SEGMENTATION FAULT DETECTED IN BACKEND** 🚨\n\nStack Trace:\n{e.stderr}"

        error_msg = e.stderr if e.stderr else e.stdout
        return False, f"Portfolio History Pipeline process disrupted:\n{error_msg}"
    except Exception as e:
        return False, f"Portfolio History Pipeline process disrupted: {e}"


def trigger_all_pipelines():
    """Runs all 3 backends in dependency order (Portfolio History reads the
    Master_Account_Summary_*.csv that the Portfolio pipeline just wrote)."""
    steps = [
        ("Portfolio", trigger_portfolio_pipeline),
        ("Portfolio History", trigger_portfolio_history_pipeline),
        ("Expense/Budget", trigger_expense_pipeline),
    ]
    results = []
    for name, trigger_fn in steps:
        with st.spinner(f"Syncing {name}..."):
            success, msg = trigger_fn()
        results.append((name, success, msg))
    return results


def promote_output_to_input(filename):
    """Copies a reviewed file from financial_data/output back over the working
    copy in financial_data/input (the source of truth the backends read from
    on their next run), keeping a backup of what it replaces."""
    src = os.path.join(FINANCIAL_DATA_OUTPUT, filename)
    dst = os.path.join(FINANCIAL_DATA_INPUT, filename)
    if not os.path.exists(src):
        return False, f"No {filename} found in output/ yet - run a sync first."

    if os.path.exists(dst):
        backup_dir = os.path.join(FINANCIAL_DATA_BASE, 'backup')
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        stem, ext = os.path.splitext(filename)
        backup_path = os.path.join(backup_dir, f'{stem}_{timestamp}{ext}')
        shutil.copy2(dst, backup_path)

    shutil.copy2(src, dst)
    return True, f"Promoted {filename} to input/ (previous copy backed up to financial_data/backup/)."


# STREAMING_CHUNK: Managing automatic execution schedules...

def auto_run_pipelines_if_needed():
    """
    Checks if input files exist but output summaries do not exist.
    If so, runs the workflows automatically so the user doesn't have to press a button.
    """
    # Collect all possible portfolio input files
    portfolio_inputs = (
        glob.glob(os.path.join(FINANCIAL_DATA_INPUT, 'All-Accounts-Positions-*')) +
        glob.glob(os.path.join(FINANCIAL_DATA_INPUT, 'XXXXInvestmentIncome')) +
        glob.glob(os.path.join(FINANCIAL_DATA_INPUT, 'Portfolio_Positions*')) 
    )
    portfolio_outputs = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, 'Master_Account_Summary_*.csv'))
    
    # Check Expense
    expense_inputs = glob.glob(os.path.join(FINANCIAL_DATA_INPUT, '*transactions*.csv')) + glob.glob(os.path.join(FINANCIAL_DATA_INPUT, 'Yearly_budget.xlsx'))
    expense_outputs = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, 'Yearly_budget.xlsx'))
    
    status_msg = []
    
    if portfolio_inputs and not portfolio_outputs:
        with st.spinner("Processing investment files automatically..."):
            success, msg = trigger_portfolio_pipeline()
            if success:
                status_msg.append("🔄 Auto-generated fresh Portfolio summary.")
                
    if expense_inputs and not expense_outputs:
        with st.spinner("Processing expense sheets automatically..."):
            success, msg = trigger_expense_pipeline()
            if success:
                status_msg.append("📊 Auto-generated fresh Budget and Expense tracking workbook.")
                
    if status_msg:
        st.toast(" | ".join(status_msg), icon="⚡")

# Auto-execute pipelines behind the scenes if raw data is ready
# auto_run_pipelines_if_needed()


# STREAMING_CHUNK: Reading and parsing backend generated alert sheets...

def parse_alerts_log():
    """
    Parses the generated alerts.txt file from expense output folder
    to display category warnings and big transactions on the frontend.
    """
    alert_files = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, '*_alerts.txt'))
    if not alert_files:
        return None
    
    latest_alert_file = max(alert_files, key=os.path.getmtime)
    
    unmapped_categories = []
    others_rolls = []
    abnormal_txns = []
    
    current_section = None
    
    try:
        with open(latest_alert_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # Identify section headings
                if "UNMAPPED CATEGORIES" in line:
                    current_section = "unmapped"
                    continue
                elif "CATEGORIES ROLLED INTO 'OTHERS'" in line:
                    current_section = "others"
                    continue
                elif "ABNORMAL TRANSACTIONS" in line:
                    current_section = "abnormal"
                    continue
                elif line.startswith("===") or line.startswith("Alerts -"):
                    continue
                
                # Parse section items
                if current_section == "unmapped" and line.startswith("-"):
                    unmapped_categories.append(line[2:])
                elif current_section == "others" and line.startswith("-"):
                    others_rolls.append(line[2:])
                elif current_section == "abnormal" and line.startswith("-"):
                    abnormal_txns.append(line[2:])
    except Exception as e:
        return {"error": f"Failed to parse alerts file: {e}"}
        
    return {
        "unmapped": unmapped_categories,
        "others": others_rolls,
        "abnormal": abnormal_txns,
        "file_date": os.path.basename(latest_alert_file).replace('_alerts.txt', '')
    }


def parse_portfolio_history_alerts():
    """
    Parses the generated {year}_portfolio_alerts.txt file from the financial_data output
    folder (written by portfolio_history_backend.py) to surface account-mapping warnings
    on the frontend - the sidebar's "Update Portfolio History" button only shows a generic
    success message, so this is otherwise console-only.
    """
    alert_files = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, '*_portfolio_alerts.txt'))
    if not alert_files:
        return None

    latest_alert_file = max(alert_files, key=os.path.getmtime)

    skipped_accounts = []
    unmapped_investment_accounts = []
    other_warnings = []

    current_section = None

    try:
        with open(latest_alert_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                if "ACCOUNTS SKIPPED" in line:
                    current_section = "skipped"
                    continue
                elif "POSSIBLE UNMAPPED INVESTMENT ACCOUNTS" in line:
                    current_section = "unmapped_investment"
                    continue
                elif "NO TRANSACTIONS FILE FOUND" in line or "ROW LOOKUP WARNINGS" in line:
                    current_section = "other"
                    continue
                elif line.startswith("===") or line.startswith("Portfolio History Alerts -"):
                    continue

                if current_section == "skipped" and line.startswith("-"):
                    skipped_accounts.append(line[2:])
                elif current_section == "unmapped_investment" and line.startswith("-"):
                    unmapped_investment_accounts.append(line[2:])
                elif current_section == "other" and line.startswith("-"):
                    other_warnings.append(line[2:])
    except Exception as e:
        return {"error": f"Failed to parse portfolio alerts file: {e}"}

    return {
        "skipped": skipped_accounts,
        "unmapped_investment": unmapped_investment_accounts,
        "other": other_warnings,
        "file_date": os.path.basename(latest_alert_file).replace('_portfolio_alerts.txt', '')
    }


# STREAMING_CHUNK: Loading comparative multi-year budget spreadsheets...

def load_historical_budget_data():
    """
    Dynamically loads and parses Yearly_budget.xlsx from the output folder.
    Returns: DataFrames for Expenses and Income, a list of years tracked, and total metrics.
    """
    output_path = os.path.join(FINANCIAL_DATA_OUTPUT, 'Yearly_budget.xlsx')
    # Fallback to input if output hasn't been generated yet
    if not os.path.exists(output_path):
        output_path = os.path.join(FINANCIAL_DATA_INPUT, 'Yearly_budget.xlsx')
        if not os.path.exists(output_path):
            return None, None, [], {}, {}
            
    try:
        wb = openpyxl.load_workbook(output_path, data_only=True)
    except Exception:
        return None, None, [], {}, {}
    
    def parse_sheet_data(sheet_name):
        if sheet_name not in wb.sheetnames:
            return None, {}, []
            
        ws = wb[sheet_name]
        
        # Determine columns containing years
        col_to_year = {}
        for col in range(2, ws.max_column + 1):
            val = ws.cell(row=1, column=col).value
            if val is not None and str(val).strip().isdigit():
                col_to_year[col] = int(str(val).strip())
                
        years = sorted(list(set(col_to_year.values())))
        # Grab up to the last 5 years of history
        historical_years = years[-5:] if len(years) >= 5 else years
        
        # Locate the total row
        total_row_idx = None
        for r in range(1, ws.max_row + 1):
            val = ws.cell(row=r, column=1).value
            if val and str(val).strip().lower() == 'total':
                total_row_idx = r
                break
                
        categories_data = []
        totals_map = {yr: 0.0 for yr in historical_years}
        
        limit_row = total_row_idx if total_row_idx else ws.max_row + 1
        for r in range(2, limit_row):
            cat = ws.cell(row=r, column=1).value
            if cat is None or not str(cat).strip():
                continue
            cat = str(cat).strip()
            
            row_dict = {'Category': cat}
            for col, yr in col_to_year.items():
                if yr in historical_years:
                    val = ws.cell(row=r, column=col).value
                    row_dict[yr] = float(val) if val is not None else 0.0
            categories_data.append(row_dict)
            
        # Dynamically calculate sheet totals in Python memory to prevent None errors before Excel executes
        for yr in historical_years:
            totals_map[yr] = sum(row[yr] for row in categories_data)
        
        # Convert all categories list to a DataFrame
        df = pd.DataFrame(categories_data)

        # Sort by total magnitude across the shown years, largest first - matches
        # the ordering convention already used in the generated Summary sheet,
        # rather than whatever row order the sheet happens to have.
        if not df.empty:
            year_cols = [c for c in df.columns if c != 'Category']
            sort_key = df[year_cols].abs().sum(axis=1) if year_cols else 0
            df = df.assign(_sort_key=sort_key).sort_values('_sort_key', ascending=False) \
                   .drop(columns='_sort_key').reset_index(drop=True)

        # Cast headers to strings to resolve PyArrow warnings
        df.columns = df.columns.astype(str)

        return df, totals_map, historical_years

    df_exp, tot_exp, years_exp = parse_sheet_data('Expense')
    df_inc, tot_inc, years_inc = parse_sheet_data('Income')

    # Reconcile years across sheets
    all_years = sorted(list(set(years_exp + years_inc)))

    return df_exp, df_inc, all_years, tot_exp, tot_inc


# STREAMING_CHUNK: Loading multi-year portfolio history (Ending Value/Cash Flow/Gain/Return)...

def load_historical_portfolio_data():
    """
    Loads Portfolio_History.xlsx (written by portfolio_history_backend.py) and parses its
    four sheets - EndingValue, CashFlow, Gain, Return - each a per-account x per-year matrix
    with Non-Retirement Total / Retirement Total / Grand Total rows.
    Returns: {sheet_name: DataFrame} and the sorted list of years tracked.
    """
    output_path = os.path.join(FINANCIAL_DATA_OUTPUT, 'Portfolio_History.xlsx')
    if not os.path.exists(output_path):
        output_path = os.path.join(FINANCIAL_DATA_INPUT, 'Portfolio_History.xlsx')
        if not os.path.exists(output_path):
            return {}, []

    try:
        wb = openpyxl.load_workbook(output_path, data_only=True)
    except Exception:
        return {}, []

    def parse_sheet(sheet_name):
        if sheet_name not in wb.sheetnames:
            return None, []
        ws = wb[sheet_name]

        col_to_year = {}
        for col in range(2, ws.max_column + 1):
            val = ws.cell(row=1, column=col).value
            if val is not None and str(val).strip().isdigit():
                col_to_year[col] = int(str(val).strip())
        years = sorted(set(col_to_year.values()))

        rows = []
        for r in range(2, ws.max_row + 1):
            label = ws.cell(row=r, column=1).value
            if label is None or not str(label).strip():
                continue
            row_dict = {'Account': str(label).strip()}
            for col, yr in col_to_year.items():
                val = ws.cell(row=r, column=col).value
                row_dict[yr] = float(val) if val is not None else None
            rows.append(row_dict)

        df = pd.DataFrame(rows)
        df.columns = df.columns.astype(str)
        return df, years

    sheets = {}
    all_years = []
    for name in ['EndingValue', 'CashFlow', 'Gain', 'Return']:
        df, years = parse_sheet(name)
        if df is not None:
            sheets[name] = df
            if len(years) > len(all_years):
                all_years = years

    return sheets, all_years


# STREAMING_CHUNK: Styling the sidebar controls...

# Sidebar branding and triggers
with st.sidebar:
    st.image("https://img.icons8.com/fluent/96/000000/money-bag.png", width=64)
    st.title("Sync & Refresh")
    st.write("Update your account and budget data")

    st.markdown("---")

    # Portfolio Control
    st.subheader("💼 Investment Statements")
    if st.button("🔄 Sync Brokerage Data"):
        with st.spinner("Processing investment files..."):
            success, msg = trigger_portfolio_pipeline()
        if success:
            st.success(msg)
        else:
            st.error(msg)

    # Multi-Year Portfolio History Control
    st.subheader("📈 Multi-Year Portfolio History")
    # st.caption("Periodic reconciliation (not needed on every daily sync).")
    if st.button("🔄 Update Portfolio History"):
        with st.spinner("Recomputing multi-year portfolio history..."):
            success, msg = trigger_portfolio_history_pipeline()
        if success:
            st.success(msg)
        else:
            st.error(msg)

    # Expense Control
    st.subheader("📊 Expense & Cash Flow")
    if st.button("⚙️ Recalculate Budgets"):
        with st.spinner("Processing expense sheets..."):
            success, msg = trigger_expense_pipeline()
        if success:
            st.success(msg)
        else:
            st.error(msg)

    # Runs all 3 backends in one click, in dependency order
    if st.button("⚡ Sync All 3 Pipelines", type="primary", use_container_width=True):
        for name, success, msg in trigger_all_pipelines():
            if success:
                st.success(f"{name}: {msg}")
            else:
                st.error(f"{name}: {msg}")

    st.markdown("---")

    # Promote reviewed output files back to input/ (the working copy the
    # backends treat as source of truth on their next run)
    st.subheader("✅ Promote Reviewed Files")
    st.caption("Once you're happy with the numbers in output/, replace the working copy in input/.")

    for label, filename in [
        ("Yearly_budget.xlsx", "Yearly_budget.xlsx"),
        ("Portfolio_History.xlsx", "Portfolio_History.xlsx"),
    ]:
        confirm_key = f"confirm_promote_{filename}"
        has_output = os.path.exists(os.path.join(FINANCIAL_DATA_OUTPUT, filename))

        if st.button(f"📥 Promote {label}", key=f"promote_btn_{filename}", disabled=not has_output):
            st.session_state[confirm_key] = True

        if st.session_state.get(confirm_key):
            st.warning(f"This overwrites input/{filename} with the reviewed output copy.")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("✅ Confirm", key=f"promote_yes_{filename}"):
                st.session_state[confirm_key] = False
                success, msg = promote_output_to_input(filename)
                if success:
                    st.success(msg)
                else:
                    st.error(msg)
            if cancel_col.button("Cancel", key=f"promote_no_{filename}"):
                st.session_state[confirm_key] = False

    st.markdown("---")
    st.caption("Active Workspace Year: 2026")
    st.caption("Click the buttons above to manually sync your data.")

# Main multi-tab UI
tab_inv, tab_exp = st.tabs(["💼 Investment Portfolio", "📊 Cash Flow & Expense"])


# STREAMING_CHUNK: Formatting and displaying the Investment allocation tab...

# ==============================================================================
# TAB 1: PORTFOLIO PRESENTATION (INVESTMENTS)
# ==============================================================================
with tab_inv:
    st.header("Portfolio Asset Allocation")
    
    # Check for latest processed portfolio results
    # Using a broader wildcard to ensure we always catch the generated file
    summary_files = glob.glob(os.path.join(FINANCIAL_DATA_OUTPUT, '*Master_Account_Summary*.csv'))
    latest_summary_file = max(summary_files, key=os.path.getmtime) if summary_files else None
    
    if latest_summary_file:
        # SAFE PURE-PYTHON PARSING: Avoids memory segment faults on Python 3.14
        master_df = pd.read_csv(latest_summary_file, engine='python')
        
        # Clean currency configurations
        master_df['MarketValue'] = pd.to_numeric(master_df['MarketValue'], errors='coerce').fillna(0)
        master_df['CostBasis'] = pd.to_numeric(master_df['CostBasis'], errors='coerce').fillna(master_df['MarketValue'])
        master_df['Gain'] = master_df['MarketValue'] - master_df['CostBasis']
        master_df['AnnualIncome'] = pd.to_numeric(master_df['AnnualIncome'], errors='coerce').fillna(0)
        
        # Calculate summary indicators
        total_mv = master_df['MarketValue'].sum()
        total_gain = master_df['Gain'].sum()
        total_inc = master_df['AnnualIncome'].sum()
        
        # Render Metric Cards with color formatting
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total Market Value", f"${total_mv:,.0f}")
        
        # Color coding for gains
        gain_prefix = "+" if total_gain >= 0 else ""
        col_m2.metric(
            "Unrealized Gain / Loss", 
            f"{gain_prefix}${total_gain:,.0f}",
            delta=f"{total_gain/master_df['CostBasis'].sum()*100:+.2f}%" if master_df['CostBasis'].sum() > 0 else None
        )
        col_m3.metric("Annual Projected Dividends", f"${total_inc:,.0f}", delta=f"{(total_inc/total_mv)*100:.2f}% Yield" if total_mv > 0 else None)
        
        st.markdown("---")
        
        # Prepare allocation grouped layout
        overall_df = master_df.groupby('Allocation').agg({
            'MarketValue': 'sum',
            'Gain': 'sum',
            'AnnualIncome': 'sum'
        }).reset_index()
        
        overall_df['Div Rate'] = (overall_df['AnnualIncome'] / overall_df['MarketValue'] * 100).fillna(0)
        overall_df['Allocation %'] = (overall_df['MarketValue'] / total_mv * 100) if total_mv > 0 else 0.0
        total_mv = overall_df['MarketValue'].sum()
        total_gain = overall_df['Gain'].sum()

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

    
        st.markdown("### 📊 Performance & Composition Summary")

        # Keep the underlying data numeric (column_config just controls display
        # formatting) so clicking a column header sorts numerically, not alphabetically.
        st.dataframe(
            overall_df, width='stretch', hide_index=True,
            column_config={
                'MarketValue': dollar_col('MarketValue'),
                'Gain': dollar_col('Gain'),
                'AnnualIncome': dollar_col('AnnualIncome'),
                'Div Rate': percent_col('Div Rate'),
                'Allocation %': percent_col('Allocation %'),
            }
        )

        st.markdown("---")
        
        # Setup charts with clean, responsive container-scaling parameters
        # Exclude the "Total Portfolio" summary row - it's a sum of the other slices,
        # not a slice of its own, and would visually double the pie.
        chart_df = overall_df[overall_df['Allocation'] != 'Total Portfolio'].copy()
        income_sum = chart_df['AnnualIncome'].sum()
        chart_df['IncomePct'] = (chart_df['AnnualIncome'] / income_sum * 100) if income_sum > 0 else 0.0
        color_encoding = alt.Color("Allocation:N", title="Asset Class", scale=alt.Scale(scheme='tableau10'))

        pie_value = alt.Chart(chart_df).mark_arc(innerRadius=60).encode(
            theta=alt.Theta(field="MarketValue", type="quantitative"),
            color=color_encoding,
            tooltip=[
                alt.Tooltip('Allocation:N', title='Asset Class'),
                alt.Tooltip('Allocation %:Q', title='Weight (%)', format='.2f'),
                alt.Tooltip('MarketValue:Q', title='Value ($)', format=',.0f')
            ]
        ).properties(title="Portfolio Weight (Asset Value)", height=280, width=280)

        pie_income = alt.Chart(chart_df).mark_arc(innerRadius=60).encode(
            theta=alt.Theta(field="AnnualIncome", type="quantitative"),
            color=color_encoding,
            tooltip=[
                alt.Tooltip('Allocation:N', title='Asset Class'),
                alt.Tooltip('IncomePct:Q', title='Income Share (%)', format='.2f'),
                alt.Tooltip('AnnualIncome:Q', title='Value ($)', format=',.0f')
            ]
        ).properties(title="Income Contribution (Dividends)", height=280, width=280)

        # Build horizontal allocation map
        combined_charts = (pie_value | pie_income).resolve_scale(color='shared').properties(spacing=60)
        
        # Place chart into container-fitting column layout
        chart_layout = st.columns([1, 0.4])
        with chart_layout[0]:
            st.altair_chart(combined_charts, width='stretch')

        st.write("")
        with st.expander("🔍 Click to view detailed holding allocation sorted by Account Type"):
            acct_df = master_df.groupby(['AccountType', 'AccountName', 'Allocation']).agg({
                'MarketValue': 'sum',
                'Gain': 'sum',
                'AnnualIncome': 'sum'
            }).reset_index()

            acct_df['Div Rate'] = (acct_df['AnnualIncome'] / acct_df['MarketValue'] * 100).fillna(0)
            acct_df['% of Portfolio'] = (acct_df['MarketValue'] / total_mv * 100) if total_mv > 0 else 0.0

            st.dataframe(
                acct_df, width='stretch', hide_index=True,
                column_config={
                    'MarketValue': dollar_col('MarketValue'),
                    'Gain': dollar_col('Gain'),
                    'AnnualIncome': dollar_col('AnnualIncome'),
                    'Div Rate': percent_col('Div Rate'),
                    '% of Portfolio': percent_col('% of Portfolio'),
                }
            )

        # ======================================================================
        # MULTI-YEAR PORTFOLIO SUMMARY (per-account Ending Value/Cash Flow/Gain/Return)
        # ======================================================================
        st.markdown("---")
        st.subheader("📈 Multi-Year Portfolio Summary")

        history_sheets, history_years = load_historical_portfolio_data()

        if history_years:
            recent_years = history_years[-5:] if len(history_years) >= 5 else history_years
            latest_year = history_years[-1]
            latest_year_col = str(latest_year)

            def latest_total(sheet_name, label):
                df = history_sheets.get(sheet_name)
                if df is None or latest_year_col not in df.columns:
                    return None
                row = df.loc[df['Account'] == label]
                if row.empty:
                    return None
                val = row.iloc[0][latest_year_col]
                return val if pd.notna(val) else None

            grand_end = latest_total('EndingValue', 'Grand Total')
            grand_cf = latest_total('CashFlow', 'Grand Total')
            grand_gain = latest_total('Gain', 'Grand Total')
            grand_ret = latest_total('Return', 'Grand Total')

            period_label = "Year-to-Date" if latest_year == datetime.now().year else "Full Year"
            st.caption(f"{period_label} — {latest_year} (Grand Total across all accounts)")

            col_h1, col_h2, col_h3, col_h4 = st.columns(4)
            col_h1.metric("Ending Value", f"${grand_end:,.0f}" if grand_end is not None else "N/A")
            cf_prefix = "+" if (grand_cf is not None and grand_cf >= 0) else ""
            col_h2.metric("Cash Flow (Net)", f"{cf_prefix}${grand_cf:,.0f}" if grand_cf is not None else "N/A")
            gain_prefix2 = "+" if (grand_gain is not None and grand_gain >= 0) else ""
            col_h3.metric("Market Gain", f"{gain_prefix2}${grand_gain:,.0f}" if grand_gain is not None else "N/A")
            col_h4.metric("Return", f"{grand_ret * 100:,.2f}%" if grand_ret is not None else "N/A")

            def grand_all_years(sheet_name):
                df = history_sheets.get(sheet_name)
                if df is None:
                    return {}
                row = df.loc[df['Account'] == 'Grand Total']
                if row.empty:
                    return {}
                row = row.iloc[0]
                return {y: row.get(str(y)) for y in history_years if pd.notna(row.get(str(y)))}

            gain_by_year = grand_all_years('Gain')
            cf_by_year = grand_all_years('CashFlow')
            ret_by_year = grand_all_years('Return')

            total_gain_all = sum(gain_by_year.values()) if gain_by_year else None
            total_cf_all = sum(cf_by_year.values()) if cf_by_year else None

            annualized_return = None
            if ret_by_year:
                growth_factor = 1.0
                for r in ret_by_year.values():
                    growth_factor *= (1 + r)
                if growth_factor > 0:
                    annualized_return = growth_factor ** (1.0 / len(ret_by_year)) - 1

            gain_share = (total_gain_all / grand_end * 100) if (total_gain_all is not None and grand_end) else None

            st.caption(f"Since {history_years[0]} ({len(ret_by_year)} years of tracked returns, Grand Total)")
            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
            col_s1.metric("Total Gain", f"${total_gain_all:,.0f}" if total_gain_all is not None else "N/A")
            cf_prefix_all = "+" if (total_cf_all is not None and total_cf_all >= 0) else ""
            col_s2.metric("Total Cash Flow", f"{cf_prefix_all}${total_cf_all:,.0f}" if total_cf_all is not None else "N/A")
            col_s3.metric("Gain Share of Portfolio", f"{gain_share:,.1f}%" if gain_share is not None else "N/A")
            col_s4.metric("Annualized Return", f"{annualized_return * 100:,.2f}%" if annualized_return is not None else "N/A")

            def select_metric_df(df, years):
                cols = ['Account'] + [str(y) for y in years]
                cols = [c for c in cols if c in df.columns]
                return df[cols]

            def year_column_config(years, as_percent=False):
                # Return sheet stores fractions (0.12 == 12%); everything else is dollars.
                col_fn = fraction_percent_col if as_percent else dollar_col
                return {str(y): col_fn(str(y)) for y in years}

            sub_tab_end, sub_tab_cf, sub_tab_gr = st.tabs(
                ["🏦 Ending Value", "💵 Cash Flow", "📈 Gain & Return"]
            )

            with sub_tab_end:
                df = history_sheets.get('EndingValue')
                if df is not None:
                    st.dataframe(select_metric_df(df, recent_years), width='stretch', hide_index=True,
                                 column_config=year_column_config(recent_years))
                    with st.expander(f"View full history since {history_years[0]}"):
                        st.dataframe(select_metric_df(df, history_years), width='stretch', hide_index=True,
                                     column_config=year_column_config(history_years))

            with sub_tab_cf:
                df = history_sheets.get('CashFlow')
                if df is not None:
                    st.caption("Per-account detail is only tracked from the year this feature went live "
                               "onward — earlier years show group totals only.")
                    st.dataframe(select_metric_df(df, recent_years), width='stretch', hide_index=True,
                                 column_config=year_column_config(recent_years))
                    with st.expander(f"View full history since {history_years[0]}"):
                        st.dataframe(select_metric_df(df, history_years), width='stretch', hide_index=True,
                                     column_config=year_column_config(history_years))

            with sub_tab_gr:
                df_gain = history_sheets.get('Gain')
                df_ret = history_sheets.get('Return')
                st.markdown("###### Gain ($)")
                if df_gain is not None:
                    st.dataframe(select_metric_df(df_gain, recent_years), width='stretch', hide_index=True,
                                 column_config=year_column_config(recent_years))
                st.markdown("###### Return (%)")
                if df_ret is not None:
                    st.dataframe(select_metric_df(df_ret, recent_years), width='stretch', hide_index=True,
                                 column_config=year_column_config(recent_years, as_percent=True))
                with st.expander(f"View full history since {history_years[0]}"):
                    st.markdown("###### Gain ($)")
                    if df_gain is not None:
                        st.dataframe(select_metric_df(df_gain, history_years), width='stretch', hide_index=True,
                                     column_config=year_column_config(history_years))
                    st.markdown("###### Return (%)")
                    if df_ret is not None:
                        st.dataframe(select_metric_df(df_ret, history_years), width='stretch', hide_index=True,
                                     column_config=year_column_config(history_years, as_percent=True))

            st.markdown("---")

            def grand_row_series(sheet_name):
                df = history_sheets.get(sheet_name)
                if df is None:
                    return None
                row = df.loc[df['Account'] == 'Grand Total']
                return row.iloc[0] if not row.empty else None

            end_row = grand_row_series('EndingValue')
            cf_row = grand_row_series('CashFlow')
            gain_row = grand_row_series('Gain')

            trend_df = pd.DataFrame({
                'Year': [str(y) for y in history_years],
                'EndingValue': [end_row.get(str(y)) if end_row is not None else None for y in history_years],
            }).dropna(subset=['EndingValue'])

            flow_gain_df = pd.DataFrame({
                'Year': [str(y) for y in history_years],
                'Cash Flow': [cf_row.get(str(y)) if cf_row is not None else None for y in history_years],
                'Market Gain': [gain_row.get(str(y)) if gain_row is not None else None for y in history_years],
            }).dropna(subset=['Cash Flow', 'Market Gain'], how='all')

            hist_cols2 = st.columns([1, 1])
            with hist_cols2[0]:
                st.markdown("#### Portfolio Value Over Time")
                if not trend_df.empty:
                    line_chart2 = alt.Chart(trend_df).mark_line(point=True, color='#0d6efd').encode(
                        x=alt.X('Year:N', title='Year'),
                        y=alt.Y('EndingValue:Q', title='Ending Value ($)'),
                        tooltip=['Year:N', alt.Tooltip('EndingValue:Q', format=',.0f')]
                    ).properties(height=280, width=420)
                    st.altair_chart(line_chart2, width='stretch')

            with hist_cols2[1]:
                st.markdown("#### Cash Flow vs. Market Gain")
                if not flow_gain_df.empty:
                    melted_flow_df = pd.melt(
                        flow_gain_df, id_vars=['Year'], value_vars=['Cash Flow', 'Market Gain'],
                        var_name='Type', value_name='Amount'
                    )
                    stacked_bar = alt.Chart(melted_flow_df).mark_bar().encode(
                        x=alt.X('Year:N', title='Year'),
                        y=alt.Y('Amount:Q', title='Value ($)'),
                        color=alt.Color('Type:N', scale=alt.Scale(
                            domain=['Cash Flow', 'Market Gain'], range=['#0d6efd', '#2ca02c'])),
                        tooltip=['Year:N', 'Type:N', alt.Tooltip('Amount:Q', format=',.0f')]
                    ).properties(height=280, width=420)
                    st.altair_chart(stacked_bar, width='stretch')

            # Account-mapping alerts from the last portfolio_history_backend.py run - the
            # sidebar button only shows a generic success message, so this is otherwise
            # console-only output.
            portfolio_alerts = parse_portfolio_history_alerts()
            if portfolio_alerts and not portfolio_alerts.get("error") and (
                portfolio_alerts['skipped'] or portfolio_alerts['unmapped_investment'] or portfolio_alerts['other']
            ):
                st.markdown("### 🚨 Portfolio History Alerts")
                st.caption(f"Log generated from run file dating: {portfolio_alerts.get('file_date', 'N/A')}")

                if portfolio_alerts['skipped']:
                    with st.expander("⚠️ Accounts Skipped (not in account_mapping.csv)"):
                        st.markdown(
                            f"""<div class="alert-card alert-danger" style="margin: 0; padding: 10px;">
                            These accounts showed up in the latest portfolio snapshot but aren't in
                            <code>account_mapping.csv</code>, so they were skipped this run.
                            <ul style="margin-top: 5px; margin-bottom: 0;">
                                {"".join([f"<li>{a}</li>" for a in portfolio_alerts['skipped']])}
                            </ul>
                            </div>""", unsafe_allow_html=True
                        )

                if portfolio_alerts['unmapped_investment']:
                    with st.expander("📋 Possible Unmapped Investment Accounts"):
                        st.markdown(
                            f"""<div class="alert-card alert-warning" style="margin: 0; padding: 10px;">
                            These Empower accounts look investment-related but aren't in
                            <code>account_mapping.csv</code> - check if any should be added.
                            <ul style="margin-top: 5px; margin-bottom: 0;">
                                {"".join([f"<li>{a}</li>" for a in portfolio_alerts['unmapped_investment']])}
                            </ul>
                            </div>""", unsafe_allow_html=True
                        )

                if portfolio_alerts['other']:
                    with st.expander("🔎 Other Warnings"):
                        st.markdown(
                            f"""<div class="alert-card alert-warning" style="margin: 0; padding: 10px;">
                            <ul style="margin-top: 5px; margin-bottom: 0;">
                                {"".join([f"<li>{a}</li>" for a in portfolio_alerts['other']])}
                            </ul>
                            </div>""", unsafe_allow_html=True
                        )
        else:
            st.info("No multi-year portfolio history found. Run `python portfolio_history_backend.py --seed` "
                     "once to import your historical tracking sheet, then use the sidebar's "
                     "'Update Portfolio History' button to keep it current.")

    else:
        st.info("No Portfolio summaries found in output folder. Drop brokerage exports into 'financial_data/input/' and use sidebar to Sync.")


# ==============================================================================
# TAB 2: CASH FLOW & EXPENSE PRESENTATION
# ==============================================================================
with tab_exp:
    # 1. Load historical budget sheet numbers
    df_exp, df_inc, historical_years, tot_exp, tot_inc = load_historical_budget_data()
    
    if historical_years:
        # Create comparative timeline summary
        flow_summary = []
        for yr in historical_years:
            income_val = tot_inc.get(yr, 0.0)
            expense_val = tot_exp.get(yr, 0.0)
            net_val = income_val - expense_val
            savings_rate = (net_val / income_val * 100) if income_val > 0 else 0.0
            
            flow_summary.append({
                'Year': str(yr),
                'Total Income': income_val,
                'Total Expense': expense_val,
                'Net Cash Flow': net_val,
                'Savings Rate': savings_rate
            })
            
        summary_flow_df = pd.DataFrame(flow_summary)
        
        # Metric Overview of the most current processed year
        latest_yr_data = flow_summary[-1]
        st.subheader(f"📅 Year-to-Date Review: {latest_yr_data['Year']}")
        
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        col_f1.metric("Annual Income", f"${latest_yr_data['Total Income']:,.0f}")
        col_f2.metric("Annual Expenses", f"${latest_yr_data['Total Expense']:,.0f}")
        
        net_flow = latest_yr_data['Net Cash Flow']
        net_prefix = "+" if net_flow >= 0 else ""
        col_f3.metric("Net Flow Amount", f"{net_prefix}${net_flow:,.0f}")
        col_f4.metric("Savings Ratio", f"{latest_yr_data['Savings Rate']:.2f}%")
        
        # Render clean categorical ledger tables
        st.markdown("---")
        st.subheader("📊 Dynamic Multi-Year Category Ledger")
        
        sub_tab_exp, sub_tab_inc = st.tabs(["💸 Expense Ledgers", "💵 Income Ledgers"])
        
        ledger_year_config = {str(yr): dollar_col(str(yr)) for yr in historical_years}

        with sub_tab_exp:
            if df_exp is not None and not df_exp.empty:
                st.dataframe(df_exp, width='stretch', hide_index=True, column_config=ledger_year_config)

        with sub_tab_inc:
            if df_inc is not None and not df_inc.empty:
                st.dataframe(df_inc, width='stretch', hide_index=True, column_config=ledger_year_config)

        # Convert list of summaries for side-by-side charting
        st.markdown("---")                
        
        flow_chart_df = pd.DataFrame({
            'Year': [s['Year'] for s in flow_summary],
            'Income': [s['Total Income'] for s in flow_summary],
            'Expense': [s['Total Expense'] for s in flow_summary],
            'Savings': [s['Net Cash Flow'] for s in flow_summary]
        })
        
        # Render historical flows
        hist_cols = st.columns([1, 1])
        
        with hist_cols[0]:
            st.markdown("#### Historical Cash Flow Trend (Last 5 Years)")
            melted_df = pd.melt(flow_chart_df, id_vars=['Year'], value_vars=['Income', 'Expense'], var_name='Type', value_name='Amount')
            
            bar_chart = alt.Chart(melted_df).mark_bar().encode(
                x=alt.X('Year:N', title='Budget Year'),
                y=alt.Y('Amount:Q', title='Value ($)'),
                color=alt.Color('Type:N', scale=alt.Scale(domain=['Income', 'Expense'], range=['#2ca02c', '#d62728'])),
                xOffset='Type:N'
            ).properties(height=280, width=420)
            
            st.altair_chart(bar_chart, width='stretch')
            
        with hist_cols[1]:
            st.markdown("#### Annual Net Wealth Savings Rate")
            line_chart = alt.Chart(flow_chart_df).mark_line(point=True, color='#0d6efd').encode(
                x=alt.X('Year:N', title='Budget Year'),
                y=alt.Y('Savings:Q', title='Savings Generated ($)'),
                tooltip=['Year:N', alt.Tooltip('Savings:Q', format=',.2f')]
            ).properties(height=280, width=420)
            
            st.altair_chart(line_chart, width='stretch')
                    
        # Alerts & Action items block
        alerts_data = parse_alerts_log()
        
        if alerts_data and (alerts_data['unmapped'] or alerts_data['others'] or alerts_data['abnormal']):
            st.markdown("### 🚨 Transaction Alerts & Operations Center")
            st.caption(f"Log generated from run file dating: {alerts_data.get('file_date', 'N/A')}")
            
            # Unmapped warnings
            if alerts_data['unmapped']:
                with st.expander("⚠️ Unmapped Transaction Categories Found"):                
                    st.markdown(
                        f"""<div class="alert-card alert-danger" style="margin: 0; padding: 10px;">
                        These categories exist in your statements but are missing in <code>category_type_mapping.csv</code>.
                        They are tagged <strong>UNMAPPED</strong>. Please update mapping table and recalculate.
                        <ul style="margin-top: 5px; margin-bottom: 0;">
                            {"".join([f"<li>{cat}</li>" for cat in alerts_data['unmapped']])}
                        </ul>
                        </div>""", unsafe_allow_html=True
                    )
                
            # Category others rollover warnings
            if alerts_data['others']:
                with st.expander("📋 Categories Rolled into 'Others'"):
                    st.markdown(
                        f"""<div class="alert-card alert-warning" style="margin: 0; padding: 10px;">
                        These budget categories don't have matching line items in <code>Yearly_budget.xlsx</code> 
                        and lack an alias mapping configuration, defaulting to 'Others'. Create row entries or custom aliases.
                        <ul style="margin-top: 5px; margin-bottom: 0;">
                            {"".join([f"<li>{cat}</li>" for cat in alerts_data['others']])}
                        </ul>
                        </div>""", unsafe_allow_html=True
                    )
                
            # Outliers detector
            if alerts_data['abnormal']:
                with st.expander("🔎 Unusual / Abnormal Transaction Outliers Flagged"):
                    st.markdown(
                        f"""<div class="alert-card alert-warning" style="margin: 0; padding: 10px;">
                        These transactions appear to be outliers and are flagged to study further.
                        <ul style="margin-top: 5px; margin-bottom: 0;">
                            {"".join([f"<li>{cat}</li>" for cat in alerts_data['abnormal']])}
                        </ul>
                        </div>""", unsafe_allow_html=True
                    )
        else:
            st.success("✨ Zero unmapped categories or extreme transaction anomalies flagged this cycle!")
    else:
        st.info("No transaction spreadsheets found in outputs. Drop transactions CSV and budget templates into 'expense_data/input/' to start calculation workflows.")
