from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
import sqlite3
from datetime import timedelta
import os

app = Flask(__name__)
# Use environment variable for secret key in production
app.secret_key = os.environ.get('SECRET_KEY', 'login!123')
# Set session timeout to 30 minutes
app.permanent_session_lifetime = timedelta(minutes=30)

def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS employee (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_name VARCHAR(30) NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            department VARCHAR(50) NOT NULL,
            gender TEXT NOT NULL,
            date_of_joining DATE NOT NULL,
            email TEXT NOT NULL UNIQUE,
            country TEXT NOT NULL,
            mobile_no NUMERIC(10) NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect('users.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('signin'))

@app.route('/home')
def home():
    if 'username' not in session:
        return redirect(url_for('signin'))
    return redirect(url_for('dashboard'))

@app.route('/styles/<path:filename>')
def styles(filename):
    return send_from_directory('styles/css', filename)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        employee_name = request.form['employee_name']
        username = request.form['username']
        password = request.form['password']
        verify_password = request.form['verify_password']
        department = request.form['department']
        gender = request.form['gender']
        date_of_joining = request.form['date_of_joining']
        email = request.form['email']
        country = request.form['country']
        mobile_no = request.form['mobile_no']
        
        if password != verify_password:
            flash('Passwords do not match.')
            return redirect(url_for('signup'))
        
        conn = get_db_connection()
        try:
            conn.execute('''INSERT INTO employee 
                            (employee_name, username, password, department, gender, date_of_joining, email, country, mobile_no, role) 
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (employee_name, username, password, department, gender, date_of_joining, email, country, mobile_no, 'employee'))
            conn.commit()
            flash('You have successfully signed up! Please sign in.')
            return redirect(url_for('signin'))
        except sqlite3.IntegrityError:
            flash('Username or Email already exists.')
            return redirect(url_for('signup'))
        finally:
            conn.close()
    return render_template('signup.html')

@app.route('/signin', methods=['POST', 'GET'])
def signin():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM employee WHERE username = ? AND password = ?', (username, password)).fetchone()
        conn.close()
        
        if user:
            # Make the session permanent and set timeout
            session.permanent = True
            session['username'] = username
            session['role'] = user['role']
            session['department'] = user['department']
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.')
            return redirect(url_for('signin'))
    return render_template('signin.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form['username']
        new_password = request.form['password']
        re_password = request.form['re_password']

        if new_password != re_password:
            flash('Passwords do not match!')
            return redirect(url_for('forgot_password'))

        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM employee WHERE username = ?', (username,))
        user = cursor.fetchone()

        if user:
            cursor.execute('UPDATE employee SET password = ? WHERE username = ?', (new_password, username))
            conn.commit()
            flash('Password updated successfully! Please sign in.')
            return redirect(url_for('signin'))
        else:
            flash('Username not found!')
            return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('signin'))
    
    username = session['username']
    role = session.get('role', 'employee')
    
    # Fetch employee details to display in the dashboard
    conn = get_db_connection()
    if role == 'hr':
        # HR can see all employees
        employee_details = conn.execute('SELECT * FROM employee').fetchall()
    else:
        # Employees can only see their own details
        employee_details = conn.execute('SELECT * FROM employee WHERE username = ?', (username,)).fetchall()
    conn.close()
    
    return render_template('dashboard.html', username=username, employee_details=employee_details, role=role)

@app.route('/create_profile', methods=['GET', 'POST'])
def profile():
    if request.method == 'POST':
        employee_id = request.form['employee_id']
        employee_name = request.form['employee_name']
        department = request.form['department']
        date_of_joining = request.form['date_of_joining']
        email = request.form['email']
        country = request.form['country']
        mobile_no = request.form['mobile_no']
        entered_username = request.form['username']
        if entered_username != session.get('username'):
            flash('Username does not match!')
            return redirect(url_for('profile'))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO employee (employee_id, employee_name, department, date_of_joining, email, country, mobile_no, username) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (employee_id, employee_name, department, date_of_joining, email, country, mobile_no, entered_username))
        conn.commit()
        conn.close()

        flash('Employee details have been saved successfully!')
        return redirect(url_for('profile'))

    return render_template('profile.html')

@app.route('/activity')
def activity():
    return render_template('daily-task.html')

@app.route('/submit_task', methods=['POST'])
def submit_task():
    if 'username' not in session:
        flash('Please log in to submit tasks.')
        return redirect(url_for('signin'))

    task_date = request.form['task_date']
    employee_id = request.form['employee_id']
    employee_name = request.form['employee_name']
    project_site = request.form['project_site']
    in_time = request.form['in_time']
    out_time = request.form['out_time']
    task = request.form['task']
    remarks = request.form['remarks']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tasks (task_date, employee_id, employee_name, project_site, in_time, out_time, task, remarks, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (task_date, employee_id, employee_name, project_site, in_time, out_time, task, remarks, session['username']))
    conn.commit()
    conn.close()

    flash('Task submitted successfully.')
    return redirect(url_for('activity'))

@app.route('/report')
def report():
    if 'username' not in session:
        return redirect(url_for('signin'))
    
    role = session.get('role', 'employee')
    username = session['username']
    
    conn = get_db_connection()
    if role == 'hr':
        # HR can see all tasks
        tasks = conn.execute('SELECT * FROM tasks').fetchall()
    else:
        # Employees can only see their own tasks
        tasks = conn.execute('SELECT * FROM tasks WHERE username = ?', (username,)).fetchall()
    conn.close()
    
    return render_template('report.html', tasks=tasks, role=role)

@app.route('/user_details')
def user_details():
    if 'username' not in session:
        return redirect(url_for('signin'))
    
    role = session.get('role', 'employee')
    username = session['username']
    
    conn = get_db_connection()
    if role == 'hr':
        # HR can see all employee details
        employee_details = conn.execute('SELECT * FROM employee').fetchall()
    else:
        # Employees can only see their own details
        employee_details = conn.execute('SELECT * FROM employee WHERE username = ?', (username,)).fetchall()
    conn.close()
    
    return render_template('user_details.html', employee_details=employee_details, role=role)

@app.route('/users', methods=['GET', 'POST'])
def users():
    conn = get_db_connection()
    users = conn.execute('SELECT * FROM employee').fetchall()
    conn.close()
    return render_template('users.html', users=users)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('signin'))

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
