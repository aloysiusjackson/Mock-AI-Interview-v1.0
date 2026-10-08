import os
import re
import json
import secrets
import smtplib
import requests
from html import escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Load .env file from project root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from database import (
    get_db_connection, dict_cursor, init_db, seed_demo_accounts, DEMO_EMAILS,
    using_postgres,
)
from ai_engine import analyze_answer, generate_questions, analyze_resume_text, ask_resume_question
import security
from security import (
    api_error, current_user_id, hash_password, log_exception, login_user,
    logout_user, needs_rehash, rate_limit, require_auth, resolve_secret_key,
    safe_json, validate_password, verify_password,
)

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), '..', 'frontend'))

# Session/cookie hardening, security headers, body-size caps and the
# cross-site write guard all live in security.configure_app().
security.configure_app(app, resolve_secret_key(os.path.dirname(os.path.abspath(__file__))))

# CORS exists only for the separately-hosted static build (GitHub Pages → Render).
# Same-origin requests never need it, so it stays opt-in and narrowly scoped
# instead of the wildcard the previous `CORS(app)` allowed.
_cors_origins = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()]
if _cors_origins:
    CORS(app, origins=_cors_origins, supports_credentials=True)

# ===================== AUTH HELPERS =====================
# Password hashing / verification now come from security.py (PBKDF2-SHA256 with
# 600k iterations, plus transparent support for the legacy sha256 hashes).

def generate_verification_token():
    return secrets.token_urlsafe(32)


# Email verification links are single-use and time-limited. 48 hours is long
# enough for a real inbox and short enough that a leaked link stops working.
VERIFICATION_LINK_TTL_HOURS = 48


def verification_expiry_from_now():
    return (datetime.now() + timedelta(hours=VERIFICATION_LINK_TTL_HOURS)).isoformat()


def verification_link_expired(expires):
    """True only for a link whose stored timestamp is in the past.

    A row written before this column existed has no timestamp; those are left
    working so upgrading the app cannot silently invalidate a pending
    verification. A value that will not parse is treated the same way (the
    token itself is still single-use and high-entropy).
    """
    if not expires:
        return False
    try:
        return datetime.fromisoformat(str(expires)) < datetime.now()
    except (TypeError, ValueError):
        return False


# Deliberately permissive but structure-checking: rejects "a@b" and spaces.
EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$')
MAX_NAME_LENGTH = 80
MAX_FEEDBACK_LENGTH = 2000


def validate_identity(name, email):
    """Return an error message when the name/email pair is unusable."""
    if not name or not email:
        return "Name, email, and password are required"
    if len(name) > MAX_NAME_LENGTH:
        return f"Name must be at most {MAX_NAME_LENGTH} characters"
    if len(email) > 254 or not EMAIL_RE.match(email):
        return "Invalid email address"
    return None

def smtp_configured():
    """True when Gmail SMTP credentials are present in the environment."""
    return bool(os.environ.get('SMTP_EMAIL', '').strip() and os.environ.get('SMTP_PASSWORD', '').strip())


def dev_auth_links_enabled():
    """Whether verification/reset links may be returned in an API response.

    The links exist so local development works without SMTP, but on a public
    deployment returning them would let anyone reset any account's password by
    simply asking (the response *is* the token). They are therefore enabled for
    local SQLite development, disabled once DATABASE_URL points at production
    PostgreSQL, and overridable with DEV_AUTH_LINKS=1 / =0.
    """
    override = (os.environ.get('DEV_AUTH_LINKS') or '').strip().lower()
    if override in ('1', 'true', 'yes'):
        return True
    if override in ('0', 'false', 'no'):
        return False
    return not using_postgres()

def verification_link(token):
    return f"{request.host_url}api/auth/verify-email?token={token}"


def note_unsent_auth_token(kind, email, token):
    """Record that no mail was sent, without putting the token in a log.

    A verification/reset token *is* a credential: anyone who can read the
    production log could use it to verify an address or take over an account.
    So the value is only ever echoed on a local-development setup (where
    DEV_AUTH_LINKS already hands the same token back to the caller).
    """
    if dev_auth_links_enabled():
        print(f"[AUTH] Email not configured. {kind} token for {email}: {token}")
    else:
        print(f"[AUTH] Email not configured; {kind} token withheld from logs (configure SMTP_* to send mail).")

def send_verification_email(email, token, name):
    """Send verification email using SMTP. Returns True on success."""
    try:
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        sender_email = os.environ.get('SMTP_EMAIL', '')
        sender_password = os.environ.get('SMTP_PASSWORD', '')

        if not sender_email or not sender_password:
            note_unsent_auth_token("verification", email, token)
            return True  # Allow signup even without email config

        verify_url = f"{request.host_url}api/auth/verify-email?token={token}"

        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Verify your Interview.AI account'
        msg['From'] = f'Interview.AI <{sender_email}>'
        msg['To'] = email

        html = f"""
        <div style="font-family: 'Plus Jakarta Sans', sans-serif; max-width: 500px; margin: 0 auto; padding: 2rem;">
            <div style="text-align: center; margin-bottom: 2rem;">
                <h1 style="color: #6366f1; font-size: 1.8rem;">Interview.AI</h1>
            </div>
            <div style="background: #f8fafc; border-radius: 16px; padding: 2rem; text-align: center;">
                <h2 style="color: #1a1f36; margin-bottom: 1rem;">Welcome, {name}! 🎉</h2>
                <p style="color: #4a5578; margin-bottom: 1.5rem; line-height: 1.6;">Thank you for signing up. Please verify your email to start your interview preparation journey.</p>
                <a href="{verify_url}" style="display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #667eea, #7c3aed); color: white; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 1rem;">Verify My Email</a>
                <p style="color: #7a829e; font-size: 0.85rem; margin-top: 1.5rem;">Or copy this link: <br><a href="{verify_url}" style="color: #6366f1;">"{verify_url}"</a></p>
            </div>
            <p style="color: #7a829e; font-size: 0.8rem; text-align: center; margin-top: 1.5rem;">If you didn't create an account, ignore this email.</p>
        </div>
        """
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, email, msg.as_string())

        print(f"[AUTH] Verification email sent to {email}")
        return True
    except Exception as e:
        print(f"[AUTH] Failed to send email: {e}")
        return False

def send_reset_email(email, token, name):
    """Send a password-reset link. Returns True on success."""
    try:
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        sender_email = os.environ.get('SMTP_EMAIL', '')
        sender_password = os.environ.get('SMTP_PASSWORD', '')

        if not sender_email or not sender_password:
            note_unsent_auth_token("reset", email, token)
            return True

        reset_url = f"{request.host_url}reset-password.html?token={token}"

        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Reset your Interview.AI password'
        msg['From'] = f'Interview.AI <{sender_email}>'
        msg['To'] = email

        html = f"""
        <div style="font-family: 'Plus Jakarta Sans', sans-serif; max-width: 500px; margin: 0 auto; padding: 2rem;">
            <div style="text-align: center; margin-bottom: 2rem;">
                <h1 style="color: #134074; font-size: 1.8rem;">Interview.AI</h1>
            </div>
            <div style="background: #f8fafc; border-radius: 16px; padding: 2rem; text-align: center;">
                <h2 style="color: #1a1f36; margin-bottom: 1rem;">Hi {name}, reset your password</h2>
                <p style="color: #4a5578; margin-bottom: 1.5rem; line-height: 1.6;">We received a request to reset the password for this account. This link is valid for <strong>2 hours</strong>.</p>
                <a href="{reset_url}" style="display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #5B9BD5, #134074); color: white; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 1rem;">Reset My Password</a>
                <p style="color: #7a829e; font-size: 0.85rem; margin-top: 1.5rem;">Or copy this link: <br><a href="{reset_url}" style="color: #134074;">"{reset_url}"</a></p>
            </div>
            <p style="color: #7a829e; font-size: 0.8rem; text-align: center; margin-top: 1.5rem;">Didn't request this? You can safely ignore this email — your password stays unchanged.</p>
        </div>
        """
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, email, msg.as_string())

        print(f"[AUTH] Password reset email sent to {email}")
        return True
    except Exception as e:
        print(f"[AUTH] Failed to send reset email: {e}")
        return False

# Initialise the schema/seed data on startup (SQLite, no server required).
print("Initializing database...")
init_db()

# --- Improved SPA Routing ---
# Serves static files if they exist, otherwise falls back to index.html for frontend routing
@app.route('/')
def serve_landing():
    return send_from_directory(app.static_folder, 'landing.html')

@app.route('/<path:path>')
def serve_static(path):
    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)

    # Unknown URL: serve the designed 404 page with a real 404 status.
    # Falling back to index.html here silently handed the dashboard to every
    # mistyped or broken link.
    return send_from_directory(app.static_folder, '404.html'), 404


@app.route('/api/<path:path>')
def serve_unknown_api(path):
    """Unknown API endpoints answer with JSON rather than an HTML page."""
    return jsonify({"error": f"Unknown API endpoint: /api/{path}"}), 404

@app.route('/api/roles', methods=['GET'])
def get_roles():
    """Returns the list of job categories available in the question bank."""
    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT DISTINCT role FROM questions")
        roles = [row['role'] for row in cur.fetchall()]
        conn.close()
        return jsonify({"roles": roles})
    except Exception as e:
        log_exception('get_roles', e)
        return api_error("Could not load roles.", 500)

