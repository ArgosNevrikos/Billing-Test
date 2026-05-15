import streamlit as st
import pandas as pd
from pymongo import MongoClient
from io import BytesIO
import matplotlib.pyplot as plt 
from fpdf import FPDF           
import numpy as np
import tempfile
import os
import certifi 

# --- DATABASE SETUP ---
@st.cache_resource
def init_connection():
    return MongoClient(
        "mongodb+srv://mykeltiu_db_user:Gu8suUJVihviPrjq@testing.kwm3vtx.mongodb.net/",
        tlsCAFile=certifi.where()
    )

client = init_connection()
db = client["spreadsheet_app"]
collection = db["sheets"]

# --- APP CONFIG ---
st.set_page_config(page_title="Billing Monitor Pro", layout="wide")
st.title("💼 Billing & Invoice Monitoring System")
st.markdown("Track invoices, manage payments, automate balances, and monitor your billing cycles efficiently.")

# --- FUNCTIONS ---
def auto_project_dates(df, date_cols_sequence, days_to_add=14):
    df_proj = df.copy()
    blank_indicators = ['', '-', '–', '—', 'nan', 'none', 'null', 'nat', 'to update']

    # CASCADE PROJECTION
    for i in range(len(date_cols_sequence) - 1):
        col_current = date_cols_sequence[i]
        col_next = date_cols_sequence[i+1]
        
        if col_current not in df_proj.columns or col_next not in df_proj.columns:
            continue
            
        # THE ULTIMATE FIX: Regex Extraction
        extracted_dates = df_proj[col_current].astype(str).str.extract(r'(\d{4}-\d{2}-\d{2})')[0]
        
        # Convert extracted text to dates so we can do math
        current_dates = pd.to_datetime(extracted_dates, errors='coerce')
        
        # Identify blank/missing cells in the NEXT column
        next_col_str = df_proj[col_next].astype(str).str.strip().str.lower()
        needs_update = df_proj[col_next].isna() | next_col_str.isin(blank_indicators)
        
        # Add the target days
        projected_dates = current_dates + pd.Timedelta(days=days_to_add)
        
        # Mask where we have a date to project FROM, and a blank cell to project TO
        mask = needs_update & current_dates.notna()
        
        # Apply the projection
        df_proj.loc[mask, col_next] = projected_dates[mask].dt.strftime('%Y-%m-%d') + " (Projected)"

    # FINAL CLEANUP: Erase any lingering 00:00:00 on untouched older dates
    for col in date_cols_sequence:
        if col in df_proj.columns:
            clean_strings = df_proj[col].astype(str).str.replace(" 00:00:00", "", regex=False)
            clean_strings = clean_strings.replace({'nan': '', 'NaT': '', 'None': '', 'nat': ''})
            df_proj[col] = clean_strings
            
    return df_proj

def fix_arrow_types(df):
    cols = df.select_dtypes(include=['object', 'string']).columns
    if not cols.empty:
        df[cols] = df[cols].astype(str)
    return df

@st.cache_data(show_spinner=False, ttl=5)
def load_sheet_names():
    return [doc["sheet_name"] for doc in collection.find({}, {"sheet_name": 1})]

@st.cache_data(show_spinner="Loading data from database...", ttl=5)
def get_sheet_data(name):
    doc = collection.find_one({"sheet_name": name})
    if not doc:
        return pd.DataFrame()
    
    df = pd.DataFrame(doc["data"])
    return fix_arrow_types(df)

def save_to_mongo(name, df):
    data = df.to_dict(orient="records")
    collection.update_one(
        {"sheet_name": name},
        {"$set": {"data": data}},
        upsert=True
    )
    st.cache_data.clear()
    st.success(f"Billing Ledger '{name}' saved successfully!")

