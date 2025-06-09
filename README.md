# HR Management System

A modern web-based Human Resource Management System built with Flask and Bootstrap.

## Features

- User Authentication (Sign up/Sign in)
- Role-based Access Control (HR/Employee)
- Dashboard with Statistics
- Task Management
- User Profile Management
- Department-wise Employee Management
- Modern and Responsive UI

## Tech Stack

- Backend: Python Flask
- Frontend: HTML, CSS, JavaScript, Bootstrap 5
- Database: SQLite
- Additional Libraries: DataTables, Chart.js

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
│   ├── signin.html
│   ├── signup.html
│   └── ...
├── app.py
├── init_db.py
├── requirements.txt
└── README.md
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 