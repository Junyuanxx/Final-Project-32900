"""
Table02Prep.py

Generates Table 02 for the intermediary asset pricing replication. It reads manually curated datasets,
fetches WRDS data for primary dealers and comparison groups, aggregates the data, and produces a final
LaTeX output. This file no longer contains testing code; see Table02_testing.py for relevant tests.
"""

import warnings
warnings.filterwarnings(
    "ignore",
    message=".*DataFrame concatenation with empty or all-NA entries.*",
    category=FutureWarning
)
import pandas as pd
import wrds
import config
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import Table02Analysis
from pathlib import Path

#---------------------------------------------------------------------
# Function: fetch_financial_data
# Description: Fetches quarterly financial data (assets, debt, equity, etc.) from WRDS for given gvkeys.
# If ITERATE=True, each gvkey uses its own start/end dates; otherwise a global date range.
# Returns a DataFrame of combined results.
#---------------------------------------------------------------------
def fetch_financial_data(db, linktable, start_date, end_date, ITERATE=False):
    pgvkeys = linktable['gvkey'].tolist()
    results = pd.DataFrame()

    if ITERATE:
        start_dates = linktable['Start Date'].tolist()
        end_dates_list = linktable['End Date'].tolist()

        # Convert global end_date (assumed in YYYY-MM-DD format) to m/d/YYYY format
        end_date_parts = end_date.split("-")
        end_date_mdy = f"{end_date_parts[1]}/{end_date_parts[2]}/{end_date_parts[0]}"
        end_dates_list = [end_date_mdy if d == 'Current' else d for d in end_dates_list]

        start_dates_dt, end_dates_dt = [], []
        for date in start_dates:
            if len(date.split('/')[-1]) == 4:
                start_dates_dt.append(datetime.strptime(date, '%m/%d/%Y'))
            else:
                start_dates_dt.append(datetime.strptime(date, '%m/%d/%y'))

        for date in end_dates_list:
            if len(date.split('/')[-1]) == 4:
                end_dates_dt.append(datetime.strptime(date, '%m/%d/%Y'))
            else:
                end_dates_dt.append(datetime.strptime(date, '%m/%d/%y'))

        for i, gvkey in enumerate(pgvkeys):
            pgvkey_str = f"'{str(gvkey).zfill(6)}'"
            query = f"""
            SELECT datadate,
                   CASE WHEN atq IS NULL OR atq = 0 THEN actq ELSE atq END AS total_assets,
                   CASE WHEN ltq IS NULL OR ltq = 0 THEN lctq ELSE ltq END AS book_debt,
                   COALESCE(teqq, ceqq + COALESCE(pstkq, 0) + COALESCE(mibnq, 0)) AS book_equity,
                   cshoq*prccq AS market_equity, gvkey, conm
            FROM comp.fundq AS cst
            WHERE cst.gvkey={pgvkey_str}
              AND cst.datadate BETWEEN '{start_dates_dt[i]}' AND '{end_dates_dt[i]}'
              AND indfmt='INDL'
              AND datafmt='STD'
              AND popsrc='D'
              AND consol='C'
            """
            data = db.raw_sql(query)
            if not data.empty:
                results = pd.concat([results, data], axis=0)
    else:
        pgvkey_str = ','.join([f"'{str(key).zfill(6)}'" for key in pgvkeys])
        query = f"""
        SELECT datadate,
               CASE WHEN atq IS NULL OR atq = 0 THEN actq ELSE atq END AS total_assets,
               CASE WHEN ltq IS NULL OR ltq = 0 THEN lctq ELSE ltq END AS book_debt,
               COALESCE(teqq, ceqq + COALESCE(pstkq, 0) + COALESCE(mibnq, 0)) AS book_equity,
               cshoq*prccq AS market_equity, gvkey, conm
        FROM comp.fundq AS cst
        WHERE cst.gvkey IN ({pgvkey_str})
          AND cst.datadate BETWEEN '{start_date}' AND '{end_date}'
          AND indfmt='INDL'
          AND datafmt='STD'
          AND popsrc='D'
          AND consol='C'
        """
        data = db.raw_sql(query)
        if not data.empty:
            results = pd.concat([results, data], axis=0)

    return results

