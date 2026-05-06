import streamlit as st
import pandas as pd
from pymongo import MongoClient
from io import BytesIO
import matplotlib.pyplot as plt 
from fpdf import FPDF           
import numpy as np
import tempfile
import os

# --- DATABASE SETUP ---
@st.cache_resource
def init_connection():
    # Cache the database connection so it doesn't reconnect on every rerun
    return MongoClient(st.secrets["MONGO_URI"])

client = init_connection()
db = client["spreadsheet_app"]
collection = db["sheets"]

# --- APP CONFIG ---
st.set_page_config(page_title="MongoSheet Editor", layout="wide")
st.title("📊 MongoSheet Manager")
st.markdown("Create, modify, and delete Excel-style sheets stored in MongoDB.")

# --- FUNCTIONS ---
def fix_arrow_types(df):
    # """Converts mixed 'object' columns to strings using vectorized operations for speed."""
    cols = df.select_dtypes(include=['object', 'string']).columns
    if not cols.empty:
        df[cols] = df[cols].astype(str)
    return df

@st.cache_data(show_spinner=False)
def load_sheet_names():
    #"""Fetches sheet names and caches them to prevent constant DB pings."""
    return [doc["sheet_name"] for doc in collection.find({}, {"sheet_name": 1})]

@st.cache_data(show_spinner="Loading data from database...")
def get_sheet_data(name):
    #"""Fetches sheet data from MongoDB and caches the resulting dataframe."""
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
    # Clear cache so the app fetches the updated data on the next rerun
    st.cache_data.clear()
    st.success(f"Sheet '{name}' saved successfully!")

def generate_pdf_report(df, sheet_name, label_col, expected_col, actual_col, pie_label_col, pie_val_col, summary_df):
    #"""Generates a PDF document with custom styled Bar and Pie charts."""
    pdf = FPDF()
    pdf.add_page()
    
    # --- REPORT HEADER ---
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, f"Analytics Report: {sheet_name}", ln=True, align='C')
    pdf.ln(5)
    
    # --- EDITABLE SUMMARY METRICS ---
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(190, 10, "Summary Totals:", ln=True)
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
        
        # Optimize aggregation
        grouped_bar = df.groupby(label_col)[[expected_col, actual_col]].sum(numeric_only=True).reset_index()
        
        labels = grouped_bar[label_col].astype(str).tolist()
        expected = grouped_bar[expected_col].tolist()
        actual = grouped_bar[actual_col].tolist()
        
        x = np.arange(len(labels))
        width = 0.35  
        
        ax.bar(x - width/2, expected, width, label='EXPECTED', color='#4285F4', edgecolor='gray')
        ax.bar(x + width/2, actual, width, label='ACTUAL', color='#EA4335', edgecolor='gray')
        
        ax.set_title('EXPECTED VS ACTUAL', loc='left', fontsize=18, fontweight='bold', color='gray')
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
        plt.close(fig) # Explicitly close to free memory
        
        pdf.image(bar_path, x=10, w=190)
        os.remove(bar_path)
        pdf.ln(5)

    # --- CHART 2: BILLING PROGRESS ---
    if pie_label_col in df.columns and pie_val_col in df.columns:
        pie_data = df.groupby(pie_label_col)[pie_val_col].sum().reset_index()
        fig, ax = plt.subplots(figsize=(8, 6))
        colors = ['#4285F4', '#EA4335', '#FBBC05', '#34A853']
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
        
        ax.set_title('BILLING PROGRESS\n', loc='left', fontsize=18, fontweight='bold', color='gray')
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
menu = st.sidebar.radio("Navigation", ["Create New Sheet", "View & Edit Sheet", "Analytics Dashboard", "Manage Database"])