@app.route('/api/questions', methods=['GET'])
@rate_limit('questions', limit=60, window_seconds=3600)
def get_questions():
    """Fetches a set of random questions for the selected role."""
    # Accounts blocked for anti-cheat violations cannot start interviews.
    blocked_user_id = current_user_id()
    if blocked_user_id is not None:
        try:
            conn = get_db_connection()
            cur = dict_cursor(conn)
            cur.execute("SELECT interview_blocked FROM users WHERE id = ?", (blocked_user_id,))
            row = cur.fetchone()
            conn.close()
            if row and row["interview_blocked"]:
                security.log_activity(
                    blocked_user_id, 'blocked_interview_attempt',
                    "Blocked user attempted to start an interview", 'failed')
                return jsonify({
                    "error": "Your account is blocked due to repeated interview misconduct. "
                             "Please contact the administrator.",
                    "interview_blocked": True,
                }), 403
        except Exception as e:
            log_exception('questions_block_check', e)

    role = str(request.args.get('role', 'Software Engineer') or 'Software Engineer').strip()
    # Bounded so `?limit=999999` cannot drag the whole question bank out.
    limit = max(1, min(request.args.get('limit', 3, type=int) or 3, 25))

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, role, category, question_text, optimal_keywords, expected_concepts, difficulty "
            "FROM questions WHERE role = ? ORDER BY RANDOM() LIMIT ?",
            (role, limit)
        )
        rows = cur.fetchall()
        conn.close()

        if len(rows) >= limit:
            questions = [{
                "id": r["id"], "role": r["role"], "category": r["category"],
                "question_text": r["question_text"], "difficulty": r["difficulty"]
            } for r in rows]
            return jsonify({"questions": questions})

        print(f"Not enough questions for '{role}' in DB. Generating via AI...")
        try:
            ai_questions = generate_questions(role, limit)
            return jsonify({"questions": ai_questions, "ai_generated": True})
        except Exception as ai_error:
            print(f"AI generation failed: {ai_error}. Using available DB questions.")
            questions = [{
                "id": r["id"], "role": r["role"], "category": r["category"],
                "question_text": r["question_text"], "difficulty": r["difficulty"]
            } for r in rows]
            return jsonify({"questions": questions})

    except Exception as e:
        log_exception('get_questions', e)
        return api_error("Could not load questions.", 500)


@app.route('/api/anti-cheat/status', methods=['GET'])
@require_auth
def anti_cheat_status():
    """Tell the frontend whether this account may start interviews."""
    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT cheat_strikes, interview_blocked FROM users WHERE id = ?", (current_user_id(),))
        row = cur.fetchone()
        conn.close()
        if not row:
            return api_error("Not signed in.", 401)
        return jsonify({
            "cheat_strikes": row["cheat_strikes"] or 0,
            "interview_blocked": bool(row["interview_blocked"]),
            "max_strikes": 3,
        })
    except Exception as e:
        log_exception('anti_cheat_status', e)
        return api_error("Could not load anti-cheat status.", 500)


@app.route('/api/anti-cheat/report', methods=['POST'])
@require_auth
@rate_limit('anti-cheat-report', limit=60, window_seconds=60)
def anti_cheat_report():
    """Record an anti-cheat violation from a live interview session.

    The frontend counts strikes client-side for instant feedback; the server
    keeps the authoritative count. On the 3rd strike the account is flagged
    `interview_blocked` — every attempt to start a new interview fails until
    an admin clears it.
    """
    MAX_STRIKES = 3
    data = safe_json()
    if not data:
        return api_error("Missing payload", 400)

    violation_type = str(data.get('violation_type', '') or '').strip()
    description = str(data.get('description', '') or '').strip() or violation_type
    if not violation_type:
        return api_error("Missing violation_type", 400)

    allowed_types = {'tab_switch', 'window_blur', 'camera_off', 'face_missing'}
    if violation_type not in allowed_types:
        return api_error("Invalid violation_type", 400)

    user_id = current_user_id()
    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT name, cheat_strikes, interview_blocked FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return api_error("Not signed in.", 401)

        if user["interview_blocked"]:
            conn.close()
            return jsonify({
                "strikes": user["cheat_strikes"] or 0,
                "max_strikes": MAX_STRIKES,
                "blocked": True,
            })

        strikes = (user["cheat_strikes"] or 0) + 1
        blocked = 1 if strikes >= MAX_STRIKES else 0
        cur.execute(
            "UPDATE users SET cheat_strikes = ?, interview_blocked = ? WHERE id = ?",
            (strikes, blocked, user_id)
        )
        conn.commit()
        conn.close()

        security.log_activity(
            user_id,
            'anticheat_violation',
            f"{violation_type} - strike {strikes} of {MAX_STRIKES} for {user['name']}",
            'failed',
            details=description,
        )
        if blocked:
            security.log_activity(
                user_id,
                'interview_blocked',
                f"Account blocked after {MAX_STRIKES} anti-cheat violations ({user['name']})",
                'failed',
                details="Interview sessions permanently disabled pending admin review",
            )

        return jsonify({
            "strikes": strikes,
            "max_strikes": MAX_STRIKES,
            "blocked": bool(blocked),
        })
    except Exception as e:
        log_exception('anti_cheat_report', e)
        return api_error("Could not record violation.", 500)


@app.route('/api/submit-interview', methods=['POST'])
@rate_limit('submit-interview', limit=20, window_seconds=3600)
def submit_interview():
    """Submits a completed interview session and grades answers."""
    data = safe_json()
    if not data:
        return api_error("Missing payload", 400)

    role = str(data.get('role', '') or '').strip()
    user_answers = data.get('answers')

    if not role or not isinstance(user_answers, list) or not user_answers:
        return api_error("Missing role or answers", 400)
    if len(user_answers) > 50:
        return api_error("Too many answers in a single submission", 400)

    # Attribute the session to the signed-in user. Guests get an unattributed
    # row (NULL) instead of the old hardcoded fallback that wrote every guest's
    # submission into account #1.
    user_id = current_user_id()

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)

        graded_answers = []
        total_score = 0.0

        for answer in user_answers:
            question_id = answer.get('question_id')
            transcript = answer.get('transcript', '')
            question_text = answer.get('question_text', '')
            category = answer.get('category', 'Behavioral')
            optimal_keywords = answer.get('optimal_keywords', '')
            expected_concepts = answer.get('expected_concepts', '')

            if question_id:
                cur.execute(
                    "SELECT question_text, category, optimal_keywords, expected_concepts "
                    "FROM questions WHERE id = ?", (question_id,)
                )
                q = cur.fetchone()
                if q:
                    question_text = q["question_text"]
                    category = q["category"]
                    optimal_keywords = q["optimal_keywords"]
                    expected_concepts = q["expected_concepts"]

            feedback = analyze_answer(
                question_text=question_text, category=category,
                optimal_keywords=optimal_keywords, expected_concepts=expected_concepts,
                transcript=transcript
            )

            score = float(feedback.get("score", 0))
            total_score += score

            graded_answers.append({
                "question_id": question_id, "question_text": question_text,
                "category": category, "transcript": transcript,
                "score": score, "feedback": feedback
            })

        overall_score = round(total_score / len(graded_answers), 1) if graded_answers else 0.0

        if overall_score >= 85:
            summary = f"Excellent performance! You exhibited deep clarity and domain mastery in the {role} role."
        elif overall_score >= 70:
            summary = f"Strong performance for {role}. Your core concepts are solid, but eliminate filler words."
        else:
            summary = f"Decent starting point, but significant improvement is needed for {role} interviews."

        date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur.execute(
            "INSERT INTO interviews (user_id, role, date, overall_score, summary) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, role, date_str, overall_score, summary)
        )
        interview_id = cur.lastrowid

        for ga in graded_answers:
            cur.execute(
                "INSERT INTO answers (interview_id, question_id, transcript, feedback_json, score) "
                "VALUES (?, ?, ?, ?, ?)",
                (interview_id, ga["question_id"], ga["transcript"], json.dumps(ga["feedback"]), ga["score"])
            )

        conn.commit()
        conn.close()

        # Log interview submission
        security.log_activity(
            user_id,
            'interview_submitted',
            f'Interview submitted for {role} role - Score: {overall_score}%',
            'success',
            details=f'Role: {role}, Score: {overall_score}, Answers: {len(graded_answers)}'
        )

        return jsonify({
            "interview_id": interview_id, "overall_score": overall_score,
            "summary": summary, "date": date_str, "answers": graded_answers
        })

    except Exception as e:
        log_exception('submit_interview', e)
        return api_error("Could not save this session.", 500)

