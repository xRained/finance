import pandas as pd
from tabulate import tabulate
import os
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

class CSVStorage:
    """Handles data persistence using a CSV file."""
    def __init__(self, filename='finance_ledger.csv'):
        self.filename = filename
        self.columns = [
            'Date', 'Time', 'Transaction', 
            'EJ Balance', 'EJ & Neng Balance', 
            'Incoming EJ', 'Outgoing EJ', 
            'Incoming (EJ & Neng)', 'Outgoing (EJ & Neng)', 
            'Total'
        ]
        self._migrate_schema()

    def _migrate_schema(self):
        """Updates old column names to new ones if they exist."""
        if self.exists():
            try:
                df = pd.read_csv(self.filename)
                rename_map = {
                    'Shared Balance': 'EJ & Neng Balance',
                    'Incoming Shared': 'Incoming (EJ & Neng)',
                    'Outgoing Shared': 'Outgoing (EJ & Neng)'
                }
                if any(old in df.columns for old in rename_map):
                    df.rename(columns=rename_map, inplace=True)
                    df.to_csv(self.filename, index=False)
            except Exception as e:
                print(f"Migration warning: {e}")

    def exists(self):
        return os.path.exists(self.filename)

    def initialize(self, initial_data):
        df = pd.DataFrame([initial_data])
        df = df[self.columns]
        df.to_csv(self.filename, index=False)
        print(f"Ledger initialized and saved to '{self.filename}'.")

    def get_last_balances(self):
        try:
            df = pd.read_csv(self.filename)
            if not df.empty:
                last_row = df.iloc[-1]
                return last_row['EJ Balance'], last_row['EJ & Neng Balance']
        except Exception as e:
            print(f"Error reading ledger: {e}")
        return 0.0, 0.0

    def add_entry(self, entry_data):
        new_df = pd.DataFrame([entry_data])
        new_df = new_df[self.columns]
        new_df.to_csv(self.filename, mode='a', header=False, index=False)

    def get_all_transactions(self):
        if self.exists():
            return pd.read_csv(self.filename)
        return pd.DataFrame(columns=self.columns)

class SupabaseStorage:
    """Handles data persistence using Supabase."""
    def __init__(self):
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            # Fallback or error if credentials are missing
            print("Warning: SUPABASE_URL or SUPABASE_KEY not found in environment.")
        
        self.supabase: Client = create_client(url, key) if url and key else None
        self.table = "finance_ledger"
        
        # Map App names (Title Case) to DB names (snake_case)
        self.col_map = {
            'ID': 'id',
            'Date': 'date',
            'Time': 'time',
            'Category': 'category',
            'Transaction': 'description',
            'EJ Balance': 'ej_balance',
            'EJ & Neng Balance': 'ej_neng_balance',
            'Incoming EJ': 'incoming_ej',
            'Outgoing EJ': 'outgoing_ej',
            'Incoming (EJ & Neng)': 'incoming_ej_neng',
            'Outgoing (EJ & Neng)': 'outgoing_ej_neng',
            'Total': 'total'
        }
        # Reverse map for reading back
        self.rev_map = {v: k for k, v in self.col_map.items()}

    def exists(self):
        if not self.supabase: return False
        try:
            # Check if we can fetch at least one row or if table exists
            res = self.supabase.table(self.table).select("id", count="exact").limit(1).execute()
            return res.count > 0
        except Exception:
            return False

    def initialize(self, initial_data):
        # Convert Title Case data to snake_case for DB
        db_data = {self.col_map.get(k, k): v for k, v in initial_data.items()}
        self.supabase.table(self.table).insert(db_data).execute()
        print("Ledger initialized in Supabase.")

    def get_last_balances(self):
        # Get the most recent entry
        res = self.supabase.table(self.table).select("*").order("id", desc=True).limit(1).execute()
        if res.data:
            row = res.data[0]
            return row['ej_balance'], row['ej_neng_balance']
        return 0.0, 0.0

    def add_entry(self, entry_data):
        db_data = {self.col_map.get(k, k): v for k, v in entry_data.items()}
        self.supabase.table(self.table).insert(db_data).execute()
        self.recalculate_balances()

    def get_entry(self, entry_id):
        res = self.supabase.table(self.table).select("*").eq("id", entry_id).execute()
        if res.data:
            # Convert back to App keys
            return {self.rev_map.get(k, k): v for k, v in res.data[0].items()}
        return None

    def update_entry(self, entry_id, data):
        # Convert to DB keys
        db_data = {self.col_map.get(k, k): v for k, v in data.items() if k in self.col_map}
        self.supabase.table(self.table).update(db_data).eq("id", entry_id).execute()
        self.recalculate_balances()

    def delete_entry(self, entry_id):
        self.supabase.table(self.table).delete().eq("id", entry_id).execute()
        self.recalculate_balances()

    def recalculate_balances(self):
        """Recalculates running balances for all transactions to ensure consistency."""
        # Fetch all rows ordered by date and ID
        res = self.supabase.table(self.table).select("*").order("date", desc=False).order("id", desc=False).execute()
        rows = res.data
        
        ej_bal = 0.0
        shared_bal = 0.0
        updates = []

        for row in rows:
            # Calculate new running totals
            ej_bal += (row.get('incoming_ej') or 0) - (row.get('outgoing_ej') or 0)
            shared_bal += (row.get('incoming_ej_neng') or 0) - (row.get('outgoing_ej_neng') or 0)
            total = ej_bal + shared_bal
            
            # Only update if numbers changed (using small epsilon for float comparison)
            if abs((row.get('ej_balance') or 0) - ej_bal) > 0.01 or \
               abs((row.get('ej_neng_balance') or 0) - shared_bal) > 0.01:
                row['ej_balance'] = ej_bal
                row['ej_neng_balance'] = shared_bal
                row['total'] = total
                updates.append(row)
        
        if updates:
            self.supabase.table(self.table).upsert(updates).execute()

    def get_all_transactions(self):
        res = self.supabase.table(self.table).select("*").order("id", desc=False).execute()
        if res.data:
            # Convert DB snake_case back to App Title Case
            converted = [{self.rev_map.get(k, k): v for k, v in row.items()} for row in res.data]
            return pd.DataFrame(converted)
        return pd.DataFrame(columns=self.col_map.keys())

    def get_chat_messages(self):
        if not self.supabase: return []
        try:
            # Fetch last 50 messages, oldest first
            res = self.supabase.table("chat_messages").select("*").order("created_at", desc=False).limit(50).execute()
            return res.data if res.data else []
        except Exception as e:
            print(f"Chat error: {e}")
            return []

    def add_chat_message(self, nickname, message):
        if not self.supabase: return
        try:
            self.supabase.table("chat_messages").insert({
                "nickname": nickname, 
                "message": message,
                "created_at": datetime.now().isoformat()
            }).execute()
        except Exception as e:
            print(f"Chat add error: {e}")

