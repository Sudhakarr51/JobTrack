"""Automated tests for JobTrack. Run with:  python -m unittest discover -s tests -t . -v

Each test gets a brand-new temporary database, so tests never touch real data.
"""
import csv
import io
import os
import re
import tempfile
import unittest
from datetime import date, timedelta

from app import create_app
import models

PASSWORD = "Passw0rd123"


def days_from_now(n):
    return (date.today() + timedelta(days=n)).isoformat()


class JobTrackTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret",
                               "DATABASE": os.path.join(self.tmp.name, "test.db")})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # ----- helpers -----
    def post(self, url, data=None, client=None, **kwargs):
        """POST with a valid CSRF token (set directly in the test session)."""
        client = client or self.client
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        return client.post(url, data={**(data or {}), "csrf_token": "test-token"}, **kwargs)

    def register(self, username="alice", email="alice@example.com", password=PASSWORD, client=None):
        return self.post("/register", {"username": username, "email": email, "password": password,
                                       "confirm_password": password}, client=client)

    def login(self, email="alice@example.com", password=PASSWORD, client=None):
        return self.post("/login", {"email": email, "password": password}, client=client)

    def create_user_and_login(self, username="alice", client=None):
        email = f"{username}@example.com"
        self.register(username, email, client=client)
        self.login(email, client=client)

    def add_application(self, client=None, **overrides):
        data = {"company": "Acme", "job_title": "Developer", "job_type": "Full-time", "location": "Chennai",
                "job_url": "https://example.com/job", "status": "Applied",
                "application_date": days_from_now(0), "notes": "Looks good"}
        data.update(overrides)
        return self.post("/applications/new", data, client=client)

    def application_id(self, company="Acme"):
        with self.app.app_context():
            row = models.get_db().execute(
                "SELECT id FROM job_applications WHERE company = ?", (company,)).fetchone()
            return row["id"]


class AuthTests(JobTrackTestCase):
    def test_registration_success_hashes_password(self):
        response = self.register()
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user = models.get_user_by_email("alice@example.com")
            self.assertIsNotNone(user)
            self.assertNotEqual(user["password_hash"], PASSWORD)
            self.assertNotIn(PASSWORD, user["password_hash"])

    def test_registration_validation(self):
        self.assertIn(b"valid email", self.register(email="not-an-email").data)
        self.assertIn(b"letter and a number", self.register(username="bob", email="b@example.com", password="short").data)
        self.assertIn(b"Username must be", self.register(username="a b", email="c@example.com").data)
        response = self.post("/register", {"username": "dave", "email": "d@example.com",
                                           "password": PASSWORD, "confirm_password": "Different1"})
        self.assertIn(b"do not match", response.data)

    def test_duplicate_email_rejected(self):
        self.register()
        self.assertIn(b"already exists", self.register(username="other").data)

    def test_login_and_logout(self):
        self.register()
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.post("/logout")
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_login_wrong_password(self):
        self.register()
        response = self.login(password="WrongPass1")
        self.assertIn(b"Invalid email or password", response.data)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_post_without_csrf_token_is_rejected(self):
        self.client.get("/login")
        response = self.client.post("/login", data={"email": "a@example.com", "password": "x"})
        self.assertEqual(response.status_code, 400)

    def test_csrf_token_from_real_page_works(self):
        page = self.client.get("/register").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/register", data={
            "username": "eve", "email": "eve@example.com", "password": PASSWORD,
            "confirm_password": PASSWORD, "csrf_token": token})
        self.assertEqual(response.status_code, 302)