@app.route('/api/auto-grade', methods=['POST'])
@rate_limit('auto-grade', limit=40, window_seconds=3600)
def auto_grade():
    """Real-time auto-grading endpoint."""
    data = safe_json()
    if not data:
        return api_error("Missing payload", 400)

    transcript = data.get('transcript', '')
    question_text = data.get('question_text', '')

    if not transcript or not question_text:
        return api_error("Missing transcript or question_text", 400)

    try:
        feedback = analyze_answer(
            question_text=question_text, category=data.get('category', 'Behavioral'),
            optimal_keywords=data.get('optimal_keywords', ''),
            expected_concepts=data.get('expected_concepts', ''),
            transcript=transcript
        )

        return jsonify({
            "score": feedback.get("score", 0),
            "clarity": feedback.get("clarity", 0),
            "grammar": feedback.get("grammar", 0),
            "relevance": feedback.get("relevance", 0),
            "filler_count": feedback.get("filler_count", 0),
            "strengths": feedback.get("strengths", []),
            "weaknesses": feedback.get("weaknesses", []),
            "tips": feedback.get("tips", [])
        })

    except Exception as e:
        log_exception('auto_grade', e)
        return api_error("Could not grade that answer.", 500)

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """Assembles metrics for the dashboard home screen.

    The account is taken from the session. The old `?user_id=` parameter let
    anyone read any account's scores and practice history by changing a number.
    """
    user_id = current_user_id()

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)

        # If no user_id (guest), return fresh empty stats
        if not user_id:
            conn.close()
            return jsonify({
                "user": {"name": "Guest Account", "target_role": "Software Engineer"},
                "metrics": {
                    "total_interviews": 0, "average_score": 0.0,
                    "subscores": {"clarity": 0.0, "grammar": 0.0, "relevance": 0.0, "avg_fillers_per_answer": 0.0},
                    "categories": {"Behavioral": 0.0, "Technical": 0.0, "Situational": 0.0}
                },
                "history": [], "progression": []
            })

        cur.execute("SELECT name, target_role FROM users WHERE id = ?", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            conn.close()
            return jsonify({"error": "User not found"}), 404

        user_info = {"name": user_row["name"], "target_role": user_row["target_role"]}

        cur.execute("SELECT COUNT(*) AS cnt, AVG(overall_score) AS avg_score FROM interviews WHERE user_id = ?", (user_id,))
        stats = cur.fetchone()
        total_interviews = stats['cnt'] or 0
        avg_score = round(stats['avg_score'], 1) if stats['avg_score'] else 0.0

        cur.execute(
            "SELECT id, role, date, overall_score FROM interviews WHERE user_id = ? ORDER BY date ASC", (user_id,)
        )
        history_rows = cur.fetchall()

        progression = []
        history_list = []
        for r in history_rows:
            raw_date = r["date"]
            if isinstance(raw_date, str):
                raw_date = datetime.strptime(str(raw_date), '%Y-%m-%d %H:%M:%S')
            formatted_date = raw_date.strftime('%b %d, %Y')

            progression.append({"interview_id": r["id"], "date": formatted_date, "score": r["overall_score"]})
            history_list.append({"id": r["id"], "role": r["role"], "date": formatted_date, "overall_score": r["overall_score"]})

        history_list.reverse()

        cur.execute(
            "SELECT q.category, AVG(a.score) as avg_score FROM answers a "
            "JOIN questions q ON a.question_id = q.id JOIN interviews i ON a.interview_id = i.id "
            "WHERE i.user_id = ? GROUP BY q.category", (user_id,)
        )
        cat_rows = cur.fetchall()
        categories = {row["category"]: round(row["avg_score"], 1) for row in cat_rows}

        for cat in ["Behavioral", "Technical", "Situational"]:
            if cat not in categories:
                categories[cat] = 0.0

        cur.execute(
            "SELECT a.feedback_json FROM answers a JOIN interviews i ON a.interview_id = i.id WHERE i.user_id = ?", (user_id,)
        )
        ans_rows = cur.fetchall()

        total_fillers = total_clarity = total_grammar = total_relevance = 0.0
        feedback_count = len(ans_rows)

        for row in ans_rows:
            try:
                fb = json.loads(row["feedback_json"])
                total_fillers += fb.get("filler_count", 0)
                total_clarity += fb.get("clarity", 0)
                total_grammar += fb.get("grammar", 0)
                total_relevance += fb.get("relevance", 0)
            except (json.JSONDecodeError, TypeError):
                continue

        subscores = {
            "clarity": round(total_clarity / feedback_count, 1) if feedback_count else 0.0,
            "grammar": round(total_grammar / feedback_count, 1) if feedback_count else 0.0,
            "relevance": round(total_relevance / feedback_count, 1) if feedback_count else 0.0,
            "avg_fillers_per_answer": round(total_fillers / feedback_count, 1) if feedback_count else 0.0
        }

        conn.close()

        return jsonify({
            "user": user_info,
            "metrics": {"total_interviews": total_interviews, "average_score": avg_score, "subscores": subscores, "categories": categories},
            "progression": progression,
            "history": history_list[:5]
        })

    except Exception as e:
        log_exception('get_dashboard', e)
        return api_error("Could not load the dashboard.", 500)

@app.route('/api/interview/<int:interview_id>', methods=['GET'])
@require_auth
def get_interview_detail(interview_id):
    """Fetches details for one of the signed-in user's own interviews."""
    user_id = current_user_id()
    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)

        # Scoped by owner: someone else's id simply looks "not found".
        cur.execute(
            "SELECT id, role, date, overall_score, summary FROM interviews "
            "WHERE id = ? AND user_id = ?",
            (interview_id, user_id)
        )
        i_row = cur.fetchone()
        if not i_row:
            conn.close()
            return api_error("Interview not found", 404)

        raw_date = i_row["date"]
        if isinstance(raw_date, str):
            raw_date = datetime.strptime(str(raw_date), '%Y-%m-%d %H:%M:%S')
        formatted_date = raw_date.strftime('%B %d, %Y at %I:%M %p')

        interview_data = {
            "id": i_row["id"], "role": i_row["role"], "date": formatted_date,
            "overall_score": i_row["overall_score"], "summary": i_row["summary"], "answers": []
        }

        cur.execute(
            "SELECT a.id, a.transcript, a.score, a.feedback_json, q.question_text, q.category "
            "FROM answers a JOIN questions q ON a.question_id = q.id WHERE a.interview_id = ?", (interview_id,)
        )
        for r in cur.fetchall():
            try:
                feedback_json = json.loads(r["feedback_json"])
            except (json.JSONDecodeError, TypeError):
                feedback_json = {}

            interview_data["answers"].append({
                "id": r["id"], "question_text": r["question_text"], "category": r["category"],
                "transcript": r["transcript"], "score": r["score"], "feedback": feedback_json
            })

        conn.close()
        return jsonify(interview_data)

    except Exception as e:
        log_exception('get_interview_detail', e)
        return api_error("Could not load this session.", 500)

@app.route('/api/analyze-resume', methods=['POST'])
@rate_limit('analyze-resume', limit=10, window_seconds=3600)
def analyze_resume():
    """Analyzes uploaded resume and generates personalized interview questions."""
    if 'resume' not in request.files:
        return api_error("No resume file uploaded", 400)

    file = request.files['resume']
    if file.filename == '':
        return api_error("No file selected", 400)

    # Bounded so a caller cannot ask the model for hundreds of questions.
    question_count = max(1, min(request.form.get('question_count', 3, type=int) or 3, 10))
    resume_text = ''

    try:
        if file.filename.endswith('.pdf'):
            try:
                import PyPDF2
                import io
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(file.read()))
                for page in pdf_reader.pages:
                    resume_text += page.extract_text() or ''
            except ImportError:
                file.seek(0)
                resume_text = file.read().decode('utf-8', errors='ignore')
        elif file.filename.endswith('.docx'):
            try:
                from docx import Document
                import io
                doc = Document(io.BytesIO(file.read()))
                resume_text = '\n'.join([para.text for para in doc.paragraphs])
            except ImportError:
                file.seek(0)
                resume_text = file.read().decode('utf-8', errors='ignore')
        else:
            file.seek(0)
            resume_text = file.read().decode('utf-8', errors='ignore')
    except Exception as e:
        log_exception('analyze_resume:read', e)
        return api_error("Could not read that file. Please upload a PDF, DOCX or TXT.", 500)

    if not resume_text.strip():
        return api_error("Could not extract text from resume", 400)

    try:
        result = analyze_resume_text(resume_text, question_count)
        result['full_text'] = resume_text
        return jsonify(result)
    except Exception as e:
        log_exception('analyze_resume', e)
        return api_error("Could not analyse that resume.", 500)

@app.route('/api/ask-resume-question', methods=['POST'])
@rate_limit('ask-resume-question', limit=30, window_seconds=3600)
def ask_resume_question_endpoint():
    """Answers questions about a resume during the interview session."""
    data = safe_json()
    if not data:
        return api_error("Missing payload", 400)

    question = str(data.get('question', '') or '').strip()
    resume_text = data.get('resume_text', '')

    if not question or not resume_text:
        return api_error("Missing question or resume_text", 400)

    try:
        result = ask_resume_question(question, resume_text)
        return jsonify(result)
    except Exception as e:
        log_exception('ask_resume_question', e)
        return api_error("Could not answer that question.", 500)

# ===================== AUTH ROUTES =====================
@app.route('/api/auth/signup', methods=['POST'])
@rate_limit('signup', limit=5, window_seconds=3600, by='email')
def signup():
    """Register a new user with email and password."""
    data = safe_json()
    name = str(data.get('name', '') or '').strip()
    email = str(data.get('email', '') or '').strip().lower()
    password = data.get('password', '')

    error = validate_identity(name, email)
    if error:
        return api_error(error, 400)
    if not password:
        return api_error("Name, email, and password are required", 400)

    policy_error = validate_password(password, email=email, name=name)
    if policy_error:
        return api_error(policy_error, 400)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)

        # Check if user exists
        cur.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            conn.close()
            return jsonify({"error": "An account with this email already exists"}), 409

        # Create user
        password_hashed = hash_password(password)
        token = generate_verification_token()
        cur.execute(
            "INSERT INTO users (name, email, password_hash, verification_token, "
            "verification_token_expires, is_verified, role) "
            "VALUES (?, ?, ?, ?, ?, 0, 'user')",
            (name, email, password_hashed, token, verification_expiry_from_now())
        )
        user_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Log registration
        security.log_activity(user_id, 'registration', f'New user registered: {name}', 'success')

        # Send verification email
        send_verification_email(email, token, name)

        payload = {
            "success": True,
            "message": "Account created. Please verify your email.",
            "user": {"id": user_id, "name": name, "email": email}
        }
        # When SMTP is not configured the email cannot be delivered, so hand the
        # link straight back to the caller instead of leaving the account locked
        # — but only where that is safe (see dev_auth_links_enabled).
        if not smtp_configured():
            payload["email_sent"] = False
            if dev_auth_links_enabled():
                payload["verification_link"] = verification_link(token)
        else:
            payload["email_sent"] = True

        return jsonify(payload)
    except Exception as e:
        log_exception('signup', e)
        return api_error("Could not create your account. Please try again.", 500)

@app.route('/api/auth/resend-verification', methods=['POST'])
@rate_limit('resend-verification', limit=3, window_seconds=900, by='email')
def resend_verification():
    """Re-issue the verification token and email it again."""
    data = safe_json()
    email = str(data.get('email', '') or '').strip().lower()
    if not email:
        return api_error("Email is required", 400)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        if not user:
            conn.close()
            # Do not reveal whether the address exists.
            return jsonify({"success": True, "email_sent": True})
        if user['is_verified']:
            conn.close()
            return jsonify({"success": True, "email_sent": True, "already_verified": True})

        token = generate_verification_token()
        cur.execute(
            "UPDATE users SET verification_token = ?, verification_token_expires = ? WHERE id = ?",
            (token, verification_expiry_from_now(), user['id'])
        )
        conn.commit()
        conn.close()

        send_verification_email(email, token, user['name'])

        payload = {"success": True, "email_sent": smtp_configured()}
        if not smtp_configured() and dev_auth_links_enabled():
            payload["verification_link"] = verification_link(token)
        return jsonify(payload)
    except Exception as e:
        log_exception('resend_verification', e)
        return api_error("Could not resend the verification email.", 500)