class FinanceTracker:
    def __init__(self):
        # Switched to SupabaseStorage as requested
        self.storage = SupabaseStorage()
        self.check_file()

    def check_file(self):
        """Checks if the CSV exists; if not, initializes it with starting balances."""
        if not self.storage.exists():
            print("Storage not initialized. Starting setup...")
            self.initialize_ledger()

    def initialize_ledger(self):
        """Sets initial balances and creates the CSV file."""
        print("\n--- Initial Setup ---")
        try:
            ej_start = float(input("Enter starting balance for EJ Personal: "))
            shared_start = float(input("Enter starting balance for EJ & Neng: "))
        except ValueError:
            print("Invalid input. Defaulting balances to 0.0.")
            ej_start = 0.0
            shared_start = 0.0
        
        total = ej_start + shared_start
        
        # Create the initial entry row
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
        
        self.storage.initialize(initial_data)

    def add_transaction(self):
        """Prompts user for transaction details, calculates new balances, and saves to CSV."""
        print("\n--- Add New Transaction ---")
        
        # Date Input
        date_input = input("Date (YYYY-MM-DD) [Press Enter for Today]: ").strip()
        if not date_input:
            date_input = datetime.now().strftime('%Y-%m-%d')
        
        # Description Input
        description = input("Description (Transaction): ").strip()
        
        # Helper function to handle numeric input safely
        def get_amount(prompt):
            val = input(prompt).strip()
            if not val:
                return 0.0
            try:
                return float(val)
            except ValueError:
                print("Invalid number, treating as 0.0")
                return 0.0

        # Amount Inputs
        inc_ej = get_amount("Incoming (EJ): ")
        out_ej = get_amount("Outgoing (EJ): ")
        inc_shared = get_amount("Incoming (EJ & Neng): ")
        out_shared = get_amount("Outgoing (EJ & Neng): ")

        prev_ej, prev_shared = self.storage.get_last_balances()

        # Automatic Balancing Logic
        # New EJ Balance = Previous EJ Balance + Incoming (EJ) - Outgoing (EJ)
        new_ej = prev_ej + inc_ej - out_ej
        
        # New Shared Balance = Previous Shared Balance + Incoming (Shared) - Outgoing (Shared)
        new_shared = prev_shared + inc_shared - out_shared
        
        # Total = EJ Balance + Shared Balance
        total = new_ej + new_shared

        # Create new record
        new_entry = {
            'Date': date_input,
            'Transaction': description,
            'EJ Balance': round(new_ej, 2),
            'EJ & Neng Balance': round(new_shared, 2),
            'Incoming EJ': inc_ej,
            'Outgoing EJ': out_ej,
            'Incoming (EJ & Neng)': inc_shared,
            'Outgoing (EJ & Neng)': out_shared,
            'Total': round(total, 2)
        }

        self.storage.add_entry(new_entry)
        print("Transaction added successfully.")

    def view_ledger(self):
        """Displays the ledger history in the requested table format."""
        try:
            df = self.storage.get_all_transactions()
            if df.empty:
                print("No transactions found.")
                return

            print("\n" + "="*30 + " LEDGER HISTORY " + "="*30)
            
            # Use tabulate to print the dataframe in a pretty grid format
            # headers='keys' uses the DataFrame column names
            # tablefmt='psql' creates a nice border similar to SQL output
            print(tabulate(df, headers='keys', tablefmt='psql', showindex=False))
            
        except Exception as e:
            print(f"Error displaying ledger: {e}")

def main():
    tracker = FinanceTracker()
    
    while True:
        print("\n=== Dual-Account Finance Tracker ===")
        print("1. Add Transaction")
        print("2. View Ledger")
        print("3. Exit")
        
        choice = input("Select an option: ").strip()
        
        if choice == '1':
            tracker.add_transaction()
        elif choice == '2':
            tracker.view_ledger()
        elif choice == '3':
            print("Exiting...")
            break
        else:
            print("Invalid option. Please try again.")

if __name__ == "__main__":
    main()
