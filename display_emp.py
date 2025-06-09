import sqlite3
from tabulate import tabulate

def get_db_connection():
    conn = sqlite3.connect('users.db')
    conn.row_factory = sqlite3.Row
    return conn

def fetch_users():
    conn = get_db_connection()
    users = conn.execute('SELECT * FROM employee').fetchall()
    conn.close()
    return users

def display_users(users):
    headers = users[0].keys() if users else ["No data found"]
    table = [list(user) for user in users]
    print(tabulate(table, headers, tablefmt="grid"))

if __name__ == '__main__':
    users = fetch_users()
    display_users(users)