#---------------------------------------------------------------------
# Function: get_comparison_group_data
# Description: A simple wrapper for fetch_financial_data.
# Returns data for a given subset (linktable_df) within the specified date range.
#---------------------------------------------------------------------
def get_comparison_group_data(db, linktable_df, start_date, end_date, ITERATE=False):
    return fetch_financial_data(db, linktable_df, start_date, end_date, ITERATE=ITERATE)

#---------------------------------------------------------------------
# Function: read_in_manual_datasets
# Description: Reads local CSV files: ticks.csv and updated_linktable.csv.
# Cleans up 'gvkey' and 'Permco' to be integers.
# Modified to use Python engine, skip bad lines, and disable quoting to avoid EOF errors.
#---------------------------------------------------------------------
def read_in_manual_datasets():
    script_dir = Path(__file__).resolve().parent
    manual_dir = (script_dir / "../data_manual").resolve()
    ticks_csv = manual_dir / 'ticks.csv'
    link_csv = manual_dir / 'updated_linktable.csv'

    # Use engine="python", quoting=3 (csv.QUOTE_NONE) and on_bad_lines='skip' to handle malformed lines
    ticks = pd.read_csv(ticks_csv, sep="|", engine="python", quoting=3, on_bad_lines='skip')
    ticks['gvkey'] = ticks['gvkey'].fillna(0.0).astype(int)
    ticks['Permco'] = ticks['Permco'].fillna(0.0).astype(int)
    linktable = pd.read_csv(link_csv)
    return ticks, linktable

#---------------------------------------------------------------------
# Function: pull_CRSP_Comp_Link_Table
# Description: Pulls the CRSP-Compustat Link Table from WRDS to cross-reference gvkeys and permco.
#---------------------------------------------------------------------
def pull_CRSP_Comp_Link_Table():
    sql_query = """
        SELECT 
            gvkey, lpermco AS permco, linktype, linkprim, linkdt, linkenddt, tic
        FROM 
            ccmlinktable
        WHERE 
            substr(linktype,1,1)='L' 
            AND (linkprim ='C' OR linkprim='P')
    """
    db = wrds.Connection(wrds_username=config.WRDS_USERNAME)
    ccm = db.raw_sql(sql_query, date_cols=["linkdt", "linkenddt"])
    db.close()
    return ccm

#---------------------------------------------------------------------
# Function: prim_deal_merge_manual_data_w_linktable
# Description: Merges manually read ticks.csv info with link_hist. 
# If UPDATED=False, restrict link_hist to fyear <= 2012.
#---------------------------------------------------------------------
def prim_deal_merge_manual_data_w_linktable(UPDATED=False):
    main_dataset, link_hist = read_in_manual_datasets()
    if not UPDATED:
        link_hist = link_hist[link_hist['fyear'] <= 2012]
    merged_main = pd.merge(main_dataset, link_hist, left_on='gvkey', right_on='GVKEY')
    merged_main = merged_main[['gvkey','conm','sic','Start Date','End Date']].drop_duplicates()
    link_hist.rename(columns={'GVKEY':'gvkey'}, inplace=True)
    return merged_main, link_hist

#---------------------------------------------------------------------
# Function: create_comparison_group_linktables
# Description: Splits link_hist into BD, Banks, Cmpust. groups, while PD is from merged_main.
# Returns a dict containing these four link table subsets.
#---------------------------------------------------------------------
def create_comparison_group_linktables(link_hist, merged_main):
    linked_bd_less_pd = link_hist[((link_hist['sic'] == 6211) | (link_hist['sic'] == 6221))
                                  & (~link_hist['gvkey'].isin(merged_main['gvkey'].tolist()))]
    linked_banks_less_pd = link_hist[(link_hist['sic'].isin([6011, 6021, 6022, 6029, 6081, 6082, 6020]))
                                     & (~link_hist['gvkey'].isin(merged_main['gvkey'].tolist()))]
    linked_all_less_pd = link_hist[(~link_hist['gvkey'].isin(merged_main['gvkey'].tolist()))]

    return {
        "BD": linked_bd_less_pd,
        "Banks": linked_banks_less_pd,
        "Cmpust.": linked_all_less_pd,
        "PD": merged_main
    }

