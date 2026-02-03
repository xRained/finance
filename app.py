import os
import uuid
import json
from datetime import datetime, time as dt_time
from functools import wraps
from werkzeug.utils import secure_filename
from flask import (Flask, render_template, request, redirect, url_for, flash, 
                   session, send_from_directory, jsonify)

from finance_tracker import FinanceTracker

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-me')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['PASSWORD'] = os.environ.get('APP_PASSWORD', '082628') # Set a strong password

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

tracker = FinanceTracker()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Helper for time formatting ---
def format_time_12hr(t):
    """Converts time string or object to 12-hour AM/PM format."""
    if isinstance(t, str) and t:
        try:
            # Handles "HH:MM:SS"
            return datetime.strptime(t, '%H:%M:%S').strftime('%I:%M:%S %p')
        except ValueError:
            try:
                # Handles "HH:MM:SS.ffffff" which might come from DB
                return datetime.strptime(t.split('.')[0], '%H:%M:%S').strftime('%I:%M:%S %p')
            except (ValueError, IndexError):
                # If split fails or parse fails, it's likely already formatted or invalid
                return t
    elif isinstance(t, dt_time):
        # Handles datetime.time objects
        return t.strftime('%I:%M:%S %p')
    # For None, NaT, etc., return as is
    return t

# --- Authentication ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == app.config['PASSWORD']:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Incorrect password.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- Main Routes ---
@app.route('/')
@login_required
def index():
    df = tracker.storage.get_all_transactions()
    if df.empty or len(df) < 2:
        return render_template('init.html')

    # Reformat time for display
    if 'Time' in df.columns:
        df['Time'] = df['Time'].apply(format_time_12hr)

    last_row = df.iloc[-1]
    ej_bal, shared_bal, total = last_row['EJ Balance'], last_row['EJ & Neng Balance'], last_row['Total']

    # Recent transactions
    df_recent = df.tail(5).iloc[::-1].copy()
    df_recent['Amount'] = (df_recent['Incoming EJ'] + df_recent['Incoming (EJ & Neng)']) - (df_recent['Outgoing EJ'] + df_recent['Outgoing (EJ & Neng)'])
    df_recent['Amount'] = df_recent['Amount'].apply(lambda x: f"₱{x:,.2f}" if x > 0 else f"-₱{-x:,.2f}")
    recent = df_recent.to_dict('records')

    # Chart data
    chart_df = df.tail(30)
    chart_labels = [f"{d.split('-')[1]}-{d.split('-')[2]}" for d in chart_df['Date']]

    # Category data (Smart: Filter for Current Month Only)
    current_month_str = datetime.now().strftime('%Y-%m')
    # Create a temporary column for filtering (safe string slicing)
    df['Month_Str'] = df['Date'].astype(str).str.slice(0, 7)
    df_month = df[df['Month_Str'] == current_month_str].copy()
    
    # Fallback: If no data for this month yet, show last 30 transactions to avoid empty charts
    target_df = df_month if not df_month.empty else df.tail(30)

    expenses_df = target_df[(target_df['Outgoing EJ'] > 0) | (target_df['Outgoing (EJ & Neng)'] > 0)].copy()
    expenses_df['Total Outgoing'] = expenses_df['Outgoing EJ'] + expenses_df['Outgoing (EJ & Neng)']
    cat_summary = expenses_df.groupby('Category')['Total Outgoing'].sum().sort_values(ascending=False)

    return render_template('index.html', 
                           ej_bal=ej_bal, shared_bal=shared_bal, total=total, recent=recent,
                           chart_labels=json.dumps(chart_labels),
                           chart_ej=json.dumps(chart_df['EJ Balance'].tolist()),
                           chart_shared=json.dumps(chart_df['EJ & Neng Balance'].tolist()),
                           cat_labels=json.dumps(cat_summary.index.tolist()),
                           cat_data=json.dumps(cat_summary.values.tolist()))

