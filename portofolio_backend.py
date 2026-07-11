import pandas as pd
import glob
import os
from datetime import datetime
import io

def parse_schwab_multi_account_csv(filepath):
    all_data = []
    current_account = None
    
    with open(filepath, 'r') as f:
        block_lines = []
        for line in f:
            line = line.strip()
            
            if line and not line.startswith('"') and '...' in line:
                current_account = line.split('...')[0].strip()
                continue
            
            if '"Symbol"' in line:
                block_lines = [line]
                continue
                
            if current_account and line.startswith('"'):
                block_lines.append(line)
                
            if not line and block_lines:
                df_block = pd.read_csv(io.StringIO("\n".join(block_lines)))
                df_block['AccountName'] = current_account
                all_data.append(df_block)
                block_lines = []
                
        if block_lines:
            df_block = pd.read_csv(io.StringIO("\n".join(block_lines)))
            df_block['AccountName'] = current_account
            all_data.append(df_block)
            
    return pd.concat(all_data, ignore_index=True)

def parse_fidelity_positions_csv(filepath):
    """
    Parses a Fidelity positions export matching the exact CSV structure provided.
    Enforces index_col=False to prevent column-shifting errors from trailing commas.
    """
    # Enforce index_col=False so columns match up perfectly with their real values
    df = pd.read_csv(filepath, index_col=False)
    
    # Clean up column names right away (remove whitespace, quotes)
    df.columns = df.columns.str.strip().str.replace('"', '').str.replace("'", "")
    
    # Locate the Account Name column dynamically if a casing mismatch occurs
    account_col = next((col for col in df.columns if 'account name' in col.lower()), None)
    
    if not account_col:
        print("Expected an 'Account name' column but found:", df.columns.tolist())
        return pd.DataFrame(columns=['Symbol', 'Description', 'Quantity', 'MarketValue', 'CostBasis', 'AccountName'])

    # Drop explicitly empty rows or footnote rows at the bottom
    df = df.dropna(subset=[account_col])
    df = df[~df[account_col].astype(str).str.contains(r'Total|Notes|\*', case=False, na=False)]
    
    # Target accounts to isolate
    target_accounts = ['cash management', 'fidelity bank account', 'guoqing ira - citi', 'jpmc pension']
    
    def matches_target(acc_name):
        acc_lower = str(acc_name).lower()
        return any(target in acc_lower for target in target_accounts)
        
    # Apply account filtering rules
    df = df[df[account_col].apply(matches_target)].copy()
    
    if df.empty:
        print(f"Note: Found the Fidelity file, but no rows matched your accounts: {target_accounts}")
        return pd.DataFrame(columns=['Symbol', 'Description', 'Quantity', 'MarketValue', 'CostBasis', 'AccountName'])
        
    # Standardize column mappings based on the specific raw headers provided
    standard_df = pd.DataFrame()
    standard_df['AccountName'] = df[account_col].str.strip()
    
    # Find columns dynamically by matching keywords
    symbol_col = next((c for c in df.columns if 'symbol' in c.lower()), 'Symbol')
    desc_col = next((c for c in df.columns if 'description' in c.lower()), 'Description')
    qty_col = next((c for c in df.columns if 'quantity' in c.lower()), 'Quantity')
    value_col = next((c for c in df.columns if 'current value' in c.lower() or 'value' in c.lower()), 'Current value')
    cost_basis_col = next((c for c in df.columns if 'cost basis total' in c.lower() or 'cost basis' in c.lower()), 'Cost basis total')
    
    # Extract asset identifiers
    standard_df['Symbol'] = df[symbol_col].fillna('CASH').astype(str).str.strip()
    standard_df.loc[standard_df['Symbol'] == '', 'Symbol'] = 'CASH'
    standard_df.loc[standard_df['Symbol'].str.contains(r'^\*\*$', na=False), 'Symbol'] = 'CASH'
    
    # Pull description text
    if desc_col in df.columns:
        standard_df['Description'] = df[desc_col].fillna('').astype(str).str.strip()
    else:
        standard_df['Description'] = standard_df['Symbol']
        
    # Map raw value strings
    standard_df['Quantity'] = df[qty_col] if qty_col in df.columns else 0.0
    standard_df['MarketValue'] = df[value_col] if value_col in df.columns else 0.0
    standard_df['CostBasis'] = df[cost_basis_col] if cost_basis_col in df.columns else 0.0
    
    # Clean up standard core cash designations to tidy up output rows
    standard_df.loc[standard_df['Symbol'].str.contains('Cash|Pending|Core|XX', case=False, na=False), 'Symbol'] = 'CASH'
    
    return standard_df