@app.route('/api/auth/forgot-password', methods=['POST'])
@rate_limit('forgot-password', limit=3, window_seconds=900, by='email')
def forgot_password():
    """Start a password reset.

    Always answers success so the response can't be used to discover which
    addresses are registered. The reset link itself is only present when SMTP
    is not configured (local development), so a real account is never locked out.
    """
    data = safe_json()
    email = str(data.get('email', '') or '').strip().lower()

    if not email or not EMAIL_RE.match(email):
        return api_error("Enter a valid email address", 400)

    payload = {"success": True, "email_sent": smtp_configured()}

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()

        # Google-only accounts have no password to reset.
        if user and user['password_hash']:
            token = generate_verification_token()
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            cur.execute(
                "UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE id = ?",
                (token, expires, user['id'])
            )
            conn.commit()

            send_reset_email(email, token, user['name'])

            if not smtp_configured() and dev_auth_links_enabled():
                payload["reset_link"] = f"{request.host_url}reset-password.html?token={token}"

        conn.close()
        return jsonify(payload)
    except Exception as e:
        log_exception('forgot_password', e)
        return api_error("Could not start the password reset.", 500)


@app.route('/api/auth/reset-password', methods=['POST'])
@rate_limit('reset-password', limit=10, window_seconds=900)
def reset_password():
    """Exchange a valid, unexpired reset token for a new password."""
    data = safe_json()
    token = str(data.get('token', '') or '').strip()
    password = data.get('password', '')

    if not token:
        return api_error("Missing reset token", 400)

    policy_error = validate_password(password)
    if policy_error:
        return api_error(policy_error, 400)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM users WHERE reset_token = ?", (token,))
        user = cur.fetchone()

        if not user:
            conn.close()
            return jsonify({"error": "This reset link is invalid or has already been used."}), 400

        expires = user['reset_token_expires']
        if not expires or datetime.fromisoformat(expires) < datetime.now():
            conn.close()
            return jsonify({"error": "This reset link has expired. Request a new one."}), 400

        # Note: is_verified is deliberately untouched — resetting a password
        # must not bypass email verification.
        cur.execute(
            "UPDATE users SET password_hash = ?, reset_token = NULL, "
            "reset_token_expires = NULL WHERE id = ?",
            (hash_password(password), user['id'])
        )
        conn.commit()
        conn.close()

        print(f"[AUTH] Password reset for {user['email']}")
        return jsonify({"success": True, "message": "Password updated. You can log in now."})
    except Exception as e:
        log_exception('reset_password', e)
        return api_error("Could not reset the password.", 500)

@app.route('/api/config', methods=['GET'])
def public_config():
    """Expose non-secret runtime settings the frontend needs."""
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    return jsonify({
        "google_client_id": client_id,
        "google_enabled": bool(client_id),
        "smtp_enabled": smtp_configured()
    })

@app.route('/api/auth/login', methods=['POST'])
@rate_limit('login', limit=20, window_seconds=300, by='email')
def login():
    """Authenticate user with email and password."""
    data = safe_json()
    email = str(data.get('email', '') or '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return api_error("Email and password are required", 400)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()

        if not user:
            # Burn a comparable amount of KDF time so response timing does not
            # reveal whether an address is registered.
            security.dummy_verify(password)
            conn.close()
            # Log failed login attempt (email doesn't exist)
            security.log_activity(None, 'failed_login', f'Failed login attempt for unknown email: {email}', 'failed')
            return api_error("Invalid email or password", 401)

        # Google-only account (no password)
        if user['google_id'] and not user['password_hash']:
            conn.close()
            security.log_activity(user['id'], 'failed_login', 'Login attempted on Google-only account without OAuth', 'failed')
            return api_error("This account uses Google login. Please sign in with Google.", 401)

        # Check password
        if not user['password_hash'] or not verify_password(password, user['password_hash']):
            conn.close()
            security.log_activity(user['id'], 'failed_login', 'Failed login attempt - incorrect password', 'failed')
            return api_error("Invalid email or password", 401)

        # Transparently upgrade a legacy hash now that we know the password.
        if needs_rehash(user['password_hash']):
            cur.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(password), user['id']))
            conn.commit()
            print(f"[AUTH] Upgraded password hash for {email}")

        # Update last login timestamp
        cur.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user['id'],))
        conn.commit()
        conn.close()

        # Check verification
        if not user['is_verified']:
            payload = {
                "success": False,
                "needs_verification": True,
                "error": "Please verify your email first",
            }
            if not smtp_configured() and user['verification_token'] and dev_auth_links_enabled():
                payload["verification_link"] = verification_link(user['verification_token'])
            return jsonify(payload), 403

        # Establish the server-side identity. Every protected endpoint reads the
        # user id from this session, never from a request parameter.
        login_user(user['id'])

        # Log successful login
        security.log_activity(user['id'], 'login', f'User logged in successfully', 'success')

        return jsonify({
            "success": True,
            "user": {
                "id": user['id'],
                "name": user['name'],
                "email": user['email'],
                "role": user['role'] or 'user',
                "target_role": user['target_role'] or 'Software Engineer',
                "avatar_url": user['avatar_url'] or ''
            }
        })
    except Exception as e:
        log_exception('login', e)
        return api_error("Could not sign you in. Please try again.", 500)


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Clear the server-side session."""
    user_id = security.current_user_id()
    logout_user()

    # Log logout
    if user_id:
        security.log_activity(user_id, 'logout', 'User logged out', 'success')

    return jsonify({"success": True})


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Return the signed-in user, or 401 for guests.

    This is the authoritative way for the frontend to learn who it is talking
    to; localStorage is display-only and must not be trusted for identity.
    """
    user_id = current_user_id()
    if user_id is None:
        return api_error("Not signed in.", 401)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT id, name, email, role, target_role, avatar_url FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        conn.close()

        if not user:
            # Stale session pointing at a deleted account.
            logout_user()
            return api_error("Not signed in.", 401)

        return jsonify({
            "id": user['id'],
            "name": user['name'],
            "email": user['email'],
            "role": user['role'] or 'user',
            "target_role": user['target_role'] or 'Software Engineer',
            "avatar_url": user['avatar_url'] or ''
        })
    except Exception as e:
        log_exception('auth_me', e)
        return api_error("Could not load your account.", 500)

@app.route('/api/auth/google', methods=['POST'])
def google_login():
    """Exchange a Google authorization code for a verified identity.

    The browser sends back the `code` Google redirected to it. We trade it for
    tokens server-side using our client secret, then have Google confirm the
    signature, issuer and audience of the identity token.
    """
    data = safe_json()
    code = str(data.get('code', '') or '').strip()
    redirect_uri = str(data.get('redirect_uri', '') or '').strip()

    if not code:
        return api_error("Missing Google authorization code", 400)
    if not redirect_uri:
        return api_error("Missing redirect_uri", 400)

    client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        return jsonify({
            "error": "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and "
                     "GOOGLE_CLIENT_SECRET in your .env file."
        }), 503

    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
    except requests.RequestException:
        return jsonify({"error": "Could not reach Google to complete sign-in."}), 502

    if token_resp.status_code != 200:
        return jsonify({"error": "Google rejected the sign-in. Please try again."}), 401

    id_token = token_resp.json().get('id_token', '')
    if not id_token:
        return jsonify({"error": "Google did not return an identity token."}), 401

    # Validate signature, issuer and audience with Google itself.
    try:
        resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=10,
        )
    except requests.RequestException:
        return jsonify({"error": "Could not verify the Google identity."}), 502

    if resp.status_code != 200:
        return jsonify({"error": "Google rejected the identity token."}), 401

    claims = resp.json()

    # Audience: the token must have been issued for *our* client ID.
    aud = claims.get('aud', '')
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        return jsonify({"error": "This Google sign-in belongs to a different application."}), 401

    # Issuer: must come from Google.
    if not str(claims.get('iss', '')).startswith('https://accounts.google.com'):
        return jsonify({"error": "Unrecognised token issuer."}), 401

    email = str(claims.get('email', '')).strip().lower()
    if not email:
        return jsonify({"error": "Google did not return an email address."}), 401
    if str(claims.get('email_verified', '')).lower() not in ('true', '1'):
        return jsonify({"error": "Your Google email address is not verified."}), 401

    google_id = str(claims.get('sub', ''))
    name = str(claims.get('name') or email.split('@')[0])
    picture = str(claims.get('picture', ''))

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)

        # Check if user exists by email or google_id
        cur.execute("SELECT * FROM users WHERE email = ? OR google_id = ?", (email, google_id))
        user = cur.fetchone()

        if user:
            # Update google_id if not set
            if not user['google_id']:
                cur.execute("UPDATE users SET google_id = ?, avatar_url = ? WHERE id = ?",
                    (google_id, picture, user['id']))
                conn.commit()
            user_id = user['id']
            user_name = user['name']
            is_new_user = False
        else:
            # Create new Google user (auto-verified)
            cur.execute(
                "INSERT INTO users (name, email, google_id, avatar_url, is_verified) "
                "VALUES (?, ?, ?, ?, 1)",
                (name, email, google_id, picture)
            )
            user_id = cur.lastrowid
            conn.commit()
            user_name = name
            is_new_user = True

        cur.execute("SELECT target_role FROM users WHERE id = ?", (user_id,))
        role_row = cur.fetchone()
        target_role = (role_row['target_role'] if role_row else None) or 'Software Engineer'

        conn.close()

        # Google has already proven the identity, so open a session for it.
        login_user(user_id)

        return jsonify({
            "success": True,
            # Tells the front-end to finish registration (name / target role)
            # before dropping a brand-new Google account on the dashboard.
            # Existing accounts are unaffected.
            "is_new_user": is_new_user,
            "user": {
                "id": user_id,
                "name": user_name,
                "email": email,
                "target_role": target_role,
                "avatar_url": picture
            }
        })
    except Exception as e:
        log_exception('google_login', e)
        return api_error("Could not complete Google sign-in.", 500)