# Updated signature to include title visibility and chart visibility options
def generate_pdf_report(df, sheet_name, label_col, expected_col, actual_col, pie_label_col, pie_val_col, pie_mode, summary_df, chart1_title, chart2_title, custom_exp_label, custom_act_label, main_pdf_title, show_main_title, show_chart1, show_chart2):
    pdf = FPDF()
    pdf.add_page()
    
    # --- REPORT HEADER ---
    if show_main_title:
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(190, 10, main_pdf_title, ln=True, align='C')
        pdf.ln(5)
    
    # --- EDITABLE SUMMARY METRICS ---
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(190, 10, "Financial Summary Totals:", ln=True)
    pdf.set_font("Arial", '', 11)
    
    for index, row in summary_df.iterrows():
        if row.get('Select for Deletion', False):
            continue
            
        m_name = str(row['Metric Name'])
        m_val = pd.to_numeric(row['Value'], errors='coerce')
        m_val = 0.0 if pd.isna(m_val) else m_val
            
        show_curr = row.get("Show ₱", True)
        curr_prefix = "PHP " if show_curr else ""
        
        pdf.cell(190, 8, f"- {m_name}: {curr_prefix}{m_val:,.2f}", ln=True)
        
    pdf.ln(5)
    
    # --- CHART 1: EXPECTED VS ACTUAL ---
    if show_chart1 and label_col in df.columns and (expected_col != "None" or actual_col != "None"):
        fig, ax = plt.subplots(figsize=(10, 5))
        
        cols_to_group = []
        if expected_col != "None" and expected_col in df.columns: cols_to_group.append(expected_col)
        if actual_col != "None" and actual_col in df.columns and actual_col != expected_col: cols_to_group.append(actual_col)
        
        if cols_to_group:
            grouped_bar = df.groupby(label_col)[cols_to_group].sum(numeric_only=True).reset_index()
            
            labels = grouped_bar[label_col].astype(str).tolist()
            x = np.arange(len(labels))
            
            if expected_col != "None" and actual_col != "None":
                expected = grouped_bar[expected_col].tolist()
                actual = expected if expected_col == actual_col else grouped_bar[actual_col].tolist()
                width = 0.35  
                ax.bar(x - width/2, expected, width, label=custom_exp_label, color='#4285F4', edgecolor='gray')
                ax.bar(x + width/2, actual, width, label=custom_act_label, color='#34A853', edgecolor='gray')
            elif expected_col != "None":
                expected = grouped_bar[expected_col].tolist()
                width = 0.5
                ax.bar(x, expected, width, label=custom_exp_label, color='#4285F4', edgecolor='gray')
            elif actual_col != "None":
                actual = grouped_bar[actual_col].tolist()
                width = 0.5
                ax.bar(x, actual, width, label=custom_act_label, color='#34A853', edgecolor='gray')
            
            ax.set_title(chart1_title, loc='left', fontsize=16, fontweight='bold', color='gray')
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=45, ha='right')
            ax.legend(loc='upper left', frameon=False, ncol=2)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda val, loc: f"₱{val:,.2f}"))
            ax.grid(axis='y', linestyle='-', alpha=0.7)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile_bar:
                plt.savefig(tmpfile_bar.name, format='png', bbox_inches='tight', dpi=150)
                bar_path = tmpfile_bar.name
            plt.close(fig) 
            
            pdf.image(bar_path, x=10, w=190)
            os.remove(bar_path)
            pdf.ln(5)

    # --- CHART 2: BILLING PROGRESS ---
    if show_chart2:
        can_plot_pie = False
        if pie_mode == "Count of Items" and pie_label_col in df.columns:
            pie_data = df.groupby(pie_label_col).size().reset_index(name='Count')
            plot_val_col = 'Count'
            can_plot_pie = True
        elif pie_mode == "Sum of Values" and pie_label_col in df.columns and pie_val_col in df.columns:
            pie_data = df.groupby(pie_label_col)[pie_val_col].sum().reset_index()
            plot_val_col = pie_val_col
            can_plot_pie = True

        if can_plot_pie:
            fig, ax = plt.subplots(figsize=(8, 6))
            colors = ['#4285F4', '#34A853', '#FBBC05', '#EA4335', '#9AA0A6', '#8A2BE2', '#FF7F50', '#00CED1']
            total = pie_data[plot_val_col].sum()
            
            def absolute_value(val):
                a = np.round(val/100.*total, 0)
                return f"{int(a)} ({val:.1f}%)" if val > 5 else f"{val:.1f}%"
                
            ax.pie(
                pie_data[plot_val_col], 
                labels=pie_data[pie_label_col], 
                autopct=absolute_value,
                shadow=False, 
                startangle=90,
                colors=colors[:len(pie_data)] if len(pie_data) <= len(colors) else None,
                wedgeprops={'edgecolor': 'w', 'linewidth': 1}
            )
            
            ax.set_title(f"{chart2_title}\n", loc='left', fontsize=16, fontweight='bold', color='gray')
            ax.axis('equal') 
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile_pie:
                plt.savefig(tmpfile_pie.name, format='png', bbox_inches='tight', dpi=150)
                pie_path = tmpfile_pie.name
            plt.close(fig)
            
            pdf.ln(85) 
            pdf.image(pie_path, x=25, w=160)
            os.remove(pie_path)

    return pdf.output(dest='S').encode('latin-1')

