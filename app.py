import os
import uuid
import json
from datetime import datetime, time as dt_time, timezone, timedelta
import logging
import pandas as pd
from functools import wraps
from werkzeug.utils import secure_filename
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, send_from_directory, jsonify)

from finance_tracker import FinanceTracker

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-me')
app.config['PASSWORD'] = os.environ.get('APP_PASSWORD', '082628') # Set a strong password

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}

tracker = FinanceTracker()

def run_daily_interest_check():
    """Scheduled job to automatically calculate and add daily interest."""
    # Using app.app_context() is good practice for background tasks
    # that might interact with parts of the Flask app.
    with app.app_context():
        logger.info("Running scheduled job: check_maribank_interest.")
        try:
            # The tracker is initialized globally. The Supabase client it uses
            # should be thread-safe for concurrent requests.
            tracker.check_maribank_interest()
            logger.info("Scheduled job: check_maribank_interest finished successfully.")
        except Exception as e:
            logger.error(f"Error in scheduled job: {e}")

# --- Cron Route (Replaces Scheduler for Vercel) ---
@app.route('/api/cron/daily-interest')
def cron_daily_interest():
    # Secure the cron endpoint if CRON_SECRET is set
    cron_secret = os.environ.get('CRON_SECRET')
    auth_header = request.headers.get('Authorization')
    
    if cron_secret and (not auth_header or auth_header != f"Bearer {cron_secret}"):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    run_daily_interest_check()
    return jsonify({'status': 'success', 'message': 'Daily interest check executed'})

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def safe_float(value, default=0.0):
    """Safely converts a value to float, returning default on failure."""
    if not value:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

