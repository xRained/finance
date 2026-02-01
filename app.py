from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from finance_tracker import SupabaseStorage
from datetime import datetime
from functools import wraps
import os
import json

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

@app.route('/')
@login_required
def index():
    if not storage.exists():
        return redirect(url_for('initialize'))
    
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