# --- SIDEBAR: NAVIGATION ---
menu = st.sidebar.radio("Navigation", [
    "Add New Billing Ledger", 
    "Manage Billing Records", 
    "Financial Analytics", 
    "System Settings"
])

st.sidebar.divider()
if st.sidebar.button("🔄 Sync / Refresh Data", type="primary", use_container_width=True):
    st.cache_data.clear() 
    st.rerun()            

if menu == "Financial Analytics":
    st.header("📈 Financial & Collection Dashboard")
    sheets = load_sheet_names()
    
    if not sheets:
        st.info("No billing data available. Please create or upload a ledger first.")
    else:
        selected_sheet = st.selectbox("Select Billing Ledger for Analysis", sheets)
        df = get_sheet_data(selected_sheet).copy()
        
        label_keywords = ['id', 'name', 'category', 'status', 'method', 'region', 'month', 'date', 'client']
        
        for col in df.columns:
            if any(key in col.lower() for key in label_keywords):
                df[col] = df[col].astype(str)
            else:
                converted = pd.to_numeric(df[col], errors='coerce')
                if not converted.isna().all():
                    df[col] = converted
        
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        cat_cols = df.select_dtypes(include=['object', 'string', 'category']).columns.tolist()

        if not numeric_cols:
            st.warning("This sheet doesn't contain numeric billing data for charting.")
        else:
            st.markdown("### ⚙️ Chart Data Mapping")
            c1, c2 = st.columns(2)
            
            with c1:
                st.markdown("**Expected Revenue vs Actual Payments**")
                bar_label = st.selectbox("X-Axis (e.g., Months, Clients)", df.columns, index=0, key="bar_label_select")
                
                exp_act_options = ["None"] + numeric_cols
                
                bar_exp = st.selectbox("Expected Values (e.g., Expected_Amount)", exp_act_options, index=1 if len(numeric_cols) > 0 else 0, key="bar_exp_select")
                bar_act = st.selectbox("Actual Values (e.g., Actual_Paid)", exp_act_options, index=2 if len(numeric_cols) > 1 else (1 if len(numeric_cols) > 0 else 0), key="bar_act_select")
            with c2:
                st.markdown("**Collection Status Breakdown**")
                pie_mode = st.radio("Pie Chart Mode", ["Sum of Values", "Count of Items"], horizontal=True, key="pie_mode_radio")
                pie_label = st.selectbox("Status Categories (e.g., Status)", cat_cols, index=0 if len(cat_cols) > 0 else None, key="pie_label_select")
                
                if pie_mode == "Sum of Values":
                    pie_val = st.selectbox("Values (e.g., Expected_Amount or Balance)", numeric_cols, index=0 if len(numeric_cols) > 0 else None, key="pie_val_select")
                else:
                    pie_val = "Count"

            st.divider()

            st.markdown("### 📊 Editable Financial Summary")
            st.caption("These totals are calculated directly from your ledger. Edit names, toggle the currency symbol per row, or delete/add rows before exporting.")

            if "summary_data" not in st.session_state or st.session_state.get("summary_sheet") != selected_sheet:
                st.session_state.summary_sheet = selected_sheet
                
                db_totals = [{
                    "Select for Deletion": False,
                    "Show ₱": True,
                    "Metric Name": f"Total {col}",
                    "Value": float(df[col].sum(skipna=True))
                } for col in numeric_cols]
                
                st.session_state.summary_data = pd.DataFrame(db_totals)

            edited_summary_df = st.data_editor(
                st.session_state.summary_data,
                num_rows="dynamic",
                width='stretch',
                column_config={
                    "Select for Deletion": st.column_config.CheckboxColumn("🗑️ Delete?", default=False),
                    "Show ₱": st.column_config.CheckboxColumn("Show ₱", default=True),
                    "Metric Name": st.column_config.TextColumn("Metric Name", required=True),
                    "Value": st.column_config.NumberColumn("Value", format="%.2f", required=True)
                },
                key="summary_editor_widget"
            )

            col_del, col_reset = st.columns(2)
            rows_to_delete_mask = edited_summary_df["Select for Deletion"] == True
            num_to_delete = rows_to_delete_mask.sum()
            
            with col_del:
                if st.button(f"🚨 Delete {num_to_delete} Selected Rows", disabled=num_to_delete == 0, width='stretch'):
                    st.session_state.summary_data = edited_summary_df[~rows_to_delete_mask].reset_index(drop=True)
                    if "summary_editor_widget" in st.session_state:
                        del st.session_state["summary_editor_widget"]
                    st.rerun()
                    
            with col_reset:
                if st.button("🔄 Reset to Original DB Totals", width='stretch'):
                    st.session_state.summary_sheet = None
                    if "summary_editor_widget" in st.session_state:
                        del st.session_state["summary_editor_widget"]
                    st.rerun()
            
            st.divider()

            # --- CHART 1: EXPECTED VS ACTUAL ---
            c_title1, c_leg1, c_leg2 = st.columns([2, 1, 1])
            with c_title1:
                chart1_title = st.text_input("Chart 1 Title", value="EXPECTED REVENUE VS ACTUAL PAYMENTS")
            with c_leg1:
                custom_exp_label = st.text_input("Rename 'Expected' Legend", value="EXPECTED REVENUE")
            with c_leg2:
                custom_act_label = st.text_input("Rename 'Actual' Legend", value="ACTUAL PAYMENTS")

            st.markdown(f"### {chart1_title}")
            
            if bar_exp == "None" and bar_act == "None":
                st.info("ℹ️ Please select at least one numeric column for Expected or Actual values to view this chart.")
            else:
                fig1, ax1 = plt.subplots(figsize=(10, 5))
                
                cols_to_group = []
                if bar_exp != "None": cols_to_group.append(bar_exp)
                if bar_act != "None" and bar_act != bar_exp: cols_to_group.append(bar_act)
                
                grouped_bar = df.groupby(bar_label)[cols_to_group].sum(numeric_only=True).reset_index()
                labels = grouped_bar[bar_label].astype(str).tolist()
                x = np.arange(len(labels))
                
                if bar_exp != "None" and bar_act != "None":
                    expected = grouped_bar[bar_exp].tolist()
                    actual = expected if bar_exp == bar_act else grouped_bar[bar_act].tolist()
                    
                    width = 0.35  
                    ax1.bar(x - width/2, expected, width, label=custom_exp_label, color='#4285F4', edgecolor='gray')
                    ax1.bar(x + width/2, actual, width, label=custom_act_label, color='#34A853', edgecolor='gray')
                    ax1.set_xticks(x)
                elif bar_exp != "None":
                    expected = grouped_bar[bar_exp].tolist()
                    width = 0.5
                    ax1.bar(x, expected, width, label=custom_exp_label, color='#4285F4', edgecolor='gray')
                    ax1.set_xticks(x)
                elif bar_act != "None":
                    actual = grouped_bar[bar_act].tolist()
                    width = 0.5
                    ax1.bar(x, actual, width, label=custom_act_label, color='#34A853', edgecolor='gray')
                    ax1.set_xticks(x)
                    
                ax1.set_xticklabels(labels, rotation=45, ha='right')
                ax1.legend(loc='upper left', frameon=False, ncol=2)
                ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda val, loc: f"₱{val:,.2f}"))
                ax1.grid(axis='y', linestyle='-', alpha=0.7)
                ax1.spines['top'].set_visible(False)
                ax1.spines['right'].set_visible(False)
                st.pyplot(fig1)
            st.divider()

            # --- CHART 2: BILLING PROGRESS ---
            chart2_title = st.text_input("Chart 2 Title", value="COLLECTION STATUS BREAKDOWN")
            st.markdown(f"### {chart2_title}")
            
            fig2, ax2 = plt.subplots(figsize=(8, 6))
            
            if pie_mode == "Sum of Values":
                pie_data = df.groupby(pie_label)[pie_val].sum().reset_index()
                plot_val_col = pie_val
            else:
                pie_data = df.groupby(pie_label).size().reset_index(name='Count')
                plot_val_col = 'Count'

            colors = ['#4285F4', '#34A853', '#FBBC05', '#EA4335', '#9AA0A6', '#8A2BE2', '#FF7F50', '#00CED1']
            total = pie_data[plot_val_col].sum()
            
            def absolute_value(val):
                a = np.round(val/100.*total, 0)
                return f"{int(a)} ({val:.1f}%)" if val > 5 else f"{val:.1f}%"
                
            ax2.pie(
                pie_data[plot_val_col], 
                labels=pie_data[pie_label], 
                autopct=absolute_value,
                shadow=False, 
                startangle=90,
                colors=colors[:len(pie_data)] if len(pie_data) <= len(colors) else None,
                wedgeprops={'edgecolor': 'w', 'linewidth': 1}
            )
            ax2.axis('equal')
            st.pyplot(fig2)

            # --- EXPORT TO PDF ---
            st.divider()
            st.markdown("### 📥 Export Dashboard")
            
            # --- NEW PDF EXPORT OPTIONS ---
            exp_col1, exp_col2 = st.columns(2)
            with exp_col1:
                custom_file_name = st.text_input("Save file as:", value=f"{selected_sheet}_billing_report", key="pdf_filename_input")
                main_pdf_title = st.text_input("Main PDF Title", value=f"Billing Analytics Report: {selected_sheet}")
            with exp_col2:
                st.markdown("**PDF Output Options:**")
                show_main_title = st.checkbox("Include Main Title", value=True)
                show_chart1_pdf = st.checkbox(f"Include Chart 1 ({chart1_title})", value=True)
                show_chart2_pdf = st.checkbox(f"Include Chart 2 ({chart2_title})", value=True)
            
            final_file_name = custom_file_name if custom_file_name.lower().endswith(".pdf") else f"{custom_file_name}.pdf"
            
            pdf_bytes = generate_pdf_report(
                df, selected_sheet, 
                bar_label, bar_exp, bar_act, 
                pie_label, pie_val, pie_mode, edited_summary_df,
                chart1_title, chart2_title, custom_exp_label, custom_act_label,
                main_pdf_title, show_main_title, show_chart1_pdf, show_chart2_pdf
            )
            # ------------------------------
            
            st.download_button(
                label="Download Financial Report (PDF)",
                data=pdf_bytes,
                file_name=final_file_name,
                mime="application/pdf",
                type="primary",
                width='stretch'
            )

