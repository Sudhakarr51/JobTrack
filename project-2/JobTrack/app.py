"""JobTrack - Flask application: routes, validation and security helpers."""
import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import models
from config import Config

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PER_PAGE = 10

EXPORT_COLUMNS = [
    "company", "job_title", "job_type", "location", "is_remote", "job_url",
    "application_date", "status", "interview_date", "follow_up_date",
    "salary_range", "notes", "created_at", "updated_at",
]


# ---------- validation helpers ----------

def validate_registration(form):
    username = form.get("username", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    errors = {}
    if not USERNAME_RE.match(username):
        errors["username"] = "Username must be 3-30 characters: letters, numbers or underscore."
    elif models.username_exists(username):
        errors["username"] = "That username is already taken."
    if len(email) > 254 or not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    elif models.get_user_by_email(email):
        errors["email"] = "An account with this email already exists."
    if not (8 <= len(password) <= 128 and any(c.isalpha() for c in password)
            and any(c.isdigit() for c in password)):
        errors["password"] = "Password must be 8-128 characters and include a letter and a number."
    elif password != form.get("confirm_password", ""):
        errors["confirm_password"] = "Passwords do not match."
    return {"username": username, "email": email, "password": password}, errors


def is_safe_url(value):
    """Only allow normal web links (http/https). Blocks javascript: and similar."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and " " not in value


def parse_date_field(value, label, key, errors):
    """Return the date text if valid, None if empty, and record an error if invalid."""
    if not value:
        return None
    try:
        if not DATE_RE.match(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        errors[key] = f"{label} must be a valid date."
        return None
    return value


def validate_application(form):
    """Clean the form values and collect error messages (server-side validation)."""
    data = {
        "company": form.get("company", "").strip(),
        "job_title": form.get("job_title", "").strip(),
        "job_type": form.get("job_type", "").strip(),
        "location": form.get("location", "").strip(),
        "is_remote": 1 if form.get("is_remote") else 0,
        "job_url": form.get("job_url", "").strip(),
        "status": form.get("status", "").strip(),
        "salary_range": form.get("salary_range", "").strip(),
        "notes": form.get("notes", "").strip(),
    }
    errors = {}
    for key, label, limit in [("company", "Company name", 100), ("job_title", "Job title", 100),
                              ("location", "Location", 100), ("salary_range", "Salary range", 60),
                              ("notes", "Notes", 2000)]:
        if len(data[key]) > limit:
            errors[key] = f"{label} must be at most {limit} characters."
    if not data["company"]:
        errors["company"] = "Company name is required."
    if not data["job_title"]:
        errors["job_title"] = "Job title is required."
    if data["job_type"] not in models.JOB_TYPES:
        errors["job_type"] = "Choose a valid job type."
    if data["status"] not in models.STATUSES:
        errors["status"] = "Choose a valid status."
    if data["job_url"] and (len(data["job_url"]) > 500 or not is_safe_url(data["job_url"])):
        errors["job_url"] = "Enter a valid link starting with http:// or https://"

    data["application_date"] = parse_date_field(
        form.get("application_date", "").strip(), "Application date", "application_date", errors)
    data["interview_date"] = parse_date_field(
        form.get("interview_date", "").strip(), "Interview date", "interview_date", errors)
    data["follow_up_date"] = parse_date_field(
        form.get("follow_up_date", "").strip(), "Follow-up date", "follow_up_date", errors)
    if (data["status"] not in ("Wishlist", "") and not data["application_date"]
            and "application_date" not in errors):
        errors["application_date"] = "Application date is required once you have applied."
    return data, errors


def csv_safe(value):
    """Stop spreadsheet formula injection: text starting with = + - @ gets a ' prefix."""
    if value is None:
        return ""
    value = str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def build_reminder(stats):
    """Returns (bootstrap colour, message) for the dashboard reminder banner."""
    overdue = len(stats["overdue_followups"])
    if overdue:
        plural = "s" if overdue != 1 else ""
        return "danger", f"You have {overdue} overdue follow-up{plural}. Send a follow-up message soon."
    if stats["upcoming_interviews"]:
        nxt = stats["upcoming_interviews"][0]
        return "info", f"Next interview: {nxt['company']} on {nxt['interview_date']}. Time to prepare!"
    if stats["upcoming_followups"]:
        nxt = stats["upcoming_followups"][0]
        return "info", f"Next follow-up: {nxt['company']} on {nxt['follow_up_date']}."
    return None, None


# ---------- application factory ----------

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    app.teardown_appcontext(models.close_db)
    with app.app_context():
        models.init_db()

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables: flask --app app init-db"""
        models.init_db()
        print("Database initialised.")

    # ----- CSRF protection (a secret token stored in the session must come back in every POST form) -----
    def get_csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        return session["csrf_token"]

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "").encode()
            expected = session.get("csrf_token", "").encode()
            if not expected or not hmac.compare_digest(sent, expected):
                abort(400)

    @app.before_request
    def load_logged_in_user():
        g.user = None
        if request.endpoint == "static":
            return
        user_id = session.get("user_id")
        if user_id:
            g.user = models.get_user_by_id(user_id)
            if g.user is None:  # account no longer exists
                session.clear()

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if g.get("user"):  # private pages should not be cached by the browser
            response.headers["Cache-Control"] = "no-store"
        return response

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    @app.context_processor
    def template_helpers():
        def page_url(page):
            """Link to another page of results while keeping the current filters."""
            args = request.args.to_dict()
            args["page"] = page
            return url_for("applications", **args)
        return {"csrf_token": get_csrf_token, "page_url": page_url,
                "STATUSES": models.STATUSES, "JOB_TYPES": models.JOB_TYPES,
                "SORT_OPTIONS": models.SORT_OPTIONS}

    @app.template_filter("pretty_date")
    def pretty_date(value):
        return date.fromisoformat(value).strftime("%d %b %Y") if value else "-"

    @app.template_filter("days_away")
    def days_away(value):
        days = (date.fromisoformat(value) - date.today()).days
        if days == 0:
            return "today"
        if days == 1:
            return "tomorrow"
        return f"in {days} days" if days > 0 else f"{-days} day(s) overdue"

    # ----- errors -----
    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, "The request was invalid or your form expired. "
                               "Go back, refresh the page and try again.")

    @app.errorhandler(404)
    def not_found(error):
        return error_page(404, "We could not find that page.")

    @app.errorhandler(405)
    def method_not_allowed(error):
        return error_page(405, "That action is not allowed here.")

    @app.errorhandler(413)
    def too_large(error):
        return error_page(413, "The submitted data was too large.")

    @app.errorhandler(500)
    def server_error(error):
        return error_page(500, "Something went wrong on our side. Please try again.")

    # ----- authentication -----
    @app.route("/")
    def index():
        return redirect(url_for("dashboard" if g.user else "login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("dashboard"))
        form, errors = {}, {}
        if request.method == "POST":
            form, errors = validate_registration(request.form)
            if not errors:
                try:
                    models.create_user(form["username"], form["email"],
                                       generate_password_hash(form["password"]))
                except sqlite3.IntegrityError:  # two people registered at the same moment
                    errors["email"] = "That username or email is already registered."
                else:
                    flash("Account created. Please log in.", "success")
                    return redirect(url_for("login"))
        return render_template("register.html", form=form, errors=errors)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        email = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = models.get_user_by_email(email)
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()  # start a fresh session after login
                session["user_id"] = user["id"]
                session.permanent = True
                flash(f"Welcome back, {user['username']}!", "success")
                return redirect(url_for("dashboard"))
            flash("Invalid email or password.", "danger")  # same message for both cases
        return render_template("login.html", email=email)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    # ----- dashboard -----
    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = models.dashboard_stats(g.user["id"], date.today())
        counts = stats["counts"]
        cards = [("Total", stats["total"], "total"), ("Applied", counts["Applied"], "applied"),
                 ("Assessment", counts["Assessment"], "assessment"),
                 ("Interview", counts["Interview"], "interview"),
                 ("Offer", counts["Offer"], "offer"), ("Rejected", counts["Rejected"], "rejected")]
        chart_data = {"labels": list(counts.keys()), "values": list(counts.values())}
        level, message = build_reminder(stats)
        return render_template("dashboard.html", stats=stats, cards=cards, chart_data=chart_data,
                               reminder_level=level, reminder=message,
                               today=date.today().isoformat())

    # ----- applications -----
    @app.route("/applications")
    @login_required
    def applications():
        status = request.args.get("status", "")
        job_type = request.args.get("job_type", "")
        filters = {
            "q": request.args.get("q", "").strip()[:100],
            "status": status if status in models.STATUSES else "",
            "job_type": job_type if job_type in models.JOB_TYPES else "",
            "location": request.args.get("location", "").strip()[:100],
            "remote_only": request.args.get("remote") == "1",
        }
        sort = request.args.get("sort", "date_desc")
        if sort not in models.SORT_OPTIONS:
            sort = "date_desc"
        page = request.args.get("page", 1, type=int)
        rows, total, page = models.search_applications(
            g.user["id"], sort=sort, page=page, per_page=PER_PAGE, **filters)
        pages = max(1, -(-total // PER_PAGE))
        has_filters = any(filters.values())
        return render_template("applications.html", rows=rows, total=total, page=page,
                               pages=pages, filters=filters, sort=sort, has_filters=has_filters,
                               today=date.today().isoformat())

    @app.route("/applications/new", methods=["GET", "POST"])
    @login_required
    def application_new():
        form, errors = {"status": "Applied", "job_type": "Full-time",
                        "application_date": date.today().isoformat()}, {}
        if request.method == "POST":
            data, errors = validate_application(request.form)
            form = request.form
            if not errors:
                app_id = models.create_application(g.user["id"], data)
                flash("Application added.", "success")
                return redirect(url_for("application_detail", app_id=app_id))
        return render_template("application_form.html", form=form, errors=errors, editing=False)

    def get_own_application_or_404(app_id):
        application = models.get_application(g.user["id"], app_id)
        if application is None:  # also used when it belongs to someone else
            abort(404)
        return application

    @app.route("/applications/<int:app_id>")
    @login_required
    def application_detail(app_id):
        return render_template("application_detail.html", app=get_own_application_or_404(app_id))

    @app.route("/applications/<int:app_id>/edit", methods=["GET", "POST"])
    @login_required
    def application_edit(app_id):
        application = get_own_application_or_404(app_id)
        form, errors = dict(application), {}
        if request.method == "POST":
            data, errors = validate_application(request.form)
            form = request.form
            if not errors:
                models.update_application(g.user["id"], app_id, data)
                flash("Application updated.", "success")
                return redirect(url_for("application_detail", app_id=app_id))
        return render_template("application_form.html", form=form, errors=errors,
                               editing=True, app=application)

    @app.route("/applications/<int:app_id>/delete", methods=["POST"])
    @login_required
    def application_delete(app_id):
        get_own_application_or_404(app_id)
        models.delete_application(g.user["id"], app_id)
        flash("Application deleted.", "info")
        return redirect(url_for("applications"))

    @app.route("/applications/export.csv")
    @login_required
    def export_csv():
        buffer = io.StringIO()
        writer = csv.writer(buffer)  # the csv module handles commas, quotes and newlines
        writer.writerow(EXPORT_COLUMNS)
        for row in models.all_applications(g.user["id"]):
            writer.writerow([csv_safe(row[column]) for column in EXPORT_COLUMNS])
        # The BOM at the start lets Excel read symbols such as the rupee sign correctly.
        return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="jobtrack_applications.csv"'})

    return app


if __name__ == "__main__":
    # Debug mode is only turned on if you set FLASK_DEBUG=1 in your local .env file.
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