if menu == "Analytics Dashboard":
    st.header("📈 Billing Analytics Dashboard")
    sheets = load_sheet_names()
    
    if not sheets:
        st.info("No data available. Please create or upload a sheet first.")
    else:
        selected_sheet = st.selectbox("Select sheet for analysis", sheets)
        
        # PULL FROM DB (Cached)
        df = get_sheet_data(selected_sheet).copy()
        
        # --- TYPE CONVERSION (Optimized) ---
        label_keywords = ['id', 'name', 'category', 'status', 'method', 'region', 'month', 'date']
        
        for col in df.columns:
            if any(key in col.lower() for key in label_keywords):
                df[col] = df[col].astype(str)
            else:
                # Safely attempt conversion using 'coerce'
                converted = pd.to_numeric(df[col], errors='coerce')
                # Only apply the conversion if the column isn't entirely NaN (non-numeric)
                if not converted.isna().all():
                    df[col] = converted
        
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        cat_cols = df.select_dtypes(include=['object', 'string', 'category']).columns.tolist()

        if not numeric_cols:
            st.warning("This sheet doesn't contain numeric data for charting.")
        else:
            # --- DASHBOARD CONFIGURATION ---
            st.markdown("### ⚙️ Chart Data Mapping")
            c1, c2 = st.columns(2)
            
            with c1:
                st.markdown("**Expected vs Actual Chart**")
                bar_label = st.selectbox("X-Axis (e.g., Months)", df.columns, index=0, key="bar_label_select")
                bar_exp = st.selectbox("Expected Values", numeric_cols, index=0 if len(numeric_cols) > 0 else None, key="bar_exp_select")
                bar_act = st.selectbox("Actual Values", numeric_cols, index=1 if len(numeric_cols) > 1 else 0, key="bar_act_select")
                
            with c2:
                st.markdown("**Billing Progress Chart**")
                pie_label = st.selectbox("Status Categories", cat_cols, index=0 if len(cat_cols) > 0 else None, key="pie_label_select")
                pie_val = st.selectbox("Values", numeric_cols, index=0 if len(numeric_cols) > 0 else None, key="pie_val_select")

            st.divider()

            # --- EDITABLE SUMMARY TOTALS ---
            st.markdown("### 📊 Editable Summary Totals")
            st.caption("These totals are calculated directly from your database. You can edit names, toggle the currency symbol per row, or delete/add rows.")

            # 1. Initialize data once per sheet selection
            if "summary_data" not in st.session_state or st.session_state.get("summary_sheet") != selected_sheet:
                st.session_state.summary_sheet = selected_sheet
                
                db_totals = [{
                    "Select for Deletion": False,
                    "Show ₱": True,
                    "Metric Name": f"Total {col}",
                    "Value": float(df[col].sum(skipna=True))
                } for col in numeric_cols]
                
                st.session_state.summary_data = pd.DataFrame(db_totals)

            # 2. Render the editor using the session_state as the source
            # DO NOT assign the output back to st.session_state.summary_data here.
            # This allows the widget to maintain its own "delta" state during multiple clicks.
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

            # 3. Only update the "master" session state when a button is clicked or during export.
            # This prevents the "refresh loop" on every checkbox click.

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
            st.markdown("### EXPECTED VS ACTUAL")
            
            fig1, ax1 = plt.subplots(figsize=(10, 5))
            
            grouped_bar = df.groupby(bar_label)[[bar_exp, bar_act]].sum(numeric_only=True).reset_index()
            
            labels = grouped_bar[bar_label].astype(str).tolist()
            expected = grouped_bar[bar_exp].tolist()
            actual = grouped_bar[bar_act].tolist()
            
            x = np.arange(len(labels))
            width = 0.35  
            
            ax1.bar(x - width/2, expected, width, label='EXPECTED', color='#4285F4', edgecolor='gray')
            ax1.bar(x + width/2, actual, width, label='ACTUAL', color='#EA4335', edgecolor='gray')
            
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
            st.markdown("### BILLING PROGRESS")
            
            fig2, ax2 = plt.subplots(figsize=(8, 6))
            pie_data = df.groupby(pie_label)[pie_val].sum().reset_index()
            colors = ['#4285F4', '#EA4335', '#FBBC05', '#34A853', '#9AA0A6']
            
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

            custom_file_name = st.text_input(
                "Save file as:", 
                value=f"{selected_sheet}_analytics_report",
                key="pdf_filename_input"
            )
            
            final_file_name = custom_file_name if custom_file_name.lower().endswith(".pdf") else f"{custom_file_name}.pdf"
            
            pdf_bytes = generate_pdf_report(df, selected_sheet, bar_label, bar_exp, bar_act, pie_label, pie_val, edited_summary_df)
            
            st.download_button(
                label="Download Full Report as PDF",
                data=pdf_bytes,
                file_name=final_file_name,
                mime="application/pdf",
                type="primary",
                width='stretch'
            )

