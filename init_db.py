import sqlite3
import os

def init_db():
    # Remove existing database if it exists
    if os.path.exists('users.db'):
        os.remove('users.db')
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # Create employee table with role column
    c.execute('''
        CREATE TABLE IF NOT EXISTS employee (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            department TEXT NOT NULL,
            gender TEXT NOT NULL,
            date_of_joining TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            country TEXT NOT NULL,
            mobile_no TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'employee'
        )
    ''')
    
    # Create tasks table for daily activities
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_date DATE NOT NULL,
            employee_id INTEGER NOT NULL,
            employee_name TEXT NOT NULL,
            project_site TEXT NOT NULL,
            in_time TIME NOT NULL,
            out_time TIME NOT NULL,
            task TEXT NOT NULL,
            remarks TEXT,
            username TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES employee (username)
        )
    ''')
    
    # Insert default HR admin user if not exists
    try:
        c.execute('''
            INSERT INTO employee (
                employee_name, username, password, department, gender, 
                date_of_joining, email, country, mobile_no, role
            ) VALUES (
                'HR Admin', 'hr_admin', 'admin123', 'HR', 'Other',
                date('now'), 'hr@company.com', 'Uganda', '1234567890', 'hr'
            )
        ''')
        conn.commit()
    except sqlite3.IntegrityError:
        # User already exists
        pass
    
    conn.close()
    print("Database initialized successfully!")

if __name__ == '__main__':
    init_db() 