def estimate_fidelity_income(schwab_df, fidelity_df):
    """
    Estimates income for Fidelity assets using implied yields from Schwab data,
    and applies a flat 3% yield to all CASH entries.
    """
    # 1. Calculate implied yields from Schwab positions
    # (Group by Symbol first to handle tickers split across multiple Schwab accounts)
    schwab_grouped = schwab_df.groupby('Symbol').agg({
        'MarketValue': 'sum',
        'EstimatedAnnualIncome': 'sum' # Assumes this column exists in your processed Schwab df
    }).reset_index()
    
    yield_map = {}
    for _, row in schwab_grouped.iterrows():
        symbol = row['Symbol']
        mkt_val = row['MarketValue']
        income = row['EstimatedAnnualIncome']
        
        # Avoid division by zero
        if mkt_val > 0:
            yield_map[symbol] = income / mkt_val
        else:
            yield_map[symbol] = 0.0

    # 2. Apply yields to Fidelity rows to estimate income
    fidelity_df = fidelity_df.copy()
    fidelity_df['EstimatedAnnualIncome'] = 0.0
    
    for idx, row in fidelity_df.iterrows():
        symbol = row['Symbol']
        mkt_val = row['MarketValue']
        
        if symbol == 'CASH':
            # Apply 3% default rate for all cash positions
            fidelity_df.at[idx, 'EstimatedAnnualIncome'] = mkt_val * 0.03
        elif symbol in yield_map:
            # Mirror the exact yield from the matching Schwab asset
            fidelity_df.at[idx, 'EstimatedAnnualIncome'] = mkt_val * yield_map[symbol]
        else:
            # Fallback for any ticker not present in Schwab data
            fidelity_df.at[idx, 'EstimatedAnnualIncome'] = 0.0
            
    return fidelity_df

def clean_currency(value):
    if isinstance(value, str):
        value = value.strip()
        if value.startswith('(') and value.endswith(')'):
            value = '-' + value[1:-1]
        value = value.replace('$', '').replace(',', '')
        if not value or value == '-':
            return 0.0
    return pd.to_numeric(value, errors='coerce')