# --- Themed, browser-facing status page -------------------------------------
# Email links open in a browser, so they render a full page rather than JSON.
# The styling mirrors frontend/css/theme.css: #0b0b0f canvas, the orange and
# purple radial glows, a glass card and a purple->indigo gradient action.
_AUTH_PAGE_STYLE = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; padding: 24px;
    display: flex; align-items: center; justify-content: center;
    background: #0b0b0f; color: #e2e8f0; overflow: hidden;
    font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .glow { position: fixed; width: 800px; height: 800px; border-radius: 50%; pointer-events: none; z-index: 0; }
  .glow-orange { top: -260px; right: -260px;
    background: radial-gradient(circle, rgba(217,119,54,0.15) 0%, rgba(11,11,15,0) 70%); }
  .glow-purple { bottom: -320px; left: -320px;
    background: radial-gradient(circle, rgba(107,56,251,0.14) 0%, rgba(11,11,15,0) 70%); }
  .card {
    position: relative; z-index: 1; width: min(460px, 100%); padding: 44px 36px;
    text-align: center; border-radius: 32px;
    background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06);
    -webkit-backdrop-filter: blur(18px); backdrop-filter: blur(18px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.35);
  }
  .brand { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 28px; }
  .brand-tile {
    width: 40px; height: 40px; border-radius: 12px; display: flex;
    align-items: center; justify-content: center; font-size: 20px;
    background: linear-gradient(135deg, #9333ea, #4338ca);
    border: 1px solid rgba(168,85,247,0.3); box-shadow: 0 8px 24px rgba(168,85,247,0.25);
  }
  .brand-name { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; color: #fff; }
  .brand-name .accent { color: #c084fc; }
  .tile {
    width: 64px; height: 64px; margin: 0 auto 22px; border-radius: 20px;
    display: flex; align-items: center; justify-content: center; font-size: 30px; line-height: 1;
  }
  .tile.ok { background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.25); box-shadow: 0 0 24px rgba(16,185,129,0.15); }
  .tile.err { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.25); box-shadow: 0 0 24px rgba(239,68,68,0.15); }
  h1 { margin: 0 0 12px; font-size: 26px; font-weight: 700; letter-spacing: -0.02em; color: #fff; }
  p { margin: 0 0 28px; font-size: 15px; line-height: 1.6; color: #94a3b8; }
  .btn {
    display: inline-block; padding: 14px 28px; border-radius: 14px;
    font-size: 15px; font-weight: 600; color: #fff; text-decoration: none;
    background: linear-gradient(90deg, #9333ea, #4f46e5);
    border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 10px 30px rgba(147,51,234,0.25);
    transition: transform 0.2s ease, filter 0.2s ease;
  }
  .btn:hover { transform: translateY(-2px); filter: brightness(1.08); }
  @media (prefers-reduced-motion: reduce) { .btn { transition: none; } }
"""


def render_auth_page(title, heading, message, tone='ok', icon='\u2705',
                     cta_label='Go to Login', cta_href='/login.html', status=200):
    """Render a themed browser page for an email/auth link, plus its status code."""
    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>{title}</title><style>{style}</style></head><body>'
        '<div class="glow glow-orange"></div><div class="glow glow-purple"></div>'
        '<main class="card">'
        '<div class="brand"><div class="brand-tile">\u26a1</div>'
        '<span class="brand-name">Interview<span class="accent">.AI</span></span></div>'
        '<div class="tile {tone}">{icon}</div>'
        '<h1>{heading}</h1><p>{message}</p>'
        '<a class="btn" href="{href}">{label}</a>'
        '</main></body></html>'
    ).format(
        title=escape(title), style=_AUTH_PAGE_STYLE, tone='err' if tone == 'err' else 'ok',
        icon=icon, heading=escape(heading), message=escape(message),
        href=escape(cta_href), label=escape(cta_label),
    )
    return html, status


@app.route('/api/auth/verify-email', methods=['GET'])
def verify_email():
    """Verify user email via token from email link."""
    token = request.args.get('token', '')
    if not token:
        return render_auth_page(
            'Link Invalid \u2014 Interview.AI', 'Missing Verification Token',
            'This link did not include a verification token. Open the link from '
            'your signup email, or request a new one from the login page.',
            tone='err', icon='\u26a0\ufe0f', status=400,
        )

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, verification_token_expires FROM users WHERE verification_token = ?",
            (token,)
        )
        user = cur.fetchone()

        if not user or verification_link_expired(user['verification_token_expires']):
            if user:
                # Retire the stale token so the link stops being usable at all.
                cur.execute(
                    "UPDATE users SET verification_token = NULL, "
                    "verification_token_expires = NULL WHERE id = ?",
                    (user['id'],)
                )
                conn.commit()
            conn.close()
            return render_auth_page(
                'Link Invalid \u2014 Interview.AI', 'Invalid or Expired Link',
                'This verification link is invalid, expired or has already been '
                'used. Sign in and request a fresh one from your account.',
                tone='err', icon='\u26a0\ufe0f', status=400,
            )

        cur.execute(
            "UPDATE users SET is_verified = 1, verification_token = NULL, "
            "verification_token_expires = NULL WHERE id = ?",
            (user['id'],)
        )
        conn.commit()
        conn.close()

        return render_auth_page(
            'Email Verified \u2014 Interview.AI', 'Email Verified!',
            'Your account is now active. Sign in to start your interview preparation.',
            tone='ok', icon='\u2705', status=200,
        )
    except Exception as e:
        log_exception('verify_email', e)
        return api_error("Could not verify that link.", 500)

@app.route('/api/auth/user', methods=['GET'])
def get_current_user():
    """Get the signed-in user's own profile.

    Previously this returned any account for a supplied `?user_id=`, which let
    anyone enumerate names and email addresses. Identity now comes from the
    session; `user_id` is accepted only when it matches the session (older
    frontend builds send it) and is otherwise ignored.
    """
    user_id = current_user_id()
    if user_id is None:
        return api_error("Authentication required.", 401)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute("SELECT id, name, email, target_role, avatar_url FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        conn.close()

        if not user:
            logout_user()
            return api_error("Not signed in.", 401)

        return jsonify({
            "id": user['id'],
            "name": user['name'],
            "email": user['email'],
            "target_role": user['target_role'] or 'Software Engineer',
            "avatar_url": user['avatar_url'] or ''
        })
    except Exception as e:
        log_exception('get_current_user', e)
        return api_error("Could not load your account.", 500)


@app.route('/api/auth/update-profile', methods=['POST'])
@require_auth
def update_profile():
    """Update the signed-in user's own profile.

    The account id is taken from the session, so `user_id` in the body can no
    longer be used to overwrite somebody else's profile.
    """
    data = safe_json()
    user_id = current_user_id()
    name = str(data.get('name', '') or '').strip()
    target_role = str(data.get('target_role', '') or '').strip()

    if len(name) > MAX_NAME_LENGTH or len(target_role) > MAX_NAME_LENGTH:
        return api_error(f"Name and role must be at most {MAX_NAME_LENGTH} characters", 400)

    try:
        conn = get_db_connection()
        cur = dict_cursor(conn)
        cur.execute(
            "UPDATE users SET name = COALESCE(?, name), target_role = COALESCE(?, target_role) WHERE id = ?",
            (name if name else None, target_role if target_role else None, user_id)
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Profile updated"})
    except Exception as e:
        log_exception('update_profile', e)
        return api_error("Could not update your profile.", 500)


@app.route('/api/feedback', methods=['POST'])
@rate_limit('feedback', limit=20, window_seconds=3600)
def submit_feedback():
    """Store interview feedback ratings."""
    data = safe_json()
    if not data:
        return api_error("Missing payload", 400)

    def clamp_rating(value):
        try:
            return max(0, min(int(value), 5))
        except (TypeError, ValueError):
            return 0

    interview_rating = clamp_rating(data.get('interview_rating', 0))
    website_rating = clamp_rating(data.get('website_rating', 0))
    comment = str(data.get('comment', '') or '')[:MAX_FEEDBACK_LENGTH]
    # Prefer the verified session over whatever the client claims to be.
    user_id = current_user_id()
    user = str(data.get('user', '') or '')[:254] if user_id is None else f"user:{user_id}"

    try:
        conn = get_db_connection()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, user_email TEXT, interview_rating INTEGER, website_rating INTEGER, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.execute(
                'INSERT INTO feedback (user_email, interview_rating, website_rating, comment) VALUES (?, ?, ?, ?)',
                (user, interview_rating, website_rating, comment)
            )
            conn.commit()
        finally:
            conn.close()

        return jsonify({'status': 'ok', 'message': 'Feedback recorded'})
    except Exception as e:
        log_exception('submit_feedback', e)
        return jsonify({'status': 'error', 'message': 'Could not save feedback'}), 500


# ══════════════════════════════════════════════════════════════════════════
#  ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/admin/stats', methods=['GET'])
@security.require_admin
def admin_stats():
    """Dashboard statistics for the admin panel."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Total users
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()['cnt']

        # Active users (verified, role != 'suspended')
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE is_verified = 1 AND role != 'suspended'")
        active_users = cur.fetchone()['cnt']

        # New users this week
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE created_at >= datetime('now', '-7 days')")
        new_users_week = cur.fetchone()['cnt']

        # New users today
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE created_at >= datetime('now', '-1 day')")
        new_users_today = cur.fetchone()['cnt']

        # Total interviews
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews")
        total_interviews = cur.fetchone()['cnt']

        # Completed interviews (with overall_score)
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews WHERE overall_score IS NOT NULL")
        completed_interviews = cur.fetchone()['cnt']

        # Total interview attempts (all interviews)
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews")
        total_attempts = cur.fetchone()['cnt']

        # Failed login attempts (recent 24h)
        cur.execute("SELECT COUNT(*) AS cnt FROM activity_logs WHERE activity_type = 'failed_login' AND created_at >= datetime('now', '-1 day')")
        failed_logins_24h = cur.fetchone()['cnt']

        # Role distribution
        cur.execute("SELECT role, COUNT(*) AS cnt FROM users GROUP BY role")
        role_distribution = {row['role']: row['cnt'] for row in cur.fetchall()}

        # Top roles by interview count
        cur.execute("""
            SELECT i.role, COUNT(*) AS cnt, ROUND(AVG(i.overall_score), 1) AS avg_score
            FROM interviews i
            WHERE i.overall_score IS NOT NULL
            GROUP BY i.role
            ORDER BY cnt DESC
            LIMIT 5
        """)
        top_roles = [{"role": row['role'], "count": row['cnt'], "avg_score": row['avg_score']}
                     for row in cur.fetchall()]

        # Recent activity (last 20 entries)
        cur.execute("""
            SELECT al.*, u.name as user_name, u.email as user_email
            FROM activity_logs al
            LEFT JOIN users u ON al.user_id = u.id
            ORDER BY al.created_at DESC
            LIMIT 20
        """)
        recent_activity = []
        for row in cur.fetchall():
            recent_activity.append({
                'id': row['id'],
                'user_id': row['user_id'],
                'user_name': row['user_name'] or 'Unknown',
                'user_email': row['user_email'] or '',
                'activity_type': row['activity_type'],
                'description': row['description'],
                'status': row['status'],
                'ip_address': row['ip_address'],
                'created_at': row['created_at'],
                'details': row['details'],
            })

        conn.close()

        return jsonify({
            'stats': {
                'total_users': total_users,
                'active_users': active_users,
                'new_users_week': new_users_week,
                'new_users_today': new_users_today,
                'total_interviews': total_interviews,
                'completed_interviews': completed_interviews,
                'total_attempts': total_attempts,
                'failed_logins_24h': failed_logins_24h,
            },
            'role_distribution': role_distribution,
            'top_roles': top_roles,
            'recent_activity': recent_activity,
        })
    except Exception as e:
        log_exception('admin_stats', e)
        return api_error("Could not load admin statistics.", 500)


@app.route('/api/admin/users', methods=['GET'])
@security.require_admin
def admin_users():
    """List users with search, filter, and pagination."""
    try:
        # Parse query params
        search = str(request.args.get('search', '') or '').strip().lower()
        status_filter = str(request.args.get('status', '') or '').strip().lower()
        role_filter = str(request.args.get('role', '') or '').strip().lower()
        page = max(1, int(request.args.get('page', 1, type=int) or 1))
        per_page = max(1, min(int(request.args.get('per_page', 20, type=int) or 20), 100))
        offset = (page - 1) * per_page

        conn = get_db_connection()
        cur = conn.cursor()

        # Build WHERE clause
        where_clauses = []
        params = []

        if search:
            where_clauses.append("(LOWER(name) LIKE ? OR LOWER(email) LIKE ?)")
            params.extend([f'%{search}%', f'%{search}%'])

        if status_filter == 'active':
            where_clauses.append("is_verified = 1 AND role != 'suspended'")
        elif status_filter == 'inactive':
            where_clauses.append("is_verified = 0")
        elif status_filter == 'suspended':
            where_clauses.append("role = 'suspended'")

        if role_filter and role_filter != 'all':
            where_clauses.append("role = ?")
            params.append(role_filter)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Get total count
        cur.execute(f"SELECT COUNT(*) AS cnt FROM users{where_sql}", params)
        total_count = cur.fetchone()['cnt']

        # Get paginated users
        cur.execute(f"""
            SELECT id, name, email, role, is_verified, created_at, last_login,
                   cheat_strikes, interview_blocked,
                   (SELECT COUNT(*) FROM interviews WHERE user_id = users.id) as interview_count
            FROM users
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        users = []
        for row in cur.fetchall():
            users.append({
                'id': row['id'],
                'name': row['name'],
                'email': row['email'],
                'role': row['role'],
                'is_verified': bool(row['is_verified']),
                'created_at': row['created_at'],
                'last_login': row['last_login'],
                'interview_count': row['interview_count'],
                'cheat_strikes': row['cheat_strikes'] or 0,
                'interview_blocked': bool(row['interview_blocked']),
                'status': 'blocked' if row['interview_blocked'] else
                          ('active' if (row['is_verified'] and row['role'] != 'suspended') else
                          ('inactive' if not row['is_verified'] else 'suspended')),
            })

        conn.close()

        return jsonify({
            'users': users,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': (total_count + per_page - 1) // per_page,
            }
        })
    except Exception as e:
        log_exception('admin_users', e)
        return api_error("Could not load user list.", 500)


@app.route('/api/admin/users/<int:user_id>', methods=['GET'])
@security.require_admin
def admin_get_user(user_id):
    """Get a single user's details and activity."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, name, email, role, is_verified, created_at, last_login,
                   verification_token IS NOT NULL as pending_verification
            FROM users WHERE id = ?
        """, (user_id,))
        user = cur.fetchone()

        if not user:
            conn.close()
            return api_error("User not found.", 404)

        # Get user's interviews
        cur.execute("""
            SELECT id, role, date, overall_score, summary
            FROM interviews WHERE user_id = ?
            ORDER BY date DESC LIMIT 50
        """, (user_id,))
        interviews = []
        for row in cur.fetchall():
            interviews.append({
                'id': row['id'],
                'role': row['role'],
                'date': row['date'],
                'overall_score': row['overall_score'],
                'summary': row['summary'],
            })

        # Get user's activity
        cur.execute("""
            SELECT activity_type, description, status, created_at, ip_address
            FROM activity_logs
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 50
        """, (user_id,))
        activity = []
        for row in cur.fetchall():
            activity.append({
                'activity_type': row['activity_type'],
                'description': row['description'],
                'status': row['status'],
                'created_at': row['created_at'],
                'ip_address': row['ip_address'],
            })

        conn.close()

        return jsonify({
            'user': {
                'id': user['id'],
                'name': user['name'],
                'email': user['email'],
                'role': user['role'],
                'is_verified': bool(user['is_verified']),
                'created_at': user['created_at'],
                'last_login': user['last_login'],
                'pending_verification': bool(user['pending_verification']),
                'interview_count': len(interviews),
                'status': 'active' if (user['is_verified'] and user['role'] != 'suspended') else
                          ('inactive' if not user['is_verified'] else 'suspended'),
            },
            'interviews': interviews,
            'activity': activity,
        })
    except Exception as e:
        log_exception('admin_get_user', e)
        return api_error("Could not load user details.", 500)


@app.route('/api/admin/users/<int:user_id>', methods=['PATCH'])
@security.require_admin
def admin_update_user(user_id):
    """Update a user's role or verification status."""
    data = safe_json()
    if not data:
        return api_error("Missing payload", 400)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify user exists
        cur.execute("SELECT id, name, role FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return api_error("User not found.", 404)

        # Prevent admin from demoting themselves
        current_uid = security.current_user_id()
        if user_id == current_uid and 'role' in data and data['role'] != 'admin':
            conn.close()
            return api_error("You cannot remove your own admin access.", 400)

        # Build update
        updates = []
        params = []

        if 'role' in data:
            valid_roles = ['user', 'admin', 'suspended']
            if data['role'] not in valid_roles:
                conn.close()
                return api_error(f"Invalid role. Must be one of: {', '.join(valid_roles)}", 400)
            updates.append("role = ?")
            params.append(data['role'])

        if 'is_verified' in data:
            updates.append("is_verified = ?")
            params.append(1 if data['is_verified'] else 0)

        if 'unblock' in data and data['unblock']:
            updates.append("interview_blocked = 0")
            updates.append("cheat_strikes = 0")

        if updates:
            params.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

            # Log the admin action
            security.log_activity(
                current_uid,
                'user_modified',
                f"Admin modified user {user['name']} (ID: {user_id})",
                'success',
                details=f"Changes: {', '.join(updates)}"
            )

        conn.close()
        return jsonify({'success': True, 'message': 'User updated successfully'})
    except Exception as e:
        log_exception('admin_update_user', e)
        return api_error("Could not update user.", 500)


@app.route('/api/admin/activity', methods=['GET'])
@security.require_admin
def admin_activity():
    """Activity log with filtering and pagination."""
    try:
        activity_type = str(request.args.get('type', '') or '').strip().lower()
        user_id = request.args.get('user_id', type=int)
        page = max(1, int(request.args.get('page', 1, type=int) or 1))
        per_page = max(20, min(int(request.args.get('per_page', 50, type=int) or 50), 200))
        offset = (page - 1) * per_page

        conn = get_db_connection()
        cur = conn.cursor()

        where_clauses = []
        params = []

        if activity_type:
            where_clauses.append("activity_type = ?")
            params.append(activity_type)

        if user_id:
            where_clauses.append("user_id = ?")
            params.append(user_id)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cur.execute(f"SELECT COUNT(*) AS cnt FROM activity_logs{where_sql}", params)
        total_count = cur.fetchone()['cnt']

        cur.execute(f"""
            SELECT al.*, u.name as user_name, u.email as user_email
            FROM activity_logs al
            LEFT JOIN users u ON al.user_id = u.id
            {where_sql}
            ORDER BY al.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        activities = []
        for row in cur.fetchall():
            activities.append({
                'id': row['id'],
                'user_id': row['user_id'],
                'user_name': row['user_name'] or 'System' if row['user_id'] else 'System',
                'user_email': row['user_email'] or '',
                'activity_type': row['activity_type'],
                'description': row['description'],
                'status': row['status'],
                'ip_address': row['ip_address'],
                'user_agent': row['user_agent'],
                'details': row['details'],
                'created_at': row['created_at'],
            })

        conn.close()

        return jsonify({
            'activities': activities,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': (total_count + per_page - 1) // per_page,
            }
        })
    except Exception as e:
        log_exception('admin_activity', e)
        return api_error("Could not load activity log.", 500)


@app.route('/api/admin/activity/types', methods=['GET'])
@security.require_admin
def admin_activity_types():
    """Return all distinct activity types and their counts."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT activity_type, COUNT(*) AS cnt,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM activity_logs
            GROUP BY activity_type
            ORDER BY cnt DESC
        """)

        types = []
        for row in cur.fetchall():
            types.append({
                'type': row['activity_type'],
                'count': row['cnt'],
                'failed_count': row['failed_count'],
            })

        conn.close()
        return jsonify({'types': types})
    except Exception as e:
        log_exception('admin_activity_types', e)
        return api_error("Could not load activity types.", 500)


# ══════════════════════════════════════════════════════════════════════════
#  SYSTEM HEALTH & ERROR LOGGING
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/admin/system-health', methods=['GET'])
@security.require_admin
def admin_system_health():
    """Return real-time system health metrics."""
    try:
        import time
        conn = get_db_connection()
        cur = conn.cursor()

        # Server uptime (seconds since module start)
        server_start = getattr(app, '_server_start_time', time.time())
        uptime_seconds = time.time() - server_start

        # Current date for "today" calculations
        today = datetime.now().date()

        # Total users
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()['cnt']

        # Active users (logged in within last 24h)
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE last_login >= datetime('now', '-1 day')")
        active_users_24h = cur.fetchone()['cnt']

        # New users today
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE date(created_at) = ?", (str(today),))
        new_users_today = cur.fetchone()['cnt']

        # Total interviews
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews")
        total_interviews = cur.fetchone()['cnt']

        # Interviews today
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews WHERE date(date) = ?", (str(today),))
        interviews_today = cur.fetchone()['cnt']

        # Completed interviews (with score)
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews WHERE overall_score IS NOT NULL")
        completed_interviews = cur.fetchone()['cnt']

        # Failed logins today
        cur.execute("SELECT COUNT(*) AS cnt FROM activity_logs WHERE activity_type = 'failed_login' AND date(created_at) = ?", (str(today),))
        failed_logins_today = cur.fetchone()['cnt']

        # Failed logins total (all time)
        cur.execute("SELECT COUNT(*) AS cnt FROM activity_logs WHERE activity_type = 'failed_login'")
        failed_logins_total = cur.fetchone()['cnt']

        # Success vs failure ratio for logins today
        cur.execute("SELECT COUNT(*) AS cnt FROM activity_logs WHERE activity_type = 'login' AND date(created_at) = ?", (str(today),))
        logins_today = cur.fetchone()['cnt']

        # Site working percentage: based on successful vs failed operations today
        total_ops_today = logins_today + failed_logins_today
        if total_ops_today > 0:
            success_rate = round((logins_today / total_ops_today) * 100, 1)
        else:
            success_rate = 100.0  # No activity means no failures

        # Error count (failed activities in last 24h)
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM activity_logs 
            WHERE status = 'failed' AND created_at >= datetime('now', '-1 day')
        """)
        errors_24h = cur.fetchone()['cnt']

        # Total errors all time
        cur.execute("SELECT COUNT(*) AS cnt FROM activity_logs WHERE status = 'failed'")
        errors_total = cur.fetchone()['cnt']

        # Recent errors (last 50)
        cur.execute("""
            SELECT al.*, u.name as user_name, u.email as user_email
            FROM activity_logs al
            LEFT JOIN users u ON al.user_id = u.id
            WHERE al.status = 'failed'
            ORDER BY al.created_at DESC
            LIMIT 50
        """)
        recent_errors = []
        for row in cur.fetchall():
            recent_errors.append({
                'id': row['id'],
                'user_id': row['user_id'],
                'user_name': row['user_name'] or 'System',
                'user_email': row['user_email'] or '',
                'activity_type': row['activity_type'],
                'description': row['description'],
                'status': row['status'],
                'ip_address': row['ip_address'],
                'created_at': row['created_at'],
                'details': row['details'],
            })

        # Daily stats for the last 7 days (for trend chart)
        daily_stats = []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).date()
            day_str = day.strftime('%Y-%m-%d')
            cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE date(created_at) = ?", (str(day_str),))
            new_users = cur.fetchone()['cnt']
            cur.execute("SELECT COUNT(*) AS cnt FROM interviews WHERE date(date) = ?", (str(day_str),))
            interviews = cur.fetchone()['cnt']
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM activity_logs 
                WHERE activity_type = 'failed_login' AND date(created_at) = ?
            """, (str(day_str),))
            failed = cur.fetchone()['cnt']
            daily_stats.append({
                'date': day_str,
                'new_users': new_users,
                'interviews': interviews,
                'failed_logins': failed,
            })

        conn.close()

        return jsonify({
            'health': {
                'server_uptime_seconds': round(uptime_seconds, 1),
                'server_uptime_formatted': _format_uptime(uptime_seconds),
                'total_users': total_users,
                'active_users_24h': active_users_24h,
                'new_users_today': new_users_today,
                'total_interviews': total_interviews,
                'interviews_today': interviews_today,
                'completed_interviews': completed_interviews,
                'failed_logins_today': failed_logins_today,
                'failed_logins_total': failed_logins_total,
                'errors_24h': errors_24h,
                'errors_total': errors_total,
                'success_rate_today': success_rate,
            },
            'daily_stats': daily_stats,
            'recent_errors': recent_errors,
        })
    except Exception as e:
        log_exception('admin_system_health', e)
        return api_error("Could not load system health.", 500)

@app.route('/api/admin/errors', methods=['GET'])
@security.require_admin
def admin_errors():
    """Get all error/failed activity logs with pagination."""
    try:
        page = max(1, int(request.args.get('page', 1, type=int) or 1))
        per_page = max(1, min(int(request.args.get('per_page', 50, type=int) or 50), 200))
        offset = (page - 1) * per_page
        activity_type = str(request.args.get('type', '') or '').strip().lower()

        conn = get_db_connection()
        cur = conn.cursor()

        where_clauses = ["status = 'failed'"]
        params = []

        if activity_type:
            where_clauses.append("activity_type = ?")
            params.append(activity_type)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cur.execute(f"SELECT COUNT(*) AS cnt FROM activity_logs{where_sql}", params)
        total_count = cur.fetchone()['cnt']

        cur.execute(f"""
            SELECT al.*, u.name as user_name, u.email as user_email
            FROM activity_logs al
            LEFT JOIN users u ON al.user_id = u.id
            {where_sql}
            ORDER BY al.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        errors = []
        for row in cur.fetchall():
            errors.append({
                'id': row['id'],
                'user_id': row['user_id'],
                'user_name': row['user_name'] or 'System',
                'user_email': row['user_email'] or '',
                'activity_type': row['activity_type'],
                'description': row['description'],
                'status': row['status'],
                'ip_address': row['ip_address'],
                'user_agent': row['user_agent'],
                'details': row['details'],
                'created_at': row['created_at'],
            })

        conn.close()

        return jsonify({
            'errors': errors,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': (total_count + per_page - 1) // per_page,
            }
        })
    except Exception as e:
        log_exception('admin_errors', e)
        return api_error("Could not load errors.", 500)

# ══════════════════════════════════════════════════════════════════════════
#  INTERVIEW MANAGEMENT (Admin)
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/admin/interviews', methods=['GET'])
@security.require_admin
def admin_interviews():
    """List all interviews with pagination, search, and filters."""
    try:
        search = str(request.args.get('search', '') or '').strip().lower()
        status_filter = str(request.args.get('status', '') or '').strip().lower()
        page = max(1, int(request.args.get('page', 1, type=int) or 1))
        per_page = max(1, min(int(request.args.get('per_page', 20, type=int) or 20), 100))
        offset = (page - 1) * per_page

        conn = get_db_connection()
        cur = conn.cursor()

        where_clauses = []
        params = []

        if search:
            where_clauses.append("(LOWER(i.role) LIKE ? OR LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ?)")
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        if status_filter:
            if status_filter == 'completed':
                where_clauses.append("i.overall_score IS NOT NULL")
            elif status_filter == 'in_progress':
                where_clauses.append("i.overall_score IS NULL")
            elif status_filter == 'high_score':
                where_clauses.append("i.overall_score >= 80")
            elif status_filter == 'low_score':
                where_clauses.append("i.overall_score < 70 AND i.overall_score IS NOT NULL")

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Get total count
        cur.execute(f"""
            SELECT COUNT(*) AS cnt FROM interviews i
            LEFT JOIN users u ON i.user_id = u.id
            {where_sql}
        """, params)
        total_count = cur.fetchone()['cnt']

        # Get paginated interviews
        cur.execute(f"""
            SELECT 
                i.id as interview_id,
                i.role,
                i.date as created_at,
                i.overall_score,
                i.summary,
                u.id as user_id,
                u.name as user_name,
                u.email as user_email,
                (SELECT COUNT(*) FROM answers WHERE interview_id = i.id) as answer_count
            FROM interviews i
            LEFT JOIN users u ON i.user_id = u.id
            {where_sql}
            ORDER BY i.date DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        interviews = []
        for row in cur.fetchall():
            score = row['overall_score']
            if score is not None:
                status = 'completed'
                score_display = round(float(score), 1)
            else:
                status = 'in_progress'
                score_display = None

            interviews.append({
                'id': row['interview_id'],
                'role': row['role'],
                'created_at': row['created_at'],
                'overall_score': score_display,
                'status': status,
                'user': {
                    'id': row['user_id'],
                    'name': row['user_name'] or 'Guest',
                    'email': row['user_email'] or 'N/A'
                },
                'answer_count': row['answer_count'],
                'summary': row['summary'],
            })

        conn.close()

        return jsonify({
            'interviews': interviews,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': (total_count + per_page - 1) // per_page,
            }
        })
    except Exception as e:
        log_exception('admin_interviews', e)
        return api_error("Could not load interviews.", 500)


@app.route('/api/admin/interviews/<int:interview_id>', methods=['GET'])
@security.require_admin
def admin_get_interview(interview_id):
    """Get a single interview with all questions, answers, and feedback."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get interview with user info
        cur.execute("""
            SELECT 
                i.id, i.role, i.date, i.overall_score, i.summary,
                u.id as user_id, u.name as user_name, u.email as user_email,
                (SELECT COUNT(*) FROM answers WHERE interview_id = i.id) as answer_count
            FROM interviews i
            LEFT JOIN users u ON i.user_id = u.id
            WHERE i.id = ?
        """, (interview_id,))
        interview = cur.fetchone()

        if not interview:
            conn.close()
            return api_error("Interview not found.", 404)

        # Get all answers with their questions and feedback
        cur.execute("""
            SELECT 
                a.id as answer_id,
                a.transcript,
                a.score,
                a.feedback_json,
                q.id as question_id,
                q.question_text,
                q.category,
                q.difficulty,
                q.optimal_keywords,
                q.expected_concepts
            FROM answers a
            JOIN questions q ON a.question_id = q.id
            WHERE a.interview_id = ?
            ORDER BY a.id
        """, (interview_id,))

        answers = []
        for row in cur.fetchall():
            try:
                feedback = json.loads(row['feedback_json']) if row['feedback_json'] else {}
            except (json.JSONDecodeError, TypeError):
                feedback = {}

            answers.append({
                'id': row['answer_id'],
                'question_id': row['question_id'],
                'question_text': row['question_text'],
                'category': row['category'],
                'difficulty': row['difficulty'],
                'transcript': row['transcript'],
                'score': row['score'],
                'feedback': feedback,
            })

        # Calculate stats
        total_answers = len(answers)
        scored_answers = [a for a in answers if a['score'] is not None]
        avg_score = round(sum(a['score'] for a in scored_answers) / len(scored_answers), 1) if scored_answers else None

        conn.close()

        return jsonify({
            'interview': {
                'id': interview['id'],
                'role': interview['role'],
                'created_at': interview['date'],
                'overall_score': interview['overall_score'],
                'summary': interview['summary'],
                'user': {
                    'id': interview['user_id'],
                    'name': interview['user_name'] or 'Guest',
                    'email': interview['user_email'] or 'N/A',
                },
                'answer_count': interview['answer_count'],
                'total_answers': total_answers,
                'scored_answers': len(scored_answers),
                'average_score': avg_score,
            },
            'answers': answers,
        })
    except Exception as e:
        log_exception('admin_get_interview', e)
        return api_error("Could not load interview details.", 500)


@app.route('/api/admin/questions', methods=['GET'])
@security.require_admin
def admin_questions():
    """List all Q&A pairs across interviews with search and filters."""
    try:
        search = str(request.args.get('search', '') or '').strip().lower()
        category_filter = str(request.args.get('category', '') or '').strip().lower()
        difficulty_filter = str(request.args.get('difficulty', '') or '').strip().lower()
        page = max(1, int(request.args.get('page', 1, type=int) or 1))
        per_page = max(1, min(int(request.args.get('per_page', 20, type=int) or 20), 100))
        offset = (page - 1) * per_page

        conn = get_db_connection()
        cur = conn.cursor()

        where_clauses = []
        params = []

        if search:
            where_clauses.append("(LOWER(q.question_text) LIKE ? OR LOWER(a.transcript) LIKE ?)")
            params.extend([f'%{search}%', f'%{search}%'])

        if category_filter and category_filter != 'all':
            where_clauses.append("q.category = ?")
            params.append(category_filter)

        if difficulty_filter and difficulty_filter != 'all':
            where_clauses.append("q.difficulty = ?")
            params.append(difficulty_filter)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Get total count
        cur.execute(f"""
            SELECT COUNT(*) AS cnt FROM answers a
            JOIN questions q ON a.question_id = q.id
            LEFT JOIN interviews i ON a.interview_id = i.id
            LEFT JOIN users u ON i.user_id = u.id
            {where_sql}
        """, params)
        total_count = cur.fetchone()['cnt']

        # Get paginated Q&A
        cur.execute(f"""
            SELECT 
                a.id as answer_id,
                q.id as question_id,
                q.question_text,
                q.category,
                q.difficulty,
                q.role,
                a.transcript,
                a.score,
                a.feedback_json,
                i.id as interview_id,
                i.role as interview_role,
                i.date as interview_date,
                u.id as user_id,
                u.name as user_name,
                u.email as user_email
            FROM answers a
            JOIN questions q ON a.question_id = q.id
            LEFT JOIN interviews i ON a.interview_id = i.id
            LEFT JOIN users u ON i.user_id = u.id
            {where_sql}
            ORDER BY a.id DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        qa_list = []
        for row in cur.fetchall():
            try:
                feedback = json.loads(row['feedback_json']) if row['feedback_json'] else {}
            except (json.JSONDecodeError, TypeError):
                feedback = {}

            qa_list.append({
                'answer_id': row['answer_id'],
                'question_id': row['question_id'],
                'question_text': row['question_text'],
                'category': row['category'],
                'difficulty': row['difficulty'],
                'question_role': row['role'],
                'transcript': row['transcript'],
                'score': row['score'],
                'feedback': feedback,
                'interview': {
                    'id': row['interview_id'],
                    'role': row['interview_role'],
                    'date': row['interview_date'],
                } if row['interview_id'] else None,
                'user': {
                    'id': row['user_id'],
                    'name': row['user_name'] or 'Guest',
                    'email': row['user_email'] or 'N/A',
                } if row['user_id'] else None,
            })

        conn.close()

        return jsonify({
            'qa_list': qa_list,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': (total_count + per_page - 1) // per_page,
            }
        })
    except Exception as e:
        log_exception('admin_questions', e)
        return api_error("Could not load questions and answers.", 500)


# ══════════════════════════════════════════════════════════════════════════
#  RESET ENDPOINTS (Admin)
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/admin/reset-users', methods=['POST'])
@security.require_admin
def admin_reset_users():
    """Delete all non-admin user accounts and their interviews."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Count users to be deleted
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE role != 'admin'")
        users_to_delete = cur.fetchone()['cnt']

        if users_to_delete == 0:
            conn.close()
            return jsonify({
                'success': True,
                'message': 'No user accounts to delete.',
                'deleted_users': 0,
                'deleted_interviews': 0
            })

        # Count their interviews first
        cur.execute("SELECT COUNT(*) AS cnt FROM interviews WHERE user_id NOT IN (SELECT id FROM users WHERE role = 'admin')")
        interviews_to_delete = cur.fetchone()['cnt']

        # Delete answers first (foreign key)
        cur.execute("DELETE FROM answers WHERE interview_id IN (SELECT id FROM interviews WHERE user_id NOT IN (SELECT id FROM users WHERE role = 'admin'))")

        # Delete interviews
        cur.execute("DELETE FROM interviews WHERE user_id NOT IN (SELECT id FROM users WHERE role = 'admin')")

        # Delete activity logs for non-admin users
        cur.execute("DELETE FROM activity_logs WHERE user_id NOT IN (SELECT id FROM users WHERE role = 'admin')")

        # Delete non-admin users, keeping the reserved demo accounts alive so
        # the documented demo credentials keep working after a reset.
        placeholders = ",".join("?" for _ in DEMO_EMAILS)
        cur.execute(f"DELETE FROM users WHERE role != 'admin' AND email NOT IN ({placeholders})", DEMO_EMAILS)
        deleted_users = cur.rowcount

        conn.commit()
        seed_demo_accounts(cur)
        conn.commit()
        conn.close()

        print(f"[ADMIN] Reset: deleted {deleted_users} users and {interviews_to_delete} interviews")

        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_users} user accounts and {interviews_to_delete} interviews.',
            'deleted_users': deleted_users,
            'deleted_interviews': interviews_to_delete
        })
    except Exception as e:
        log_exception('admin_reset_users', e)
        return api_error("Could not reset users.", 500)


@app.route('/api/admin/reset-all', methods=['POST'])
@security.require_admin
def admin_reset_all():
    """Delete ALL data including admin accounts. Use with extreme caution."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Count everything before deletion
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM interviews")
        total_interviews = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM answers")
        total_answers = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM activity_logs")
        total_logs = cur.fetchone()['cnt']

        # Delete in order of foreign key dependencies
        cur.execute("DELETE FROM answers")
        cur.execute("DELETE FROM interviews")
        cur.execute("DELETE FROM activity_logs")
        cur.execute("DELETE FROM feedback")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM questions")  # Also reset questions to start fresh

        conn.commit()
        # Bring back the demo accounts (and seed questions) so the app is
        # immediately usable again — otherwise the reset bricks all logins.
        cur.execute('SELECT COUNT(*) AS cnt FROM questions')
        if cur.fetchone()['cnt'] == 0:
            from database import seed_questions
            seed_questions(cur)
        seed_demo_accounts(cur)
        conn.commit()

        # When demo logins are not recreated (production) a full reset can leave
        # no admin behind at all. Report that instead of silently bricking the
        # only way back in.
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin'")
        admins_left = cur.fetchone()['cnt']
        conn.close()

        print(f"[ADMIN] Full reset: wiped {total_users} users, {total_interviews} interviews, {total_answers} answers, {total_logs} activity logs")

        payload = {
            'success': True,
            'message': 'All data has been permanently deleted.',
            'deleted': {
                'users': total_users,
                'interviews': total_interviews,
                'answers': total_answers,
                'activity_logs': total_logs
            }
        }
        if not admins_left:
            payload['warning'] = (
                'No admin accounts remain. Recreate one with: '
                'python backend/tests/create_admin.py you@example.com "Your Name"'
            )
        return jsonify(payload)
    except Exception as e:
        log_exception('admin_reset_all', e)
        return api_error("Could not reset all data.", 500)


def _format_uptime(seconds):
    """Format uptime seconds into a human-readable string."""
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)


def resolve_port(default=5000):
    """Choose the port to serve on.

    `PORT=0` tells the OS to pick any free port. Some shells and launchers
    export PORT=0 to mean "unset", and because load_dotenv() never overwrites
    an existing environment variable, that silently beat the PORT=5000 in
    .env -- the server then bound a random ephemeral port and vanished from
    the documented http://localhost:5000 URL. Treat anything missing,
    non-numeric or non-positive as "not configured".
    """
    try:
        port = int(str(os.environ.get('PORT', '') or '').strip() or default)
    except (TypeError, ValueError):
        return default
    return port if port > 0 else default


if __name__ == '__main__':
    # Never ship the Werkzeug debugger: it exposes an interactive console that
    # allows remote code execution. Opt in explicitly for local debugging.
    import time
    app._server_start_time = time.time()
    debug_mode = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=resolve_port(), debug=debug_mode)