elif menu == "Add New Billing Ledger":
    st.header("✨ Add a New Billing Ledger")
    
    creation_method = st.radio(
        "How would you like to start?", 
        ["Create Standard Billing Template", "Upload Existing Excel/CSV"], 
        horizontal=True
    )
    
    new_name = st.text_input("Ledger Name", placeholder="e.g. Q1_2024_Invoices")
    st.divider() 
    
    if creation_method == "Upload Existing Excel/CSV":
        uploaded_file = st.file_uploader("Upload your billing records to start", type=["xlsx", "csv"])
        
        if uploaded_file:
            if uploaded_file.name.endswith('xlsx'):
                xls = pd.ExcelFile(uploaded_file)
                sheet_names = xls.sheet_names
                
                if len(sheet_names) > 1:
                    selected_tab = st.selectbox("📂 Select the Excel tab (sheet) to load:", sheet_names)
                else:
                    selected_tab = sheet_names[0]
                    st.info(f"Loaded the only sheet available: {selected_tab}")
                    
                df = pd.read_excel(uploaded_file, sheet_name=selected_tab)
            else:
                df = pd.read_csv(uploaded_file)
                
            df = fix_arrow_types(df)

            st.write(f"Preview (Showing all {len(df)} records):")
            st.dataframe(df, width="stretch", height=800)
            
            if st.button("Save to Database", type="primary"):
                if new_name:
                    save_to_mongo(new_name, df)
                else:
                    st.error("Please provide a ledger name.")

    elif creation_method == "Create Standard Billing Template":
        st.info("A standard billing template has been loaded for you. Fill in the records below!")
        
        starter_data = pd.DataFrame([{
            "Invoice_ID": "INV-001", 
            "Client_Name": "", 
            "Due_Date": "", 
            "Expected_Amount": 0.0,
            "Actual_Paid": 0.0,
            "Balance": 0.0,
            "Status": "Pending"
        }])
        
        edited_df = st.data_editor(
            starter_data, 
            num_rows="dynamic",
            width="stretch",
            height=600
        )
        
        if st.button("Save to Database", type="primary"):
            if new_name:
                save_to_mongo(new_name, edited_df)
            else:
                st.error("Please provide a ledger name.")