@app.route('/ledger')
@login_required
def ledger():
    df = tracker.storage.get_all_transactions()
    
    # Feature: Search Functionality
    query = request.args.get('q')
    if query:
        # Case-insensitive search across all columns
        mask = df.apply(lambda x: x.astype(str).str.contains(query, case=False, na=False)).any(axis=1)
        df = df[mask]

    # Reformat time for display
    if 'Time' in df.columns:
        df['Time'] = df['Time'].apply(format_time_12hr)
    
    transactions = df.iloc[::-1].to_dict('records')
    return render_template('ledger.html', transactions=transactions, search_query=query)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        form = request.form
        new_entry = {
            'Date': form.get('date'),
            'Time': datetime.now().strftime('%I:%M:%S %p'),
            'Category': form.get('category'),
            'Transaction': form.get('description'),
            'Incoming EJ': float(form.get('inc_ej') or 0),
            'Outgoing EJ': float(form.get('out_ej') or 0),
            'Incoming (EJ & Neng)': float(form.get('inc_shared') or 0),
            'Outgoing (EJ & Neng)': float(form.get('out_shared') or 0),
            'Receipt': None
        }

        # --- Handle Receipt File Upload ---
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                new_entry['Receipt'] = unique_filename
        
        tracker.storage.add_entry(new_entry)
        flash('Transaction added successfully!')
        return redirect(url_for('ledger'))

    return render_template('add.html', today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/edit/<int:entry_id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(entry_id):
    entry = tracker.storage.get_entry(entry_id)
    if not entry: return "Entry not found", 404

    if request.method == 'POST':
        form = request.form
        data_to_update = {
            'Date': form.get('date'),
            'Category': form.get('category'),
            'Transaction': form.get('description'),
            'Incoming EJ': float(form.get('inc_ej') or 0),
            'Outgoing EJ': float(form.get('out_ej') or 0),
            'Incoming (EJ & Neng)': float(form.get('inc_shared') or 0),
            'Outgoing (EJ & Neng)': float(form.get('out_shared') or 0),
        }

        # --- Handle Receipt File Upload on Edit ---
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename != '' and allowed_file(file.filename):
                # Delete old file if it exists
                if entry.get('Receipt'):
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], entry['Receipt'])
                    if os.path.exists(old_path):
                        os.remove(old_path)
                
                # Save new file
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                data_to_update['Receipt'] = unique_filename

        tracker.storage.update_entry(entry_id, data_to_update)
        flash('Transaction updated successfully!')
        return redirect(url_for('ledger'))

    return render_template('edit.html', entry=entry)

@app.route('/edit/<int:entry_id>/delete_receipt')
@login_required
def delete_receipt(entry_id):
    entry = tracker.storage.get_entry(entry_id)
    if entry and entry.get('Receipt'):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], entry['Receipt'])
        if os.path.exists(filepath):
            os.remove(filepath)
        
        # Update database to set receipt to null
        tracker.storage.update_entry(entry_id, {'Receipt': None}, recalculate=False)
        flash('Receipt deleted successfully.')
    else:
        flash('No receipt found to delete.', 'warning')
    return redirect(url_for('edit_transaction', entry_id=entry_id))

@app.route('/delete/<int:entry_id>')
@login_required
def delete_transaction(entry_id):
    entry = tracker.storage.get_entry(entry_id)
    if entry and entry.get('Receipt'):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], entry['Receipt'])
        if os.path.exists(filepath):
            os.remove(filepath)

    tracker.storage.delete_entry(entry_id)
    flash('Transaction and any associated receipt have been deleted.')
    return redirect(url_for('ledger'))

@app.route('/export')
@login_required
def export_csv():
    df = tracker.storage.get_all_transactions()
    return df.to_csv(index=False), 200, {
        'Content-Type': 'text/csv',
        'Content-Disposition': 'attachment; filename=finance_ledger.csv'
    }

if __name__ == '__main__':
    app.run(debug=True)