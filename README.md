# HR Management System

A modern web-based Human Resource Management System built with Flask and Bootstrap.

## Features

- User Authentication (Sign up/Sign in)
- Role-based Access Control (HR/Employee)
- Dashboard with Statistics
- Task Management
  - Daily task submission with time tracking
  - Task history and reporting
  - Project site tracking
  - Time validation (in/out time)
- User Profile Management
- Department-wise Employee Management
- Modern and Responsive UI
- Form Validation (Client & Server-side)
- Flash Messages for User Feedback
- Dark/Light Mode Toggle

## Tech Stack

- Backend: Python Flask
- Frontend: HTML, CSS, JavaScript, Bootstrap 5
- Database: SQLite
- Additional Libraries: DataTables, Chart.js, Font Awesome

## Database Schema

### Employee Table
- id (PRIMARY KEY)
- employee_name
- username (UNIQUE)
- password
- department
- gender
- date_of_joining
- email (UNIQUE)
- country
- mobile_no
- role (DEFAULT 'employee')

### Tasks Table
- id (PRIMARY KEY)
- task_date
- employee_id
- employee_name
- project_site
- in_time
- out_time
- task
- remarks
- username (FOREIGN KEY)
- created_at

## Setup Instructions

1. Clone the repository:
```bash
git clone https://github.com/upendra657/HR-Management-System.git
cd HR-Management-System
```

2. Create and activate virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Initialize the database:
```bash
python init_db.py
```

5. Run the application:
```bash
python app.py
```

6. Access the application at `http://localhost:5000`

## Default Admin Credentials

- Username: admin
- Password: admin123

## Project Structure

```
HRMS/
├── static/
│   ├── css/
│   ├── js/
│   └── images/
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── daily-task.html
│   ├── signin.html
│   ├── signup.html
│   └── ...
├── app.py
├── init_db.py
├── requirements.txt
└── README.md
```

## Recent Updates

### Task Management Improvements
- Added daily task submission form with pre-filled employee information
- Implemented time validation for in/out time
- Added project site tracking
- Enhanced form validation (both client and server-side)
- Added flash messages for better user feedback
- Improved UI with Bootstrap 5 components

### Security Enhancements
- Added session management
- Implemented role-based access control
- Added input validation
- Protected routes with authentication checks

### UI/UX Improvements
- Added floating labels for form inputs
- Implemented responsive design
- Added dark/light mode toggle
- Enhanced form validation feedback
- Improved navigation and layout

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 