#---------------------------------------------------------------------
# Function: pull_data_for_all_comparison_groups
# Description: Iterates over each group (BD, Banks, Cmpust., PD), fetches data, returns a dict of DataFrames.
#---------------------------------------------------------------------
def pull_data_for_all_comparison_groups(db, comparison_group_dict, UPDATED=False):
    datasets = {}
    for key, linktable in comparison_group_dict.items():
        ITERATE = (key == 'PD')
        if not UPDATED:
            ds = get_comparison_group_data(db, linktable, config.START_DATE, config.END_DATE, ITERATE=ITERATE)
        else:
            if pd.to_datetime(config.UPDATED_END_DATE) > datetime.now():
                UPDATED_END_DATE = datetime.now().strftime('%Y-%m-%d')
            else:
                UPDATED_END_DATE = config.UPDATED_END_DATE
            ds = get_comparison_group_data(db, linktable, config.START_DATE, UPDATED_END_DATE, ITERATE=ITERATE)
        datasets[key] = ds.drop_duplicates()
    return datasets

#---------------------------------------------------------------------
# Function: prep_datasets
# Description: Converts datadate to a quarterly period, sums up key columns by quarter, 
# fills missing numeric values with column means. 
# Returns dict of aggregated data for each group.
#---------------------------------------------------------------------
def prep_datasets(datasets):
    prepped_datasets = {}
    key_cols = ['total_assets', 'book_debt', 'book_equity', 'market_equity']
    for group_name, df in datasets.items():
        if 'datadate' in df.columns:
            df['datadate'] = pd.to_datetime(df['datadate'])
            df['datadate'] = df['datadate'].dt.to_period('Q').dt.to_timestamp()
            for col in key_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df[key_cols] = df[key_cols].fillna(df[key_cols].mean())
        else:
            print(f"'datadate' column not found for group {group_name}")
        grouped = df.groupby('datadate').agg({c: 'sum' for c in key_cols}).reset_index()
        prepped_datasets[group_name] = grouped
    return prepped_datasets

#---------------------------------------------------------------------
# Function: create_ratios_for_table
# Description: Build ratio columns (PD metric / (PD metric + group metric)) for each group, 
# over selected sample periods. Returns a combined DataFrame.
#---------------------------------------------------------------------
def create_ratios_for_table(prepped_datasets, UPDATED=False):
    if not UPDATED:
        sample_periods = [
            ('1960-01-01', '2012-12-31'),
            ('1960-01-01', '1990-12-31'),
            ('1990-01-01', '2012-12-31')
        ]
    else:
        sample_periods = [
            ('1960-01-01', '2025-01-01'),
            ('1960-01-01', '1990-12-31'),
            ('1990-01-01', '2025-01-01')
        ]

    pd_df = prepped_datasets['PD']
    pd_df['datadate'] = pd.to_datetime(pd_df['datadate'])
    pd_df.index = pd_df['datadate']

    ratio_frames = {}
    filtered_pd = {}
    for period in sample_periods:
        start_date, end_date = map(lambda d: datetime.strptime(d, '%Y-%m-%d'), period)
        sub = pd_df.copy()[start_date: end_date]
        ratio_frames[period] = pd.DataFrame(index=sub.index)
        filtered_pd[period] = sub

    for grp_name, grp_df in prepped_datasets.items():
        if grp_name == 'PD':
            continue
        grp_df['datadate'] = pd.to_datetime(grp_df['datadate'])
        grp_df.index = grp_df['datadate']
        for period in sample_periods:
            start_date, end_date = period
            sub = grp_df[start_date:end_date]
            for c in ['total_assets', 'book_debt', 'book_equity', 'market_equity']:
                sum_col = filtered_pd[period][c] + sub[c]
                sum_col = sum_col.replace(0, np.nan)
                ratio_frames[period][f'{c}_{grp_name}'] = filtered_pd[period][c] / sum_col

    combined = pd.DataFrame()
    for period, df in ratio_frames.items():
        start_date, end_date = map(lambda d: datetime.strptime(d, '%Y-%m-%d'), period)
        df['Period'] = f"{start_date.year}-{end_date.year}"
        combined = pd.concat([combined, df])
    return combined