@app.after_request
def add_security_headers(response):
    """Add basic security headers to responses."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response

# --- Helper for time formatting ---
def format_time_12hr(t):
    """Converts time string or object to 12-hour AM/PM format."""
    # Handle NaN/None/NaT robustly
    if pd.isna(t):
        return "-"
    if isinstance(t, str) and t:
        if t.strip().lower() == 'nan':
            return "-"
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
            flash('Incorrect password.', 'danger')
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

    # Replace NaN values with None so they evaluate to False in Jinja2 templates
    df = df.where(df.notnull(), None)

    last_row = df.iloc[-1]
    ej_bal, shared_bal, total = last_row['EJ Balance'], last_row['EJ & Neng Balance'], last_row['Total']

    # Recent transactions
    df_recent = df.tail(5).iloc[::-1].copy()
    # Calculate Amount treating NaNs as 0 to ensure valid totals appear
    df_recent['Amount'] = (df_recent['Incoming EJ'].fillna(0) + df_recent['Incoming (EJ & Neng)'].fillna(0)) - \
                          (df_recent['Outgoing EJ'].fillna(0) + df_recent['Outgoing (EJ & Neng)'].fillna(0))
    df_recent['Amount'] = df_recent['Amount'].apply(lambda x: "-" if (x != x or x is None) else (f"₱{x:,.2f}" if x >= 0 else f"-₱{-x:,.2f}"))
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
    
    # Add a new column for the public URL of the receipt for Vercel compatibility
    if 'Receipt' in df.columns:
        df['ReceiptURL'] = df['Receipt'].apply(
            lambda path: tracker.storage.get_receipt_url(path) if pd.notna(path) else None
        )

    # Format numeric columns: NaN becomes "-", numbers become currency
    numeric_cols = ['EJ Balance', 'EJ & Neng Balance', 'Incoming EJ', 'Outgoing EJ', 
                    'Incoming (EJ & Neng)', 'Outgoing (EJ & Neng)', 'Total']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: "-" if (x != x or x is None) else (f"₱{x:,.2f}" if x >= 0 else f"-₱{-x:,.2f}"))

    # Replace NaN values with None so they evaluate to False in Jinja2 templates
    df = df.where(df.notnull(), None)

    transactions = df.iloc[::-1].to_dict('records')
    return render_template('ledger.html', transactions=transactions, search_query=query)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        ph_tz = timezone(timedelta(hours=8))
        form = request.form
        new_entry = {
            'Date': form.get('date'),
            'Time': datetime.now(ph_tz).strftime('%I:%M:%S %p'),
            'Category': form.get('category'),
            'Transaction': form.get('description'),
            'Incoming EJ': safe_float(form.get('inc_ej')),
            'Outgoing EJ': safe_float(form.get('out_ej')),
            'Incoming (EJ & Neng)': safe_float(form.get('inc_shared')),
            'Outgoing (EJ & Neng)': safe_float(form.get('out_shared')),
            'Receipt': None
        }

        # --- Handle Receipt File Upload ---
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename != '' and allowed_file(file.filename):
                unique_filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                # Upload to Supabase Storage instead of local filesystem
                uploaded_path = tracker.storage.upload_receipt(
                    file_path=unique_filename,
                    file_stream_bytes=file.read(),
                    content_type=file.content_type
                )
                if uploaded_path:
                    new_entry['Receipt'] = uploaded_path
                else:
                    flash('Failed to upload receipt to cloud storage.', 'danger')
        
        tracker.storage.add_entry(new_entry)
        flash('Transaction added successfully!', 'success')
        return redirect(url_for('ledger'))

    ph_tz = timezone(timedelta(hours=8))
    return render_template('add.html', today=datetime.now(ph_tz).strftime('%Y-%m-%d'))

@app.route('/edit/<int:entry_id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(entry_id):
    entry = tracker.storage.get_entry(entry_id)
    if not entry: return "Entry not found", 404

    # Get public URL for the receipt for display
    if entry.get('Receipt'):
        entry['ReceiptURL'] = tracker.storage.get_receipt_url(entry['Receipt'])

    if request.method == 'POST':
        form = request.form
        data_to_update = {
            'Date': form.get('date'),
            'Category': form.get('category'),
            'Transaction': form.get('description'),
            'Incoming EJ': safe_float(form.get('inc_ej')),
            'Outgoing EJ': safe_float(form.get('out_ej')),
            'Incoming (EJ & Neng)': safe_float(form.get('inc_shared')),
            'Outgoing (EJ & Neng)': safe_float(form.get('out_shared')),
        }

        # --- Handle Receipt File Upload on Edit ---
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename != '' and allowed_file(file.filename):
                # Delete old file from Supabase if it exists
                if entry.get('Receipt'):
                    tracker.storage.delete_receipt(entry['Receipt'])
                
                # Save new file to Supabase
                unique_filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                uploaded_path = tracker.storage.upload_receipt(
                    file_path=unique_filename,
                    file_stream_bytes=file.read(),
                    content_type=file.content_type
                )
                if uploaded_path:
                    data_to_update['Receipt'] = uploaded_path
                else:
                    flash('Failed to upload new receipt to cloud storage.', 'danger')

        tracker.storage.update_entry(entry_id, data_to_update)
        flash('Transaction updated successfully!', 'success')
        return redirect(url_for('ledger'))

    return render_template('edit.html', entry=entry)

@app.route('/edit/<int:entry_id>/delete_receipt')
@login_required
def delete_receipt(entry_id):
    entry = tracker.storage.get_entry(entry_id)
    if entry and entry.get('Receipt'):
        # Delete from Supabase Storage
        if tracker.storage.delete_receipt(entry['Receipt']):
            # Update database to set receipt to null
            tracker.storage.update_entry(entry_id, {'Receipt': None}, recalculate=False)
            flash('Receipt deleted successfully.', 'info')
        else:
            flash('Failed to delete receipt from cloud storage.', 'danger')
    else:
        flash('No receipt found to delete.', 'warning')
    return redirect(url_for('edit_transaction', entry_id=entry_id))

@app.route('/delete/<int:entry_id>')
@login_required
def delete_transaction(entry_id):
    entry = tracker.storage.get_entry(entry_id)
    if entry and entry.get('Receipt'):
        # Delete associated receipt from Supabase Storage
        tracker.storage.delete_receipt(entry['Receipt'])

    tracker.storage.delete_entry(entry_id)
    flash('Transaction and any associated receipt have been deleted.', 'info')
    return redirect(url_for('ledger'))

@app.route('/export')
@login_required
def export_csv():
    df = tracker.storage.get_all_transactions()
    return df.to_csv(index=False), 200, {
        'Content-Type': 'text/csv',
        'Content-Disposition': 'attachment; filename=finance_ledger.csv'
    }

# --- Chat Routes ---
@app.route('/chat/join', methods=['GET', 'POST'])
@login_required
def chat_join():
    # Handle nickname setting from base.html widget
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if data and 'nickname' in data:
            session['chat_nickname'] = data['nickname']
            return jsonify({'status': 'success'})

    messages = tracker.storage.get_chat_messages()
    
    # Format timestamps for display (UTC+8)
    ph_tz = timezone(timedelta(hours=8))
    for msg in messages:
        if msg.get('created_at'):
            try:
                dt = datetime.fromisoformat(msg['created_at'].replace('Z', '+00:00'))
                msg['time'] = dt.astimezone(ph_tz).strftime('%b %d, %I:%M %p')
            except:
                pass

    return jsonify(messages)

@app.route('/chat/send', methods=['POST'])
@login_required
def chat_send():
    data = request.json
    nickname = data.get('nickname', 'Anonymous')
    message = data.get('message', '')
    if message:
        tracker.storage.add_chat_message(nickname, message)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 400

@app.route('/chat/leave')
def chat_leave():
    session.pop('chat_nickname', None)
    return jsonify({'status': 'success'})

@app.route('/chat')
@login_required
def chat_page():
    return render_template('chat.html')

if __name__ == '__main__':
    app.run(debug=True)