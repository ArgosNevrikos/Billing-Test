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
def fix_arrow_types(df):
    cols = df.select_dtypes(include=['object', 'string']).columns
    if not cols.empty:
        df[cols] = df[cols].astype(str)
    return df

@st.cache_data(show_spinner=False)
def load_sheet_names():
    return [doc["sheet_name"] for doc in collection.find({}, {"sheet_name": 1})]

@st.cache_data(show_spinner="Loading data from database...")
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

def generate_pdf_report(df, sheet_name, label_col, expected_col, actual_col, pie_label_col, pie_val_col, summary_df):
    pdf = FPDF()
    pdf.add_page()
    
    # --- REPORT HEADER ---
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, f"Billing Analytics Report: {sheet_name}", ln=True, align='C')
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
    if label_col in df.columns and expected_col in df.columns and actual_col in df.columns:
        fig, ax = plt.subplots(figsize=(10, 5))
        
        grouped_bar = df.groupby(label_col)[[expected_col, actual_col]].sum(numeric_only=True).reset_index()
        
        labels = grouped_bar[label_col].astype(str).tolist()
        expected = grouped_bar[expected_col].tolist()
        actual = grouped_bar[actual_col].tolist()
        
        x = np.arange(len(labels))
        width = 0.35  
        
        ax.bar(x - width/2, expected, width, label='EXPECTED REVENUE', color='#4285F4', edgecolor='gray')
        ax.bar(x + width/2, actual, width, label='ACTUAL PAYMENTS', color='#34A853', edgecolor='gray')
        
        ax.set_title('EXPECTED REVENUE VS. ACTUAL PAYMENTS', loc='left', fontsize=16, fontweight='bold', color='gray')
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
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
    if pie_label_col in df.columns and pie_val_col in df.columns:
        pie_data = df.groupby(pie_label_col)[pie_val_col].sum().reset_index()
        fig, ax = plt.subplots(figsize=(8, 6))
        colors = ['#4285F4', '#34A853', '#FBBC05', '#EA4335']
        total = pie_data[pie_val_col].sum()
        
        def absolute_value(val):
            a = np.round(val/100.*total, 0)
            return f"{int(a)} ({val:.1f}%)" if val > 5 else f"{val:.1f}%"
            
        ax.pie(
            pie_data[pie_val_col], 
            labels=pie_data[pie_label_col], 
            autopct=absolute_value,
            shadow=False, 
            startangle=90,
            colors=colors[:len(pie_data)],
            wedgeprops={'edgecolor': 'w', 'linewidth': 1}
        )
        
        ax.set_title('COLLECTION STATUS PROGRESS\n', loc='left', fontsize=16, fontweight='bold', color='gray')
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
            
            # --- NEW FEATURE: AUTO-COMPUTE BALANCES MOVED HERE ---
            st.markdown("### 🧮 Auto-Compute Tools")
            st.caption("Select your expected and actual payment columns to automatically calculate 'Balance' and 'Status' for the dashboard and PDF report.")
            
            ac1, ac2 = st.columns(2)
            with ac1:
                auto_exp_col = st.selectbox("Expected Amount Column", numeric_cols, index=0 if len(numeric_cols)>0 else None, key="auto_exp")
            with ac2:
                auto_act_col = st.selectbox("Actual Paid Column", numeric_cols, index=1 if len(numeric_cols)>1 else 0, key="auto_act")
                
            if st.button("⚡ Auto-Calculate Balance & Status"):
                if auto_exp_col and auto_act_col:
                    try:
                        updated_master_df = df.copy()
                        exp = pd.to_numeric(updated_master_df[auto_exp_col], errors='coerce').fillna(0)
                        act = pd.to_numeric(updated_master_df[auto_act_col], errors='coerce').fillna(0)
                        
                        updated_master_df["Balance"] = exp - act
                        
                        conditions = [
                            (act == 0) & (exp > 0),
                            (act > 0) & (act < exp),
                            (act >= exp) & (exp > 0),
                            (act > exp)
                        ]
                        choices = ["Unpaid", "Partially Paid", "Fully Paid", "Overpaid"]
                        
                        if "Status" not in updated_master_df.columns:
                            updated_master_df["Status"] = "Pending"
                            
                        updated_master_df["Status"] = np.select(conditions, choices, default=updated_master_df["Status"])
                        
                        save_to_mongo(selected_sheet, updated_master_df)
                        # Force the summary widget to regenerate and pick up the new totals
                        st.session_state.summary_sheet = None 
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error computing values: {e}")
            
            st.divider()

            st.markdown("### ⚙️ Chart Data Mapping")
            c1, c2 = st.columns(2)
            
            with c1:
                st.markdown("**Expected Revenue vs Actual Payments**")
                bar_label = st.selectbox("X-Axis (e.g., Months, Clients)", df.columns, index=0, key="bar_label_select")
                bar_exp = st.selectbox("Expected Values (e.g., Expected_Amount)", numeric_cols, index=0 if len(numeric_cols) > 0 else None, key="bar_exp_select")
                bar_act = st.selectbox("Actual Values (e.g., Actual_Paid)", numeric_cols, index=1 if len(numeric_cols) > 1 else 0, key="bar_act_select")
                
            with c2:
                st.markdown("**Collection Status Breakdown**")
                pie_label = st.selectbox("Status Categories (e.g., Status)", cat_cols, index=0 if len(cat_cols) > 0 else None, key="pie_label_select")
                pie_val = st.selectbox("Values (e.g., Expected_Amount or Balance)", numeric_cols, index=0 if len(numeric_cols) > 0 else None, key="pie_val_select")

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
            st.markdown("### EXPECTED REVENUE VS ACTUAL PAYMENTS")
            fig1, ax1 = plt.subplots(figsize=(10, 5))
            grouped_bar = df.groupby(bar_label)[[bar_exp, bar_act]].sum(numeric_only=True).reset_index()
            labels = grouped_bar[bar_label].astype(str).tolist()
            expected = grouped_bar[bar_exp].tolist()
            actual = grouped_bar[bar_act].tolist()
            x = np.arange(len(labels))
            width = 0.35  
            
            ax1.bar(x - width/2, expected, width, label='EXPECTED REVENUE', color='#4285F4', edgecolor='gray')
            ax1.bar(x + width/2, actual, width, label='ACTUAL PAYMENTS', color='#34A853', edgecolor='gray')
            ax1.set_xticks(x)
            ax1.set_xticklabels(labels)
            ax1.legend(loc='upper left', frameon=False, ncol=2)
            ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda val, loc: f"₱{val:,.2f}"))
            ax1.grid(axis='y', linestyle='-', alpha=0.7)
            ax1.spines['top'].set_visible(False)
            ax1.spines['right'].set_visible(False)
            st.pyplot(fig1)
            st.divider()

            # --- CHART 2: BILLING PROGRESS ---
            st.markdown("### COLLECTION STATUS BREAKDOWN")
            fig2, ax2 = plt.subplots(figsize=(8, 6))
            pie_data = df.groupby(pie_label)[pie_val].sum().reset_index()
            colors = ['#4285F4', '#34A853', '#FBBC05', '#EA4335', '#9AA0A6']
            
            total = pie_data[pie_val].sum()
            def absolute_value(val):
                a = np.round(val/100.*total, 0)
                return f"{int(a)} ({val:.1f}%)" if val > 5 else f"{val:.1f}%"
                
            ax2.pie(
                pie_data[pie_val], 
                labels=pie_data[pie_label], 
                autopct=absolute_value,
                shadow=False, 
                startangle=90,
                colors=colors[:len(pie_data)],
                wedgeprops={'edgecolor': 'w', 'linewidth': 1}
            )
            ax2.axis('equal')
            st.pyplot(fig2)

            # --- EXPORT TO PDF ---
            st.divider()
            st.markdown("### 📥 Export Dashboard")
            custom_file_name = st.text_input("Save file as:", value=f"{selected_sheet}_billing_report", key="pdf_filename_input")
            final_file_name = custom_file_name if custom_file_name.lower().endswith(".pdf") else f"{custom_file_name}.pdf"
            pdf_bytes = generate_pdf_report(df, selected_sheet, bar_label, bar_exp, bar_act, pie_label, pie_val, edited_summary_df)
            
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
            df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('xlsx') else pd.read_csv(uploaded_file)
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
        
        # New standardized billing template
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

        # Vectorized Filtering Application
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
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Ledger Changes", type="primary", width="stretch"):
                updated_master_df = df.copy()
                clean_edited_df = edited_filtered_df.drop(columns=["Select for Deletion"])
                clean_filtered_df = filtered_df.drop(columns=["Select for Deletion"])
                
                updated_master_df.update(clean_edited_df)
                
                new_rows = clean_edited_df[~clean_edited_df.index.isin(df.index)]
                if not new_rows.empty:
                    updated_master_df = pd.concat([updated_master_df, new_rows], ignore_index=True)
                    
                deleted_indices = clean_filtered_df.index.difference(clean_edited_df.index)
                updated_master_df = updated_master_df.drop(deleted_indices)
                
                save_to_mongo(selected_sheet, updated_master_df)
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