class AccessTests(JobTrackTestCase):
    def test_private_pages_redirect_to_login(self):
        for url in ["/dashboard", "/applications", "/applications/new", "/applications/1",
                    "/applications/1/edit", "/applications/export.csv"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/login", response.headers["Location"], url)

    def test_private_posts_blocked_when_logged_out(self):
        response = self.post("/applications/1/delete")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_foreign_keys_enabled(self):
        with self.app.app_context():
            self.assertEqual(models.get_db().execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_unknown_page_gives_404(self):
        self.assertEqual(self.client.get("/nope").status_code, 404)


class ApplicationCrudTests(JobTrackTestCase):
    def setUp(self):
        super().setUp()
        self.create_user_and_login()

    def test_add_application(self):
        response = self.add_application()
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/applications").get_data(as_text=True)
        self.assertIn("Acme", page)
        detail = self.client.get(f"/applications/{self.application_id()}").get_data(as_text=True)
        self.assertIn("Developer", detail)
        self.assertIn("https://example.com/job", detail)

    def test_add_validation_errors(self):
        response = self.add_application(company="", job_url="javascript:alert(1)", status="Hacked")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Company name is required", page)
        self.assertIn("valid link", page)
        self.assertIn("valid status", page)
        self.assertEqual(self.client.get("/applications").get_data(as_text=True).count("Acme"), 0)

    def test_bad_dates_rejected(self):
        page = self.add_application(interview_date="2026-13-45").get_data(as_text=True)
        self.assertIn("Interview date must be a valid date", page)
        page = self.add_application(application_date="").get_data(as_text=True)
        self.assertIn("Application date is required", page)

    def test_wishlist_does_not_need_application_date(self):
        response = self.add_application(status="Wishlist", application_date="")
        self.assertEqual(response.status_code, 302)

    def test_html_in_input_is_escaped(self):
        self.add_application(company="<script>alert(1)</script>")
        page = self.client.get("/applications").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_edit_application(self):
        self.add_application()
        app_id = self.application_id()
        self.assertEqual(self.client.get(f"/applications/{app_id}/edit").status_code, 200)
        response = self.post(f"/applications/{app_id}/edit", {
            "company": "Acme Corp", "job_title": "Senior Developer", "job_type": "Full-time",
            "status": "Interview", "application_date": days_from_now(0),
            "interview_date": days_from_now(3)})
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(f"/applications/{app_id}").get_data(as_text=True)
        self.assertIn("Acme Corp", detail)
        self.assertIn("Senior Developer", detail)
        self.assertIn("Interview", detail)

    def test_delete_application(self):
        self.add_application()
        app_id = self.application_id()
        response = self.post(f"/applications/{app_id}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(f"/applications/{app_id}").status_code, 404)

    def test_delete_requires_post(self):
        self.add_application()
        self.assertEqual(self.client.get(f"/applications/{self.application_id()}/delete").status_code, 405)


class SearchFilterTests(JobTrackTestCase):
    def setUp(self):
        super().setUp()
        self.create_user_and_login()
        self.add_application(company="Google", job_title="Backend Engineer", status="Interview",
                             location="Bangalore", application_date="2026-01-10")
        self.add_application(company="Zoho", job_title="Python Developer", status="Applied",
                             job_type="Internship", location="Chennai", is_remote="1",
                             application_date="2026-03-05")
        self.add_application(company="Amazon", job_title="SDE", status="Rejected",
                             location="Hyderabad", application_date="2026-02-01")

    def names(self, query):
        page = self.client.get("/applications" + query).get_data(as_text=True)
        return [c for c in ["Google", "Zoho", "Amazon"] if f">{c}</a>" in page]

    def test_search_by_company_and_title(self):
        self.assertEqual(self.names("?q=goo"), ["Google"])
        self.assertEqual(self.names("?q=python"), ["Zoho"])

    def test_filter_by_status(self):
        self.assertEqual(self.names("?status=Rejected"), ["Amazon"])

    def test_filter_by_type_location_and_remote(self):
        self.assertEqual(self.names("?job_type=Internship"), ["Zoho"])
        self.assertEqual(self.names("?location=hyder"), ["Amazon"])
        self.assertEqual(self.names("?remote=1"), ["Zoho"])

    def test_search_treats_percent_literally(self):
        self.assertEqual(self.names("?q=%25"), [])

    def test_sorting(self):
        def order(query):
            page = self.client.get("/applications" + query).get_data(as_text=True)
            return sorted(["Google", "Zoho", "Amazon"], key=lambda c: page.index(f">{c}</a>"))
        self.assertEqual(order("?sort=company_asc"), ["Amazon", "Google", "Zoho"])
        self.assertEqual(order("?sort=company_desc"), ["Zoho", "Google", "Amazon"])
        self.assertEqual(order("?sort=date_desc"), ["Zoho", "Amazon", "Google"])
        self.assertEqual(order("?sort=date_asc"), ["Google", "Amazon", "Zoho"])

    def test_invalid_sort_and_page_do_not_crash(self):
        self.assertEqual(self.client.get("/applications?sort=DROP+TABLE&page=abc").status_code, 200)
        self.assertEqual(self.client.get("/applications?page=999").status_code, 200)

    def test_no_match_message_and_reset_link(self):
        page = self.client.get("/applications?q=nothing-here").get_data(as_text=True)
        self.assertIn("No applications match", page)
        self.assertIn("Reset filters", page)

    def test_filters_kept_in_pagination_links(self):
        for i in range(12):
            self.add_application(company=f"Extra{i}", job_title="Tester", status="Applied")
        page = self.client.get("/applications?status=Applied&sort=company_asc").get_data(as_text=True)
        self.assertIn("page=2", page)
        self.assertIn("status=Applied", page.split("page=2")[0][-200:] + page.split("page=2")[1][:200])


class IsolationTests(JobTrackTestCase):
    def setUp(self):
        super().setUp()
        self.create_user_and_login("alice")
        self.add_application(company="AliceSecretCo")
        self.alice_app_id = self.application_id("AliceSecretCo")
        self.bob = self.app.test_client()
        self.create_user_and_login("bob", client=self.bob)

    def test_other_user_cannot_view_edit_or_delete(self):
        base = f"/applications/{self.alice_app_id}"
        self.assertEqual(self.bob.get(base).status_code, 404)
        self.assertEqual(self.bob.get(base + "/edit").status_code, 404)
        self.assertEqual(self.post(base + "/edit", {"company": "Hacked", "job_title": "x",
                                                    "job_type": "Full-time", "status": "Wishlist"},
                                   client=self.bob).status_code, 404)
        self.assertEqual(self.post(base + "/delete", client=self.bob).status_code, 404)
        detail = self.client.get(base).get_data(as_text=True)
        self.assertIn("AliceSecretCo", detail)

    def test_other_user_does_not_see_data_in_list_dashboard_or_export(self):
        self.assertNotIn("AliceSecretCo", self.bob.get("/applications").get_data(as_text=True))
        self.assertNotIn("AliceSecretCo", self.bob.get("/dashboard").get_data(as_text=True))
        self.assertNotIn("AliceSecretCo", self.bob.get("/applications/export.csv").get_data(as_text=True))


class CsvExportTests(JobTrackTestCase):
    def setUp(self):
        super().setUp()
        self.create_user_and_login()

    def rows(self):
        response = self.client.get("/applications/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["Content-Type"])
        self.assertIn("attachment", response.headers["Content-Disposition"])
        return list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))

    def test_export_contains_header_and_data(self):
        self.add_application(company="Acme", job_title="Dev")
        rows = self.rows()
        self.assertEqual(rows[0][:3], ["company", "job_title", "job_type"])
        self.assertEqual(rows[1][0], "Acme")
        self.assertEqual(len(rows), 2)

    def test_export_escapes_commas_quotes_and_newlines(self):
        self.add_application(company='Big, "Bold" Co', notes="line1\nline2")
        row = self.rows()[1]
        self.assertEqual(row[0], 'Big, "Bold" Co')
        self.assertEqual(row[11], "line1\nline2")

    def test_export_blocks_formula_injection(self):
        self.add_application(company="=HYPERLINK(\"http://evil\")")
        self.assertTrue(self.rows()[1][0].startswith("'="))

    def test_empty_export_has_only_header(self):
        self.assertEqual(len(self.rows()), 1)


class DashboardTests(JobTrackTestCase):
    def setUp(self):
        super().setUp()
        self.create_user_and_login()

    def stats(self):
        with self.app.app_context():
            user = models.get_user_by_email("alice@example.com")
            return models.dashboard_stats(user["id"], date.today())

    def test_empty_dashboard_is_safe(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Add your first application", response.get_data(as_text=True))
        stats = self.stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["conversion_rate"], 0.0)  # no division by zero

    def test_statistics_come_from_database(self):
        self.add_application(company="A", status="Applied")
        self.add_application(company="B", status="Applied")
        self.add_application(company="C", status="Interview", interview_date=days_from_now(2))
        self.add_application(company="D", status="Offer")
        self.add_application(company="E", status="Rejected", interview_date=days_from_now(-5))
        self.add_application(company="F", status="Wishlist", application_date="")
        stats = self.stats()
        self.assertEqual(stats["total"], 6)
        self.assertEqual(stats["counts"]["Applied"], 2)
        self.assertEqual(stats["counts"]["Wishlist"], 1)
        self.assertEqual(stats["offers"], 1)
        self.assertEqual(stats["submitted"], 5)
        self.assertEqual(stats["interviews"], 3)         # Interview, Offer, Rejected-with-date
        self.assertEqual(stats["conversion_rate"], 60.0)  # 3 / 5
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("60.0%", page)
        self.assertIn('id="chart-data"', page)

    def test_upcoming_and_overdue_followups(self):
        self.add_application(company="Late", status="Applied", follow_up_date=days_from_now(-3))
        self.add_application(company="Soon", status="Applied", follow_up_date=days_from_now(2))
        self.add_application(company="Done", status="Rejected", follow_up_date=days_from_now(-9))
        stats = self.stats()
        self.assertEqual([r["company"] for r in stats["overdue_followups"]], ["Late"])
        self.assertEqual([r["company"] for r in stats["upcoming_followups"]], ["Soon"])
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("1 overdue follow-up", page)
        self.assertIn("Overdue", page)
        self.assertIn("No email or phone notifications", page)

    def test_upcoming_interviews_listed(self):
        self.add_application(company="Soon", status="Interview", interview_date=days_from_now(1))
        self.add_application(company="Past", status="Interview", interview_date=days_from_now(-1))
        self.assertEqual([r["company"] for r in self.stats()["upcoming_interviews"]], ["Soon"])

    def test_edit_dates_updates_dashboard(self):
        self.add_application(company="Acme")
        app_id = self.application_id()
        self.post(f"/applications/{app_id}/edit", {
            "company": "Acme", "job_title": "Developer", "job_type": "Full-time", "status": "Applied",
            "application_date": days_from_now(0), "follow_up_date": days_from_now(-1)})
        self.assertEqual(len(self.stats()["overdue_followups"]), 1)


if __name__ == "__main__":
    unittest.main()