def run_portfolio_workflow():
    data_dir = 'financial_data/input' 
    output_dir = 'financial_data/output'
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    
    # 1. Locate the latest files
    schwab_files = glob.glob(os.path.join(data_dir, 'All-Accounts-Positions-*-*-*.csv'))
    income_files = glob.glob(os.path.join(data_dir, 'XXXX905_InvestmentIncome_*-*.CSV'))
    fidelity_files = glob.glob(os.path.join(data_dir, 'Portfolio_Positions*')) + glob.glob(os.path.join(data_dir, 'Portforlio_Positions*'))
    mapping_file = os.path.join(data_dir, 'asset_allocation_mapping.csv')
    
    if not (schwab_files and income_files):
        print("Error: Required Schwab or Income input files not found in directory.")
        return
        
    latest_schwab = max(schwab_files, key=os.path.getmtime)
    latest_income = max(income_files, key=os.path.getmtime)
    
    # 2. Process Schwab Positions
    schwab_pos = parse_schwab_multi_account_csv(latest_schwab)
    schwab_pos = schwab_pos[schwab_pos['Symbol'] != 'Positions Total']

    target_cols = ['Symbol', 'Description', 'Qty (Quantity)', 'Mkt Val (Market Value)', 'Cost Basis', 'AccountName']
    schwab_pos = schwab_pos[target_cols].copy()
    schwab_pos.columns = ['Symbol', 'Description', 'Quantity', 'MarketValue', 'CostBasis', 'AccountName']
    schwab_pos['Symbol'] = schwab_pos['Symbol'].replace('Cash & Cash Investments', 'CASH')

    # 3. Process Fidelity Positions (If file exists)
    fidelity_pos = pd.DataFrame()
    if fidelity_files:
        latest_fidelity = max(fidelity_files, key=os.path.getmtime)
        fidelity_pos = parse_fidelity_positions_csv(latest_fidelity)
    else:
        print("Note: No Fidelity file found matching 'Portfolio_Positions*'. Skipping Fidelity.")

    # Clean currencies right away so math functions work
    for col in ['Quantity', 'MarketValue', 'CostBasis']:
        schwab_pos[col] = schwab_pos[col].apply(clean_currency).fillna(0)
        if not fidelity_pos.empty:
            fidelity_pos[col] = fidelity_pos[col].apply(clean_currency).fillna(0)

    # 4. Process Income (Schwab Only)
    income_df = pd.read_csv(latest_income, skiprows=1)
    income_df['Symbol'] = income_df['Symbol'].replace(['NO NUMBER', 'NO NUMB'], 'CASH')
    
    income_df['Transaction Amount'] = pd.to_numeric(
        income_df['Transaction Amount'].replace(r'[\$,]', '', regex=True), 
        errors='coerce'
    ).fillna(0)
    
    income_df['Account Name'] = income_df['Account Name'].str.strip()
    
    account_income = income_df.groupby(['Symbol', 'Account Name'])['Transaction Amount'].sum().reset_index()
    account_income.columns = ['Symbol', 'AccountName', 'AnnualIncome']

    # Normalize Account Names for the Merge
    account_income['MergeKey'] = account_income['AccountName'].str.lower().str.replace(' ', '', regex=False).str.replace('_', '', regex=False)
    schwab_pos['MergeKey'] = schwab_pos['AccountName'].str.lower().str.replace(' ', '', regex=False).str.replace('_', '', regex=False)
    
    # Merge income onto Schwab data first
    schwab_master = pd.merge(
        account_income, 
        schwab_pos, 
        on=['Symbol', 'MergeKey'], 
        how='right'
    )
    schwab_master['AccountName'] = schwab_master['AccountName_y'].fillna(schwab_master['AccountName_x'])
    schwab_master = schwab_master.drop(columns=['MergeKey', 'AccountName_x', 'AccountName_y'])
    schwab_master['AnnualIncome'] = schwab_master['AnnualIncome'].fillna(0)

    # Estimate Fidelity income using the mapped Schwab yields
    if not fidelity_pos.empty:
        schwab_for_estimate = schwab_master.rename(columns={'AnnualIncome': 'EstimatedAnnualIncome'})
        fidelity_pos = estimate_fidelity_income(schwab_for_estimate, fidelity_pos)
        fidelity_pos = fidelity_pos.rename(columns={'EstimatedAnnualIncome': 'AnnualIncome'})
    else:
        fidelity_pos['AnnualIncome'] = 0.0

    # Combine Schwab master and Fidelity assets 
    master_df = pd.concat([schwab_master, fidelity_pos], ignore_index=True)

    # Aggregate cross-brokerage summary profiles neatly
    master_df = master_df.groupby(['Symbol', 'AccountName', 'Description']).agg({
        'Quantity': 'sum', 
        'MarketValue': 'sum', 
        'CostBasis': 'sum',
        'AnnualIncome': 'sum'
    }).reset_index()

    # Classify clean descriptive types 
    def determine_account_type(name):
        name_str = str(name).lower()
        if 'ira' in name_str: return 'IRA'
        if 'roth' in name_str: return 'Roth'
        if 'pension' in name_str: return 'Pension'
        if 'cash' in name_str or 'bank' in name_str: return 'Cash/Cash Equivalents'
        return 'Investment'

    master_df['AccountType'] = master_df['AccountName'].apply(determine_account_type)
    
    # --- INCORPORATE ASSET ALLOCATION MAPPING ---
    if os.path.exists(mapping_file):
        # Read allocation mapping (Assumes columns like 'Symbol' and 'Allocation')
        mapping_df = pd.read_csv(mapping_file)
        mapping_df.columns = mapping_df.columns.str.strip()
        mapping_df['Symbol'] = mapping_df['Symbol'].str.strip()
        
        # Merge allocation definitions based on Symbol
        master_df = pd.merge(master_df, mapping_df[['Symbol', 'Allocation']], on='Symbol', how='left')
    else:
        print(f"Warning: Mapping file not found at {mapping_file}. Creating empty Allocation column.")
        master_df['Allocation'] = pd.NA
        
    # Apply override rule: force CASH symbols to be classified as Bond
    master_df.loc[master_df['Symbol'] == 'CASH', 'Allocation'] = 'bond'
    # Fill remaining unmatched assets with a default category if desired
    master_df['Allocation'] = master_df['Allocation'].fillna('Other/Unmapped')

    # Enforce standard presentation column alignment including Allocation
    final_cols = ['Symbol', 'Description', 'Allocation', 'AccountName', 'AccountType', 'Quantity', 'MarketValue', 'CostBasis', 'AnnualIncome']
    master_df = master_df[final_cols]
    
    # 5. Generate Output
    timestamp = datetime.now().strftime("%Y%m%d")
    master_summary_path = os.path.join(output_dir, f'Master_Account_Summary_{timestamp}.csv')
    
    master_df.to_csv(master_summary_path, index=False)
    print(f"Workflow complete. File generated: {master_summary_path}")    
if __name__ == "__main__":
    run_portfolio_workflow()