import sqlite3

def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username  VARCHAR(20) UNIQUE NOT NULL,
                password VARCHAR(20) NOT NULL,
                gender TEXT NOT NULL,
                country TEXT NOT NULL,
                phone_number NUMERIC(10) NOT NULL
            )
    ''')
    conn.commit()
    conn.close()

def create_employee_table():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS employee (
                        employee_id INT NOT NULL,
                        employee_name VARCHAR(30) NOT NULL,
                        password TEXT NOT NULL,
                        department VARCHAR(50) NOT NULL,
                        date_of_joining DATE,
                        email TEXT NOT NULL,
                        country TEXT NOT NULL,
                        mobile_no NUMERIC(10) NOT NULL
                    )''')
    conn.commit()
    conn.close()

# Call the function to create the table
create_employee_table()

if __name__ == '__main__':
    init_db()
