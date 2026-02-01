import pandas as pd
from tabulate import tabulate
import os
from datetime import datetime

class CSVStorage:
    """Handles data persistence using a CSV file."""
    def __init__(self, filename='finance_ledger.csv'):
        self.filename = filename
        self.columns = [
            'Date', 'Transaction', 
            'EJ Balance', 'Shared Balance', 
            'Incoming EJ', 'Outgoing EJ', 
            'Incoming Shared', 'Outgoing Shared', 
            'Total'
        ]

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
                return last_row['EJ Balance'], last_row['Shared Balance']
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

class FinanceTracker:
    def __init__(self):
        # To use Supabase later, you would swap CSVStorage() with SupabaseStorage() here
        self.storage = CSVStorage()
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
            shared_start = float(input("Enter starting balance for Shared Future: "))
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
            'Shared Balance': round(shared_start, 2),
            'Incoming EJ': 0.0,
            'Outgoing EJ': 0.0,
            'Incoming Shared': 0.0,
            'Outgoing Shared': 0.0,
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
        inc_shared = get_amount("Incoming (Shared): ")
        out_shared = get_amount("Outgoing (Shared): ")

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
            'Shared Balance': round(new_shared, 2),
            'Incoming EJ': inc_ej,
            'Outgoing EJ': out_ej,
            'Incoming Shared': inc_shared,
            'Outgoing Shared': out_shared,
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
