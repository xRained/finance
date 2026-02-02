from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from finance_tracker import SupabaseStorage
from datetime import datetime, timedelta
from functools import wraps
import os
import json
import pandas as pd

# Set template_folder to '.' to find HTML files in the current directory
app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24)) # Required for session management

# Security Configuration
ADMIN_PASSWORD = "082628"

storage = SupabaseStorage()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def check_maribank_interest():
    try:
        if not storage.exists():
            return

        df = storage.get_all_transactions()
        if df.empty:
            return

        # Ensure data is sorted by date for accurate balance history
        df['DateObj'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df.dropna(subset=['DateObj'])
        
        if 'ID' in df.columns:
            df = df.sort_values(by=['DateObj', 'ID'])
        else:
            df = df.sort_values(by='DateObj')

        # Filter for Maribank Interest transactions
        interest_df = df[df['Transaction'].str.strip() == 'Maribank Interest']
        
        today = datetime.now().date()
        
        if not interest_df.empty:
            last_interest_date = interest_df['DateObj'].max().date()
        else:
            # Start from yesterday if no history
            last_interest_date = today - timedelta(days=1)

        next_date = last_interest_date + timedelta(days=1)
        session_interest = 0.0
        entries_added = False
        
        while next_date <= today:
            next_date_str = next_date.strftime('%Y-%m-%d')
            
            # Check for duplicates
            if not df[(df['Date'] == next_date_str) & (df['Transaction'].str.strip() == 'Maribank Interest')].empty:
                next_date += timedelta(days=1)
                continue
                
            # Balance from previous day
            balance_date = next_date - timedelta(days=1)
            mask = df['DateObj'].dt.date <= balance_date
            past_df = df.loc[mask]
            
            if not past_df.empty:
                last_row = past_df.iloc[-1]
                total_assets = float(last_row.get('Total', 0) or 0)
                current_total_balance = total_assets + session_interest
                
                if current_total_balance > 0:
                    # 1. Determine Tiered Interest Rate
                    # 3.25% for first 1M, 3.75% for any amount over 1M
                    tier_limit = 1000000
                    if current_total_balance <= tier_limit:
                        daily_gross = (current_total_balance * 0.0325) / 365
                    else:
                        # Calculate interest for the first 1M at 3.25%
                        tier1_interest = (tier_limit * 0.0325) / 365
                        # Calculate interest for the excess at 3.75%
                        excess_balance = current_total_balance - tier_limit
                        tier2_interest = (excess_balance * 0.0375) / 365
                        daily_gross = tier1_interest + tier2_interest
                    
                    # 2. Apply 20% Philippine Withholding Tax
                    tax_amount = daily_gross * 0.20
                    net_interest = round(daily_gross - tax_amount, 2)
                    
                    # 3. Log only if it meets the 1 centavo minimum credit threshold
                    if net_interest >= 0.01:
                        new_entry = {
                            'Date': next_date_str,
                            'Transaction': 'Maribank Interest',
                            'Category': 'Interest',
                            'EJ Balance': 0, 
                            'EJ & Neng Balance': 0, 
                            'Incoming EJ': 0.0,
                            'Outgoing EJ': 0.0,
                            'Incoming (EJ & Neng)': net_interest,
                            'Outgoing (EJ & Neng)': 0.0,
                            'Total': 0 
                        }
                        storage.add_entry(new_entry, recalculate=False)
                        session_interest += net_interest
                        entries_added = True
            
            next_date += timedelta(days=1)
        
        if entries_added:
            storage.recalculate_balances()
    except Exception as e:
        print(f"Error in check_maribank_interest: {e}")

@app.route('/')
@login_required
def index():
    if not storage.exists():
        return redirect(url_for('initialize'))
    
    check_maribank_interest()
    
    ej_bal, shared_bal = storage.get_last_balances()
    total = ej_bal + shared_bal
    
    # Get recent transactions for display (top 5)
    df = storage.get_all_transactions()
    
    # Prepare Chart Data
    chart_labels = []
    chart_ej = []
    chart_shared = []
    
    # Prepare Category Data for Pie Chart
    category_data = {}
    
    if not df.empty:
        chart_labels = df['Date'].tolist()
        chart_ej = df['EJ Balance'].tolist()
        chart_shared = df['EJ & Neng Balance'].tolist()
        
        # Aggregate expenses by category
        for index, row in df.iterrows():
            cat = row.get('Category', 'Other')
            expense = (row['Outgoing EJ'] or 0) + (row['Outgoing (EJ & Neng)'] or 0)
            if expense > 0:
                category_data[cat] = category_data.get(cat, 0) + expense

    recent = []
    if not df.empty:
        recent = df.tail(5).to_dict('records')
        recent = recent[::-1] # Reverse to show newest first
        
        for tx in recent:
            def get_val(key):
                v = tx.get(key)
                return float(v) if pd.notna(v) else 0.0

            inc = get_val('Incoming EJ') + get_val('Incoming (EJ & Neng)')
            out = get_val('Outgoing EJ') + get_val('Outgoing (EJ & Neng)')
            
            if inc > 0 and out > 0:
                tx['Amount'] = f"+{inc:,.2f} | -{out:,.2f}"
            elif inc > 0:
                tx['Amount'] = f"+{inc:,.2f}"
            elif out > 0:
                tx['Amount'] = f"-{out:,.2f}"
            else:
                tx['Amount'] = "-"

    return render_template('index.html', ej_bal=ej_bal, shared_bal=shared_bal, total=total, recent=recent,
                           chart_labels=json.dumps(chart_labels), chart_ej=json.dumps(chart_ej), 
                           chart_shared=json.dumps(chart_shared),
                           cat_labels=json.dumps(list(category_data.keys())),
                           cat_data=json.dumps(list(category_data.values())))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Invalid password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/initialize', methods=['GET', 'POST'])
@login_required
def initialize():
    if request.method == 'POST':
        try:
            ej_start = float(request.form.get('ej_start', 0))
            shared_start = float(request.form.get('shared_start', 0))
        except ValueError:
            return "Invalid input", 400

        total = ej_start + shared_start
        initial_data = {
            'Date': datetime.now().strftime('%Y-%m-%d'),
            'Time': datetime.now().strftime('%H:%M:%S'),
            'Transaction': 'Initial Balance',
            'Category': 'Initial',
            'EJ Balance': round(ej_start, 2),
            'EJ & Neng Balance': round(shared_start, 2),
            'Incoming EJ': 0.0,
            'Outgoing EJ': 0.0,
            'Incoming (EJ & Neng)': 0.0,
            'Outgoing (EJ & Neng)': 0.0,
            'Total': round(total, 2)
        }
        storage.initialize(initial_data)
        return redirect(url_for('index'))
        
    return render_template('init.html')

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if not storage.exists():
        return redirect(url_for('initialize'))

    if request.method == 'POST':
        date_input = request.form.get('date')
        if not date_input:
            date_input = datetime.now().strftime('%Y-%m-%d')
        
        description = request.form.get('description', 'No Description')
        category = request.form.get('category', 'Other')
        
        try:
            inc_ej = float(request.form.get('inc_ej', 0) or 0)
            out_ej = float(request.form.get('out_ej', 0) or 0)
            inc_shared = float(request.form.get('inc_shared', 0) or 0)
            out_shared = float(request.form.get('out_shared', 0) or 0)
        except ValueError:
            return "Invalid amounts", 400

        prev_ej, prev_shared = storage.get_last_balances()
        
        new_ej = prev_ej + inc_ej - out_ej
        new_shared = prev_shared + inc_shared - out_shared
        total = new_ej + new_shared

        new_entry = {
            'Date': date_input,
            'Time': datetime.now().strftime('%H:%M:%S'),
            'Transaction': description,
            'Category': category,
            'EJ Balance': round(new_ej, 2),
            'EJ & Neng Balance': round(new_shared, 2),
            'Incoming EJ': inc_ej,
            'Outgoing EJ': out_ej,
            'Incoming (EJ & Neng)': inc_shared,
            'Outgoing (EJ & Neng)': out_shared,
            'Total': round(total, 2)
        }
        
        storage.add_entry(new_entry)
        return redirect(url_for('index'))

    return render_template('add.html', today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(id):
    entry = storage.get_entry(id)
    if not entry:
        return redirect(url_for('view_ledger'))

    if request.method == 'POST':
        try:
            update_data = {
                'Date': request.form.get('date'),
                'Transaction': request.form.get('description'),
                'Category': request.form.get('category'),
                'Incoming EJ': float(request.form.get('inc_ej', 0) or 0),
                'Outgoing EJ': float(request.form.get('out_ej', 0) or 0),
                'Incoming (EJ & Neng)': float(request.form.get('inc_shared', 0) or 0),
                'Outgoing (EJ & Neng)': float(request.form.get('out_shared', 0) or 0)
            }
            # We don't calculate balances here; storage.update_entry handles recalculation
            storage.update_entry(id, update_data)
            flash('Transaction updated successfully!')
            return redirect(url_for('view_ledger'))
        except ValueError:
            flash('Invalid input')

    return render_template('edit.html', entry=entry)

@app.route('/delete/<int:id>')
@login_required
def delete_transaction(id):
    storage.delete_entry(id)
    flash('Transaction deleted.')
    return redirect(url_for('view_ledger'))

@app.route('/ledger')
@login_required
def view_ledger():
    if not storage.exists():
        return redirect(url_for('initialize'))
        
    df = storage.get_all_transactions()
    transactions = df.to_dict('records') if not df.empty else []
    return render_template('ledger.html', transactions=transactions[::-1])

@app.route('/chat')
@login_required
def chat():
    if 'chat_nickname' not in session:
        return redirect(url_for('chat_join'))
    return render_template('chat.html', nickname=session['chat_nickname'])

@app.route('/chat/join', methods=['GET', 'POST'])
@login_required
def chat_join():
    if request.method == 'POST':
        # Handle AJAX request from floating widget
        if request.is_json:
            data = request.get_json()
            nickname = data.get('nickname')
            if nickname:
                session['chat_nickname'] = nickname
                return jsonify({'status': 'success'})
            return jsonify({'error': 'Nickname required'}), 400
            
        nickname = request.form.get('nickname')
        if nickname:
            session['chat_nickname'] = nickname
            return redirect(url_for('chat'))
    return render_template('chat_join.html')

@app.route('/chat/leave')
@login_required
def chat_leave():
    session.pop('chat_nickname', None)
    return redirect(request.referrer or url_for('index'))

@app.route('/api/chat', methods=['GET', 'POST'])
@login_required
def chat_api():
    if 'chat_nickname' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    if request.method == 'POST':
        data = request.get_json()
        msg = data.get('message')
        if msg:
            storage.add_chat_message(session['chat_nickname'], msg)
        return jsonify({"status": "success"})
    
    messages = storage.get_chat_messages()
    return jsonify({"messages": messages})

@app.route('/export')
@login_required
def export_data():
    if not storage.exists():
        return redirect(url_for('initialize'))
        
    df = storage.get_all_transactions()
    return Response(
        df.to_csv(index=False, encoding='utf-8-sig'),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=finance_ledger_{datetime.now().strftime('%Y-%m-%d')}.csv"}
    )

if __name__ == '__main__':
    app.run(debug=True)