elif menu == "Manage Billing Records":
    st.header("📝 Manage Billing Records")
    sheets = load_sheet_names()
    
    if not sheets:
        st.info("No billing ledgers found in database.")
    else:
        selected_sheet = st.selectbox("Select Ledger to Edit/View", sheets)
        df = get_sheet_data(selected_sheet)
        
        if "working_sheet" not in st.session_state or st.session_state.working_sheet != selected_sheet:
            st.session_state.working_sheet = selected_sheet
            st.session_state.working_df = get_sheet_data(selected_sheet)
            
        df = st.session_state.working_df

        st.subheader("🔍 Search & Filter")
        f_col1, f_col2 = st.columns(2)
        
        with f_col1:
            st.markdown("**📝 Text Search (e.g. Client Name, Invoice ID)**")
            search_term = st.text_input("Search for...", placeholder="Type word or phrase here...")
            filter_col = st.selectbox("Text Search in Column:", ["All Columns"] + list(df.columns))
            
        with f_col2:
            st.markdown("**🔢 Number Filter (e.g. Amounts, Balances)**")
            numeric_cols = [col for col in df.columns if pd.api.types.is_numeric_dtype(pd.to_numeric(df[col], errors='coerce'))]
            
            use_num_filter = st.checkbox("Enable Number Filter", disabled=len(numeric_cols) == 0)
            
            if use_num_filter and numeric_cols:
                n1, n2, n3 = st.columns([2, 1, 2])
                with n1:
                    num_col = st.selectbox("Numeric Column:", numeric_cols)
                with n2:
                    num_op = st.selectbox("Condition:", [">", "<", ">=", "<=", "==", "!="])
                with n3:
                    num_val = st.number_input("Value:", value=0.0)

        filtered_df = df.copy()
        
        if search_term:
            if filter_col == "All Columns":
                mask = filtered_df.astype(str).apply(lambda x: x.str.contains(search_term, case=False, na=False)).any(axis=1)
                filtered_df = filtered_df[mask]
            else:
                mask = filtered_df[filter_col].astype(str).str.contains(search_term, case=False, na=False)
                filtered_df = filtered_df[mask]
                
        if use_num_filter and numeric_cols:
            temp_col = pd.to_numeric(filtered_df[num_col], errors='coerce')
            if num_op == ">": filtered_df = filtered_df[temp_col > num_val]
            elif num_op == "<": filtered_df = filtered_df[temp_col < num_val]
            elif num_op == ">=": filtered_df = filtered_df[temp_col >= num_val]
            elif num_op == "<=": filtered_df = filtered_df[temp_col <= num_val]
            elif num_op == "==": filtered_df = filtered_df[temp_col == num_val]
            elif num_op == "!=": filtered_df = filtered_df[temp_col != num_val]

        if search_term or use_num_filter:
            st.caption(f"Showing {len(filtered_df)} out of {len(df)} total records.")
            
        st.divider()
        
        st.info("✏️ **Edit & Add:** Double-click any cell to edit. Add new invoices by typing in the bottom row with the '+' icon.")

        filtered_df.insert(0, "Select for Deletion", False)

        edited_filtered_df = st.data_editor(
            filtered_df, 
            num_rows="dynamic", 
            width="stretch", 
            height=600,
            column_config={
                "Select for Deletion": st.column_config.CheckboxColumn(
                    "🗑️ Delete?",
                    help="Check to mark this invoice for deletion",
                    default=False,
                )
            }
        )
        
        st.markdown("### 🗑️ Bulk Delete Records")
        
        rows_to_delete_mask = edited_filtered_df["Select for Deletion"] == True
        num_selected_to_delete = rows_to_delete_mask.sum()
        
        if st.button(f"🚨 Permanently Delete {num_selected_to_delete} Selected Record(s)", type="primary", width="stretch", disabled=num_selected_to_delete == 0):
            real_indices_to_drop = edited_filtered_df[rows_to_delete_mask].index
            updated_master_df_after_drop = df.drop(index=real_indices_to_drop)
            save_to_mongo(selected_sheet, updated_master_df_after_drop)
            st.rerun()

        st.divider()
        
        with st.expander("🛠️ Advanced: Add or Rename Columns"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Add a New Data Point (Column)**")
                new_col = st.text_input("New Field Name", key="new_col_input")
                if st.button("➕ Add Field", width="stretch"):
                    if new_col and new_col not in df.columns:
                        updated_df = df.copy()
                        updated_df[new_col] = "" 
                        save_to_mongo(selected_sheet, updated_df)
                        st.rerun() 
                    elif new_col in df.columns:
                        st.error("Field already exists!")
            with c2:
                st.markdown("**Rename Existing Field**")
                col_to_rename = st.selectbox("Select Field", df.columns)
                new_col_name = st.text_input("New Name", key="rename_col_input")
                if st.button("✏️ Rename Field", width="stretch"):
                    if new_col_name and new_col_name not in df.columns:
                        updated_df = df.rename(columns={col_to_rename: new_col_name})
                        save_to_mongo(selected_sheet, updated_df)
                        st.rerun()
                    elif new_col_name in df.columns:
                        st.error("Field name already exists!")

        st.divider()
        
        with st.expander("📅 Auto-Project Missing Dates"):
            st.markdown("Select your chronological date columns. The system will fill in blanks or 'to update' cells based on the previous step's date.")
            
            target_sequence = [
                "P&D", 
                "FOR (GR) PROCESSING", 
                "GR", 
                "BILL TO JB CENTER", 
                "PAYMENT ADVICE", 
                "COLLECTION RECEIPT"
            ]
            
            valid_defaults = [col for col in target_sequence if col in df.columns]
            
            date_sequence = st.multiselect(
                "Select Date Columns in Chronological Order:",
                options=df.columns.tolist(),
                default=valid_defaults,
                help="The standard billing sequence. You can add or remove steps if needed."
            )
            
            days_to_project = st.number_input("Days to add for projection:", min_value=1, value=14, step=1)
            
            if st.button("🚀 Apply Projections", type="primary"):
                if len(date_sequence) < 2:
                    st.warning("Please select at least two columns to create a projection sequence.")
                else:
                    clean_edited = edited_filtered_df.drop(columns=["Select for Deletion"], errors='ignore')
                    updated_df = auto_project_dates(clean_edited, date_sequence, days_to_project)
                    st.session_state.working_df.update(updated_df)
                    st.success("✅ Projections applied to the table above! Click 'Save Ledger Changes' below to finalize.")
                    st.rerun()

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Ledger Changes", type="primary", width="stretch"):
                updated_master_df = st.session_state.working_df.copy()
                clean_edited_df = edited_filtered_df.drop(columns=["Select for Deletion"])
                clean_filtered_df = filtered_df.drop(columns=["Select for Deletion"])
                
                updated_master_df.update(clean_edited_df)
                
                new_rows = clean_edited_df[~clean_edited_df.index.isin(updated_master_df.index)]
                if not new_rows.empty:
                    updated_master_df = pd.concat([updated_master_df, new_rows], ignore_index=True)
                    
                deleted_indices = clean_filtered_df.index.difference(clean_edited_df.index)
                updated_master_df = updated_master_df.drop(deleted_indices)
                
                save_to_mongo(selected_sheet, updated_master_df)
                st.session_state.working_df = updated_master_df
                st.rerun()
                
        with col2:
            towrite = BytesIO()
            export_df = edited_filtered_df.drop(columns=["Select for Deletion"])
            export_df.to_excel(towrite, index=False, engine='openpyxl')
            st.download_button(label="📥 Download Ledger as Excel", data=towrite.getvalue(), file_name=f"{selected_sheet}_export.xlsx", width="stretch")

elif menu == "System Settings":
    st.header("🗑️ Manage Databases")
    sheets = load_sheet_names()
    
    if not sheets:
        st.info("System is empty.")
    else:
        st.warning("⚠️ **Warning:** Deleting a ledger will permanently erase all billing records associated with it from the database.")
        sheet_to_delete = st.selectbox("Select Ledger to permanently remove", sheets)
        
        confirm_delete = st.checkbox(f"I understand that deleting '{sheet_to_delete}' cannot be undone.")
        
        if st.button("🚨 Delete Ledger", type="primary", disabled=not confirm_delete):
            collection.delete_one({"sheet_name": sheet_to_delete})
            st.cache_data.clear() 
            st.success(f"Deleted {sheet_to_delete}")
            st.rerun()