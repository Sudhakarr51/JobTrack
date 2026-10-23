# JobTrack - Job Application Tracker

A full-stack web app that helps students and job seekers track off-campus job applications, interviews and follow-ups in one place. Built with Flask and SQLite as a beginner-friendly portfolio project.

## Problem Statement
During placement season, students apply to dozens of companies through different portals. Details end up scattered across emails, notes and spreadsheets, so interview dates and follow-ups get missed. JobTrack keeps every application, status and date in one private dashboard.

## Features
- Registration, login and logout with hashed passwords and session-based authentication
- CSRF protection on every form, server-side validation, and per-user data isolation
- Dashboard with summary cards (Total, Applied, Assessment, Interview, Offer, Rejected), a status chart, upcoming interviews, upcoming and overdue follow-ups, recent applications, and empty states
- Add, view, edit and delete (with confirmation) applications with 12 fields
- Search by company or title; filter by status, job type, location and remote; sort by date or company; reset button; filters kept while paging
- Interview analytics: total interviews, offers, and interview conversion rate (see definition below)
- CSV export of your own applications (CSV-injection safe)
- Responsive Bootstrap 5 layout with a light blue theme

**Reminders are shown inside the dashboard only.** No email or mobile notifications are sent.

**Conversion rate** = interviews / submitted applications x 100. *Submitted* = all applications except Wishlist. *Interviews* = submitted applications that have an interview date, or are currently in Interview or Offer status. If nothing has been submitted, the rate is 0%.

## Technology Stack
Python 3, Flask, SQLite (built-in `sqlite3` module), Werkzeug (password hashing), python-dotenv, Jinja2 templates, HTML5, CSS3, Bootstrap 5.3 (CDN), Chart.js 4 (CDN), JavaScript (only for chart and delete confirmation), unittest for tests.

## Architecture Overview
```
Browser  ->  Flask routes (app.py)  ->  Database functions (models.py)  ->  SQLite file
                 |                                                  
                 +-> Jinja2 templates (templates/) + static CSS/JS
```
- `app.py` - routes, validation, CSRF, security headers, error pages
- `models.py` - schema and every SQL query (all parameterized with `?`)
- `config.py` - settings read from environment variables

## Database Design
**users**: `id` (PK), `username` (unique), `email` (unique), `password_hash`, `created_at`

**job_applications**: `id` (PK), `user_id` (FK -> users, ON DELETE CASCADE), `company`, `job_title`, `job_type` (CHECK), `location`, `is_remote` (0/1), `job_url`, `application_date`, `status` (CHECK), `interview_date`, `follow_up_date`, `salary_range`, `notes`, `created_at`, `updated_at`

One user has many applications. Dates are stored as ISO text (`YYYY-MM-DD`) because SQLite has no date type. Indexes exist on `(user_id, status)`, `(user_id, company)`, `(user_id, application_date)` and `(user_id, follow_up_date)`. Foreign keys are enforced with `PRAGMA foreign_keys = ON` on every connection.

## Folder Structure
```
JobTrack/
├── app.py
├── config.py
├── models.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── templates/   base, _macros, login, register, dashboard, applications,
│                application_form, application_detail, error (.html)
├── static/
│   ├── css/style.css
│   └── js/dashboard.js
├── tests/
│   ├── __init__.py
│   └── test_app.py
└── instance/    (the SQLite database is created here; ignored by Git)
```

## Prerequisites
- Python 3.10 or newer (`python --version`)
- Git
- Internet connection in the browser (Bootstrap and Chart.js load from a CDN)

## Installation (Windows PowerShell)
```powershell
# 1. Go to the project folder
cd path\to\JobTrack

# 2. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1
# If PowerShell blocks the script, run this once, then activate again:
# Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your environment file
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
# Open .env and paste the printed value after SECRET_KEY=
```
Set `FLASK_DEBUG=1` in `.env` only on your own computer.

## Database Initialization
```powershell
flask --app app init-db
```
The app also creates missing tables automatically on start, so this command is safe to run any time.

## Run the Project
```powershell
python app.py
```
Open http://127.0.0.1:5000, register an account, and add your first application.

## Run the Tests
```powershell
python -m unittest discover -s tests -t . -v
```
Tests use a temporary database, so your real data is never touched.

**Verification status:** the 38 automated tests were run and passed on Python 3.12 (Flask 3.1.3, Linux). Running on Windows and viewing pages in a browser are for you to confirm using the checklist.

## Screenshots
*Add your own screenshots after running the app (save them in a `screenshots/` folder).*

| Page | Screenshot |
|------|------------|
| Login | `[PLACEHOLDER: screenshots/login.png]` |
| Dashboard | `[PLACEHOLDER: screenshots/dashboard.png]` |
| Applications list | `[PLACEHOLDER: screenshots/applications.png]` |
| Add/Edit form | `[PLACEHOLDER: screenshots/form.png]` |
| Mobile view | `[PLACEHOLDER: screenshots/mobile.png]` |

## Future Enhancements
- Email reminders for interviews and follow-ups
- Login rate limiting and password reset
- Status-change history to measure true conversion
- Document uploads (resume versions) and tagging
- Deployment to a hosting service with a production server and HTTPS

## What I Learned
*Write this in your own words before publishing. Ideas:* how sessions and password hashing work, why CSRF tokens are needed, writing parameterized SQL, designing a one-to-many schema, enforcing per-user access, and testing with Flask's test client.