elif menu == "Create New Sheet":
    st.header("✨ Create a New Sheet")
    
    creation_method = st.radio(
        "How would you like to start?", 
        ["Upload File", "Create from Scratch"], 
        horizontal=True
    )
    
    new_name = st.text_input("Sheet Name", placeholder="Monthly_Budget_2024")
    st.divider() 
    
    if creation_method == "Upload File":
        uploaded_file = st.file_uploader("Upload an Excel/CSV file to start", type=["xlsx", "csv"])
        
        if uploaded_file:
            df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('xlsx') else pd.read_csv(uploaded_file)
            df = fix_arrow_types(df)

            st.write(f"Preview (Showing all {len(df)} rows):")
            st.dataframe(df, width="stretch", height=800)
            
            if st.button("Save to MongoDB", type="primary"):
                if new_name:
                    save_to_mongo(new_name, df)
                else:
                    st.error("Please provide a sheet name.")

    elif creation_method == "Create from Scratch":
        st.info("Start typing in the cells below. You can click the bottom row to add more rows!")
        
        starter_data = pd.DataFrame([{"Col_A": "", "Col_B": "", "Col_C": ""}])
        
        edited_df = st.data_editor(
            starter_data, 
            num_rows="dynamic",
            width="stretch",
            height=600
        )
        
        if st.button("Save to MongoDB", type="primary"):
            if new_name:
                save_to_mongo(new_name, edited_df)
            else:
                st.error("Please provide a sheet name.")

