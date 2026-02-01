import streamlit as st
import pandas as pd
from datetime import datetime
from finance_tracker import CSVStorage

# --- Page Configuration ---
st.set_page_config(page_title="Finance Tracker", layout="wide")
st.title("💰 Dual-Account Finance Tracker")

# --- Initialize Storage ---
# We reuse the exact same logic from your finance_tracker.py
storage = CSVStorage()

# --- Initialization Check ---
if not storage.exists():
    st.warning("Ledger file not found. Please initialize your accounts.")
    with st.form("init_form"):
        col1, col2 = st.columns(2)
        ej_start = col1.number_input("EJ Personal Start", min_value=0.0, step=0.01)
        shared_start = col2.number_input("EJ & Neng Start", min_value=0.0, step=0.01)
        
        if st.form_submit_button("Initialize Ledger"):
            total = ej_start + shared_start
            initial_data = {
                'Date': datetime.now().strftime('%Y-%m-%d'),
                'Transaction': 'Initial Balance',
                'EJ Balance': round(ej_start, 2),
                'EJ & Neng Balance': round(shared_start, 2),
                'Incoming EJ': 0.0,
                'Outgoing EJ': 0.0,
                'Incoming (EJ & Neng)': 0.0,
                'Outgoing (EJ & Neng)': 0.0,
                'Total': round(total, 2)
            }
            storage.initialize(initial_data)
            st.rerun()

else:
    # --- Dashboard Metrics ---
    ej_bal, shared_bal = storage.get_last_balances()
    total = ej_bal + shared_bal

    # Display big metric cards
    c1, c2, c3 = st.columns(3)
    c1.metric("EJ Personal", f"${ej_bal:,.2f}")
    c2.metric("EJ & Neng", f"${shared_bal:,.2f}")
    c3.metric("Total Assets", f"${total:,.2f}")

    st.divider()

    # --- Add Transaction Form ---
    st.subheader("Add New Transaction")
    
    with st.form("transaction_form", clear_on_submit=True):
        col_date, col_desc = st.columns([1, 3])
        date_input = col_date.date_input("Date", datetime.now())
        desc = col_desc.text_input("Description", placeholder="e.g. Grocery Shopping")

        st.write("---")
        
        c_ej, c_shared = st.columns(2)
        with c_ej:
            st.caption("EJ Personal Account")
            inc_ej = st.number_input("Incoming (+)", min_value=0.0, step=0.01, key="inc_ej")
            out_ej = st.number_input("Outgoing (-)", min_value=0.0, step=0.01, key="out_ej")
        
        with c_shared:
            st.caption("EJ & Neng Account")
            inc_shared = st.number_input("Incoming (+)", min_value=0.0, step=0.01, key="inc_shared")
            out_shared = st.number_input("Outgoing (-)", min_value=0.0, step=0.01, key="out_shared")

        if st.form_submit_button("Save Transaction", type="primary"):
            # Calculate new balances
            new_ej = ej_bal + inc_ej - out_ej
            new_shared = shared_bal + inc_shared - out_shared
            new_total = new_ej + new_shared

            new_entry = {
                'Date': date_input.strftime('%Y-%m-%d'),
                'Transaction': desc if desc else "No Description",
                'EJ Balance': round(new_ej, 2),
                'EJ & Neng Balance': round(new_shared, 2),
                'Incoming EJ': inc_ej,
                'Outgoing EJ': out_ej,
                'Incoming (EJ & Neng)': inc_shared,
                'Outgoing (EJ & Neng)': out_shared,
                'Total': round(new_total, 2)
            }
            storage.add_entry(new_entry)
            st.success("Transaction saved!")
            st.rerun()

    # --- Ledger Table ---
    st.subheader("Ledger History")
    df = storage.get_all_transactions()
    # Show newest first, and use full width
    st.dataframe(df.iloc[::-1], use_container_width=True, hide_index=True)