#---------------------------------------------------------------------
# Function: format_final_table
# Description: Takes the ratio DataFrame, groups by Period, reorders columns with MultiIndex, 
# then returns the final pivoted table.
#---------------------------------------------------------------------
def format_final_table(table, UPDATED=False):
    table = table.groupby('Period').mean()
    all_cols = [
        'total_assets_BD','total_assets_Banks','total_assets_Cmpust.',
        'book_debt_BD','book_debt_Banks','book_debt_Cmpust.',
        'book_equity_BD','book_equity_Banks','book_equity_Cmpust.',
        'market_equity_BD','market_equity_Banks','market_equity_Cmpust.'
    ]
    grouped_table = table[all_cols]
    grouped_table = grouped_table.reset_index()

    mapping = {
        'total_assets_Banks':('Total assets','Banks'),
        'total_assets_BD':('Total assets','BD'),
        'total_assets_Cmpust.':('Total assets','Cmpust.'),
        'book_debt_Banks':('Book debt','Banks'),
        'book_debt_BD':('Book debt','BD'),
        'book_debt_Cmpust.':('Book debt','Cmpust.'),
        'book_equity_Banks':('Book equity','Banks'),
        'book_equity_BD':('Book equity','BD'),
        'book_equity_Cmpust.':('Book equity','Cmpust.'),
        'market_equity_Banks':('Market equity','Banks'),
        'market_equity_BD':('Market equity','BD'),
        'market_equity_Cmpust.':('Market equity','Cmpust.')
    }
    import pandas as pd
    multiindex = pd.MultiIndex.from_tuples(
        [mapping[c] for c in all_cols],
        names=['Metric','Source']
    )
    final_df = pd.DataFrame(
        grouped_table.drop('Period', axis=1).values,
        index=grouped_table['Period'],
        columns=multiindex
    )

    if UPDATED:
        new_order = ['1960-2025','1960-1990','1990-2025']
    else:
        new_order = ['1960-2012','1960-1990','1990-2012']
    final_df = final_df.reindex(new_order)
    return final_df

#---------------------------------------------------------------------
# Function: convert_and_export_table_to_latex
# Description: Exports the final ratio table as LaTeX, writing to the config.OUTPUT_DIR.
#---------------------------------------------------------------------
def convert_and_export_table_to_latex(formatted_table, UPDATED=False):
    latex = formatted_table.to_latex(index=True, column_format='lcccccccccccc', float_format="%.3f")

    if UPDATED:
        caption = "Original"
        fname = "updated_table02.tex"
    else:
        caption = "Updated"
        fname = "table02.tex"

    wrapper = rf"""
    \begin{{table}}[htbp]
    \centering
    \caption{{{caption}}}
    \label{{tab:Table 2}}
    \small
    {latex}
    \end{{table}}
    """
    outpath = config.OUTPUT_DIR / fname
    with open(outpath, 'w', encoding='utf-8') as f:
        f.write(wrapper)
    print(f"Table 02 LaTeX saved to: {outpath}")

#---------------------------------------------------------------------
# Main function
# Description: Main pipeline for generating Table 2.
# 1) Connect to WRDS
# 2) Merge main dataset with link table
# 3) Pull data for each group
# 4) Prep data by quarter
# 5) Analysis: summary stats, ratio figure, correlation matrix
# 6) Export final table
#---------------------------------------------------------------------
def main(UPDATED=False):
    db = wrds.Connection(wrds_username=config.WRDS_USERNAME)
    merged_main, link_hist = prim_deal_merge_manual_data_w_linktable(UPDATED=UPDATED)
    group_links = create_comparison_group_linktables(link_hist, merged_main)

    ds = pull_data_for_all_comparison_groups(db, group_links, UPDATED=UPDATED)
    pds = prep_datasets(ds)

    Table02Analysis.create_summary_stat_table_for_data(ds, UPDATED=UPDATED)
    ratio_df = create_ratios_for_table(pds, UPDATED=UPDATED)
    Table02Analysis.create_figure_for_data(ratio_df, UPDATED=UPDATED)
    # correlation across same metrics with PD, BD, Banks, Cmpust.
    Table02Analysis.create_corr_matrix_for_data(ds, UPDATED=UPDATED)
    formatted = format_final_table(ratio_df, UPDATED=UPDATED)
    convert_and_export_table_to_latex(formatted, UPDATED=UPDATED)
    return formatted

if __name__ == "__main__":
    main(UPDATED=False)