elif menu == "View & Edit Sheet":
    st.header("📝 View & Edit Sheet")
    sheets = load_sheet_names()
    
    if not sheets:
        st.info("No sheets found in database.")
    else:
        selected_sheet = st.selectbox("Select sheet to edit/view", sheets)
        df = get_sheet_data(selected_sheet)
        
        st.subheader("🔍 Search & Filter")
        f_col1, f_col2 = st.columns(2)
        
        with f_col1:
            st.markdown("**📝 Text Search**")
            search_term = st.text_input("Search for...", placeholder="Type word or phrase here...")
            filter_col = st.selectbox("Text Search in Column:", ["All Columns"] + list(df.columns))
            
        with f_col2:
            st.markdown("**🔢 Number Filter**")
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
                # Vectorized search across all columns
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
            st.caption(f"Showing {len(filtered_df)} out of {len(df)} total rows.")
            
        st.divider()
        st.info("✏️ **Edit & Add:** Double-click any cell to edit. Add new rows by typing in the bottom row with the '+' icon.")

        filtered_df.insert(0, "Select for Deletion", False)

        edited_filtered_df = st.data_editor(
            filtered_df, 
            num_rows="dynamic", 
            width="stretch", 
            height=800,
            column_config={
                "Select for Deletion": st.column_config.CheckboxColumn(
                    "🗑️ Delete?",
                    help="Check to mark this row for deletion",
                    default=False,
                )
            }
        )
        
        st.markdown("### 🗑️ Bulk Delete Rows")
        st.caption("Check the boxes in the '🗑️ Delete?' column above, then click the button below to permanently erase those rows.")
        
        rows_to_delete_mask = edited_filtered_df["Select for Deletion"] == True
        num_selected_to_delete = rows_to_delete_mask.sum()
        
        if st.button(f"🚨 Permanently Delete {num_selected_to_delete} Selected Row(s)", type="primary", width="stretch", disabled=num_selected_to_delete == 0):
            real_indices_to_drop = edited_filtered_df[rows_to_delete_mask].index
            updated_master_df_after_drop = df.drop(index=real_indices_to_drop)
            save_to_mongo(selected_sheet, updated_master_df_after_drop)
            st.rerun()

        st.divider()
        
        with st.expander("🛠️ Add or Rename Columns"):
            c1, c2 = st.columns(2)
            
            with c1:
                st.markdown("**Add a New Column**")
                new_col = st.text_input("New Column Name", key="new_col_input")
                if st.button("➕ Add Column", width="stretch"):
                    if new_col and new_col not in df.columns:
                        updated_df = df.copy()
                        updated_df[new_col] = "" 
                        save_to_mongo(selected_sheet, updated_df)
                        st.rerun() 
                    elif new_col in df.columns:
                        st.error("Column already exists!")
            
            with c2:
                st.markdown("**Rename Existing Column**")
                col_to_rename = st.selectbox("Select column", df.columns)
                new_col_name = st.text_input("New Name", key="rename_col_input")
                if st.button("✏️ Rename Column", width="stretch"):
                    if new_col_name and new_col_name not in df.columns:
                        updated_df = df.rename(columns={col_to_rename: new_col_name})
                        save_to_mongo(selected_sheet, updated_df)
                        st.rerun()
                    elif new_col_name in df.columns:
                        st.error("Column name already exists!")

        st.divider()
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Cell Changes", type="primary", width="stretch"):
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
            st.download_button(label="📥 Download View as Excel", data=towrite.getvalue(), file_name=f"{selected_sheet}_export.xlsx", width="stretch")
            
        st.divider()
        
        st.subheader("🔍 Inspect Individual Rows")
        
        if not edited_filtered_df.empty:
            tab1, tab2 = st.tabs(["👤 Individual Row View", "🗄️ Raw Database Document"])
            
            with tab1:
                st.markdown("Use the navigation buttons or number input to inspect individual rows from the table above.")
                max_row = max(0, len(edited_filtered_df) - 1)
                
                if "current_row" not in st.session_state:
                    st.session_state.current_row = 0
                if st.session_state.current_row > max_row:
                    st.session_state.current_row = max_row
                
                def go_next_row():
                    if st.session_state.current_row < max_row:
                        st.session_state.current_row += 1
                        
                def go_prev_row():
                    if st.session_state.current_row > 0:
                        st.session_state.current_row -= 1
                
                nav1, nav2, nav3 = st.columns([1, 2, 1])
                
                with nav1:
                    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                    st.button("⬅️ Previous", on_click=go_prev_row, width="stretch", disabled=st.session_state.current_row <= 0)
                        
                with nav2:
                    st.number_input("Row Index", min_value=0, max_value=max_row, key="current_row")
                    
                with nav3:
                    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                    st.button("Next ➡️", on_click=go_next_row, width="stretch", disabled=st.session_state.current_row >= max_row)
                
                st.write(f"**Showing Data for Table Row {st.session_state.current_row}:**")
                display_row = edited_filtered_df.drop(columns=["Select for Deletion"]).iloc[st.session_state.current_row].to_dict()
                st.json(display_row)

            with tab2:
                st.markdown("This is how the document is currently saved in MongoDB (does not reflect unsaved edits).")
                raw_doc = collection.find_one({"sheet_name": selected_sheet}, {"_id": 0})
                st.json(raw_doc)
        else:
            st.warning("No rows match your search, or the sheet is empty.")

elif menu == "Manage Database":
    st.header("🗑️ Delete Sheets")
    sheets = load_sheet_names()
    
    if not sheets:
        st.info("Database is empty.")
    else:
        st.warning("⚠️ **Warning:** Deleting a sheet will permanently erase all its data from the database.")
        sheet_to_delete = st.selectbox("Select sheet to permanently remove", sheets)
        
        confirm_delete = st.checkbox(f"I understand that deleting '{sheet_to_delete}' cannot be undone.")
        
        if st.button("🚨 Delete Sheet", type="primary", disabled=not confirm_delete):
            collection.delete_one({"sheet_name": sheet_to_delete})
            st.cache_data.clear() # Clear cache on deletion
            st.success(f"Deleted {sheet_to_delete}")
            st.rerun()