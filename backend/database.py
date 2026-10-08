import os
import json
import sqlite3
from datetime import datetime, timedelta

import db_backend
import pg_schema

# ---- SQLite database path (still the default engine) ----
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, 'mock_interview.db')


def using_postgres():
    """True when DATABASE_URL is set, i.e. the app runs on PostgreSQL (Supabase)."""
    return db_backend.is_postgres()


def get_db_connection():
    """Open a connection to whichever engine is configured.

    * No ``DATABASE_URL``  -> the original SQLite file (local development).
    * ``DATABASE_URL`` set -> PostgreSQL, through the compatibility wrapper in
      db_backend.py so every existing ``?``-placeholder query keeps working.
    """
    if using_postgres():
        return db_backend.connect()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def dict_cursor(conn):
    """Return a cursor whose rows are dict-like on either engine.

    SQLite gets that from ``row_factory``; PostgreSQL from the wrapper cursor in
    db_backend.py (RealDictCursor + SQLite-shaped values).
    """
    return conn.cursor()

def init_db():
    """Create tables if they don't exist and seed data."""
    if using_postgres():
        return init_postgres_db()

    conn = get_db_connection()
    cur = conn.cursor()

    # ── Create tables ──────────────────────────────────────────────
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT,
        google_id TEXT,
        avatar_url TEXT,
        target_role TEXT DEFAULT 'Software Engineer',
        is_verified INTEGER DEFAULT 0,
        verification_token TEXT,
        verification_token_expires TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # ── Lightweight migrations for databases created before a column existed ──
    existing_cols = {row['name'] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
    for col_name, col_type in (
        ("reset_token", "TEXT"), ("reset_token_expires", "TEXT"),
        ("verification_token_expires", "TEXT"),
        ("role", "TEXT DEFAULT 'user'"), ("last_login", "TIMESTAMP"),
        ("cheat_strikes", "INTEGER DEFAULT 0"),
        ("interview_blocked", "INTEGER DEFAULT 0"),
    ):
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            conn.commit()

    # ── Activity logs table ──────────────────────────────────────────────────
    cur.execute('''
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        activity_type TEXT NOT NULL,
        description TEXT,
        status TEXT DEFAULT 'success',
        ip_address TEXT,
        user_agent TEXT,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Index for faster lookups by user and by date
    cur.execute('CREATE INDEX IF NOT EXISTS idx_activity_logs_user_id ON activity_logs(user_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_activity_logs_type ON activity_logs(activity_type)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_activity_logs_created ON activity_logs(created_at DESC)')
    conn.commit()


    cur.execute('''
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        category TEXT CHECK(category IN ('Behavioral', 'Technical', 'Situational')) NOT NULL,
        question_text TEXT NOT NULL,
        optimal_keywords TEXT,
        expected_concepts TEXT,
        difficulty TEXT CHECK(difficulty IN ('Easy', 'Medium', 'Hard')) NOT NULL
    )
    ''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS interviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        role TEXT NOT NULL,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        overall_score REAL,
        summary TEXT
    )
    ''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        interview_id INTEGER REFERENCES interviews(id),
        question_id INTEGER REFERENCES questions(id),
        transcript TEXT,
        feedback_json TEXT,
        score REAL
    )
    ''')

    # Feedback table is written by /api/feedback; create it here too so
    # admin_reset_all() can always clear it without an OperationalError.
    cur.execute('''
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        interview_rating INTEGER,
        website_rating INTEGER,
        comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()

    # ── Seed questions ─────────────────────────────────────────────
    cur.execute('SELECT COUNT(*) AS cnt FROM questions')
    if cur.fetchone()['cnt'] == 0:
        seed_questions(cur)
        conn.commit()

    # ── Seed demo user & history ───────────────────────────────────
    cur.execute('SELECT COUNT(*) AS cnt FROM users')
    if cur.fetchone()['cnt'] == 0:
        seed_users_and_history(cur)
        conn.commit()

    # ── Seed demo accounts (always, self-healing) ──────────────────
    seed_demo_accounts(cur)
    conn.commit()

    cur.close()
    conn.close()
    print("Database initialized successfully!")


# ═══════════════════════════════════════════════════════════════════
#  POSTGRESQL INITIALISATION (Supabase)
# ═══════════════════════════════════════════════════════════════════

def _pg_existing_columns(cur, table):
    """Names of a table's columns, the PostgreSQL equivalent of PRAGMA table_info."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?",
        (table,),
    )
    return {row['column_name'] for row in cur.fetchall()}


def init_postgres_db():
    """Create the PostgreSQL schema and seed it when it is brand new.

    Mirrors the SQLite initialiser: the same tables, the same additive column
    migrations and the same seeding rules, so a fresh Supabase database behaves
    exactly like a fresh ``mock_interview.db``.
    """
    conn = get_db_connection()
    try:
        pg_schema.create_schema(conn)
        cur = conn.cursor()

        # Additive migrations for a database created by an older schema.
        users_cols = _pg_existing_columns(cur, 'users')
        for col_name, col_type in (
            ("reset_token", "TEXT"), ("reset_token_expires", "TEXT"),
            ("verification_token_expires", "TEXT"),
            ("role", "TEXT DEFAULT 'user'"), ("last_login", "TIMESTAMP"),
            ("cheat_strikes", "INTEGER DEFAULT 0"),
            ("interview_blocked", "INTEGER DEFAULT 0"),
        ):
            if col_name not in users_cols:
                cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
        conn.commit()

        # Seed the question bank only when it is empty.
        cur.execute('SELECT COUNT(*) AS cnt FROM questions')
        if cur.fetchone()['cnt'] == 0:
            seed_questions(cur)
            conn.commit()

        # Seed demo history only for a completely empty install.
        cur.execute('SELECT COUNT(*) AS cnt FROM users')
        if cur.fetchone()['cnt'] == 0:
            seed_users_and_history(cur)
            conn.commit()

        seed_demo_accounts(cur)
        conn.commit()
        cur.close()

        # Promote the declared relationships to fully validated (a no-op on a
        # brand-new database; on an imported one it reports what could not be
        # validated instead of failing).
        pg_schema.validate_constraints(conn)
    finally:
        conn.close()
    print(f"PostgreSQL database initialized successfully ({pg_schema.TABLES_IN_ORDER}).")


# ═══════════════════════════════════════════════════════════════════
#  DEMO ACCOUNTS
# ═══════════════════════════════════════════════════════════════════

# Reserved demo logins. They are (re)created at every startup so the app can
# always be signed into, even after a Danger Zone reset or a fresh database.
# The credentials are intentionally public — they only exist for demos.
DEMO_PASSWORD = 'Demo@1234'
DEMO_EMAILS = ('admin@demo.com', 'user@demo.com', 'demo@example.com')


def demo_accounts_enabled():
    """Whether the public demo logins should be (re)created by this process.

    The demo credentials are published in the README, so they must never exist
    on a public deployment. They stay enabled for local SQLite development and
    are disabled automatically once DATABASE_URL points at production
    PostgreSQL. Override either way with SEED_DEMO_ACCOUNTS=1 / =0.
    """
    override = (os.environ.get('SEED_DEMO_ACCOUNTS') or '').strip().lower()
    if override in ('1', 'true', 'yes'):
        return True
    if override in ('0', 'false', 'no'):
        return False
    return not using_postgres()


def seed_demo_accounts(cur):
    """Ensure the demo admin + demo user exist, verified and loginable.

    - Missing row        → created with the demo password and correct role.
    - Row without a hash → password set (covers the legacy passwordless seed).
    - Known demo emails  → password is reset back to DEMO_PASSWORD on every
      startup so the documented credentials never stop working.

    Skipped entirely (see demo_accounts_enabled) when the app runs against
    production PostgreSQL, so no publicly-known admin exists on a live site.
    """
    if not demo_accounts_enabled():
        return

    from security import hash_password, verify_password

    roster = (
        ('admin@demo.com', 'Demo Admin', 'admin'),
        ('user@demo.com', 'Demo User', 'user'),
        # Legacy seed row from seed_users_and_history(): give it a working
        # password but never touch its role or existing password.
        ('demo@example.com', 'Demo User', None),
    )

    for email, name, role in roster:
        cur.execute(
            "SELECT id, password_hash, role FROM users WHERE email = ?", (email,)
        )
        row = cur.fetchone()

        if row is None:
            if role is None:
                continue
            cur.execute(
                "INSERT INTO users (name, email, password_hash, is_verified, role, target_role) "
                "VALUES (?, ?, ?, 1, ?, 'Software Engineer')",
                (name, email, hash_password(DEMO_PASSWORD), role),
            )
            print(f"[DEMO] Created demo account {email} (role={role})")
            continue

        stored = row['password_hash']
        if role is None:
            # Legacy account: only give it a password if it has none.
            if not stored:
                cur.execute(
                    "UPDATE users SET password_hash = ?, is_verified = 1 WHERE id = ?",
                    (hash_password(DEMO_PASSWORD), row['id']),
                )
                print(f"[DEMO] Set demo password for legacy account {email}")
            continue

        needs_password = not stored or not verify_password(DEMO_PASSWORD, stored)
        needs_role = row['role'] != role
        if needs_password or needs_role:
            cur.execute(
                "UPDATE users SET password_hash = ?, is_verified = 1, role = ? WHERE id = ?",
                (hash_password(DEMO_PASSWORD), role, row['id']),
            )
            print(f"[DEMO] Repaired demo account {email} (role={role})")

    # A blocked demo account can't demonstrate anything, so clear any
    # anti-cheat strikes/blocks on startup (admins can still block live).
    cur.execute(
        "UPDATE users SET cheat_strikes = 0, interview_blocked = 0 "
        "WHERE email IN (?, ?) AND (cheat_strikes > 0 OR interview_blocked > 0)",
        ('admin@demo.com', 'user@demo.com'),
    )


# ═══════════════════════════════════════════════════════════════════
#  SEED DATA
# ═══════════════════════════════════════════════════════════════════

def seed_questions(cur):
    """Insert all seed interview questions.

    Works on both engines: the statements use ``?`` placeholders and the
    compatibility layer supplies the PostgreSQL ids back through lastrowid.
    """
    questions_data = [
        # === SOFTWARE ENGINEER ===
        ("Software Engineer", "Behavioral", "Tell me about a time you had a technical disagreement with a team member. How did you resolve it?", "compromise, discussion, collaboration, perspective, consensus", "Resolving conflict, communication, constructive debate, technical compromise", "Medium"),
        ("Software Engineer", "Technical", "Can you explain the difference between a Relational Database (SQL) and a Non-Relational Database (NoSQL)? When would you choose one over the other?", "schema, ACID, scale, horizontal, vertical, structured, key-value, document", "Database design, trade-offs, scaling properties, relational vs document storage, ACID compliance", "Medium"),
        ("Software Engineer", "Behavioral", "Describe a challenging programming bug you encountered. How did you diagnose and fix it?", "debugging, root cause, log, monitoring, isolation, fix, prevention", "Problem-solving, step-by-step troubleshooting, testing, writing reproducible test cases", "Hard"),
        ("Software Engineer", "Technical", "Explain the concept of REST APIs and how they differ from GraphQL. What are the key advantages of each?", "stateless, endpoint, query, over-fetching, under-fetching, resource, HTTP methods", "API architecture, payloads, REST verbs, flexible querying, protocol differences", "Medium"),
        ("Software Engineer", "Technical", "What is Big O notation, and why is it important in algorithm design? Explain with examples of O(1), O(n), and O(log n).", "time complexity, space complexity, scale, input size, search, binary search, array lookup", "Algorithmic efficiency, execution speed, scalability, time/space limits, sorting/search performance", "Easy"),
        # === PRODUCT MANAGER ===
        ("Product Manager", "Behavioral", "Tell me about a time when a product launch didn't go as planned. What did you learn and how did you adapt?", "post-mortem, customer feedback, metric, root cause, adaptation, launch", "Resilience, post-launch feedback loop, metrics tracking, pivot capability", "Hard"),
        ("Product Manager", "Technical", "How do you decide what features to prioritize when building a product roadmap? What frameworks do you use?", "RICE, MoSCoW, value, effort, metrics, stakeholders, trade-offs, ROI", "Roadmapping, value/effort scoring, customer needs analysis, product strategy", "Medium"),
        ("Product Manager", "Behavioral", "How do you manage conflicting requirements from different stakeholders, such as engineering, sales, and marketing?", "alignment, compromise, user value, vision, conflict resolution, communication", "Stakeholder communication, user-centered prioritization, data-driven negotiation", "Medium"),
        ("Product Manager", "Technical", "If our user acquisition drops by 15% week-over-week, how would you go about diagnosing the root cause?", "funnel analysis, conversion rate, channels, tracking, seasonality, data, AB testing", "Analytical reasoning, metrics breakdown, telemetry analysis, user flow diagnostic", "Hard"),
        # === DATA ANALYST ===
        ("Data Analyst", "Technical", "What is the difference between inner join, left join, and outer join in SQL? Give a practical example of when to use each.", "join, merge, null, matching rows, left table, right table, keys", "Data cleaning, table relationships, aggregation, relational mapping", "Easy"),
        ("Data Analyst", "Technical", "Explain the difference between correlation and causation. Can you give an example of how confusing these could hurt a business decision?", "correlation, causation, variable, confounding factor, metrics, hypothesis testing", "Data literacy, analytical bias, scientific method, statistics", "Medium"),
        ("Data Analyst", "Behavioral", "Describe a situation where you had to explain complex analysis results to a non-technical stakeholder. How did you structure your communication?", "storytelling, dashboard, visualizations, simplification, actionable insights, business impact", "Data communication, business alignment, visual charts, slide presentation", "Medium"),
        ("Data Analyst", "Technical", "What is overfitting in a predictive model, and what steps can you take to prevent it?", "overfitting, training data, validation, testing, complexity, cross-validation, regularization", "Model robustness, validation strategies, complexity control", "Hard"),
        # === SALES EXECUTIVE ===
        ("Sales Executive", "Behavioral", "Describe a time you exceeded your quarterly sales target. What specific strategies did you use?", "prospecting, pipeline, closing, negotiation, relationship, upsell, cross-sell", "Sales methodology, target achievement, strategic planning, customer relationship management", "Medium"),
        ("Sales Executive", "Situational", "How would you handle a situation where a long-time client is considering switching to a competitor?", "retention, value proposition, feedback, solution, loyalty, competitor analysis", "Customer retention, consultative selling, competitive differentiation, problem-solving", "Hard"),
        ("Sales Executive", "Behavioral", "Tell me about a time you failed to close an important deal. What did you learn from the experience?", "analysis, reflection, objection, follow-up, qualification, improvement", "Learning from failure, sales process refinement, self-awareness, resilience", "Medium"),
        # === MARKETING MANAGER ===
        ("Marketing Manager", "Technical", "What marketing KPIs do you track to measure campaign effectiveness? How do you optimize underperforming campaigns?", "ROI, CAC, LTV, conversion rate, CTR, impressions, engagement, A/B testing", "Marketing analytics, campaign optimization, KPI tracking, data-driven decisions", "Medium"),
        ("Marketing Manager", "Behavioral", "Describe a successful marketing campaign you led from concept to execution. What was the outcome?", "strategy, creative, execution, metrics, target audience, brand awareness, lead generation", "Campaign lifecycle, creative strategy, cross-functional coordination, results measurement", "Medium"),
        ("Marketing Manager", "Situational", "Your product launch campaign is underperforming after two weeks. Walk me through how you would pivot the strategy.", "data analysis, pivot, optimization, feedback, budget reallocation, messaging", "Agile marketing, data-informed pivoting, budget management, real-time optimization", "Hard"),
        # === HR MANAGER ===
        ("HR Manager", "Behavioral", "Tell me about a time you handled a sensitive employee relations issue. How did you maintain confidentiality and fairness?", "confidentiality, fairness, investigation, policy, communication, resolution", "Employee relations, conflict resolution, compliance, HR best practices, empathy", "Hard"),
        ("HR Manager", "Technical", "What strategies do you use to improve employee retention and reduce turnover in an organization?", "retention, engagement, culture, feedback, development, recognition, exit interviews", "Talent management, employee engagement, company culture, retention strategies", "Medium"),
        ("HR Manager", "Behavioral", "Describe a situation where you had to manage a difficult hiring decision. How did you ensure a fair process?", "bias mitigation, structured interviews, diverse panel, objective criteria, evaluation", "Recruitment best practices, diversity and inclusion, structured hiring, decision-making", "Medium"),
        # === FINANCIAL ANALYST ===
        ("Financial Analyst", "Technical", "Walk me through how you would build a financial model to evaluate a potential investment opportunity.", "DCF, NPV, IRR, assumptions, revenue projections, costs, valuation, sensitivity analysis", "Financial modeling, valuation methods, forecasting, risk assessment, investment analysis", "Hard"),
        ("Financial Analyst", "Technical", "What are the key financial statements every analyst should understand? How do they interconnect?", "income statement, balance sheet, cash flow, revenue, expenses, assets, liabilities", "Financial accounting, statement analysis, inter-statement relationships, GAAP principles", "Medium"),
        ("Financial Analyst", "Behavioral", "Describe a time when your financial analysis uncovered a critical insight that influenced a major business decision.", "analysis, insight, recommendation, impact, stakeholders, presentation", "Analytical discovery, business impact, executive communication, strategic influence", "Medium"),
        # === OPERATIONS MANAGER ===
        ("Operations Manager", "Behavioral", "Tell me about a time you improved an inefficient process in your organization. What was the impact?", "process improvement, efficiency, cost reduction, automation, workflow, optimization", "Process optimization, operational efficiency, change management, measurable impact", "Medium"),
        ("Operations Manager", "Situational", "A key supplier has suddenly gone bankrupt, threatening your production timeline. How do you respond?", "contingency planning, supplier diversification, communication, expediting, risk management", "Supply chain management, crisis response, stakeholder communication, problem-solving", "Hard"),
        ("Operations Manager", "Technical", "What metrics do you use to measure operational efficiency, and how do you drive continuous improvement?", "KPI, SLA, throughput, cycle time, waste, Lean, Six Sigma, continuous improvement", "Operational metrics, process excellence, Lean/Six Sigma methodology, performance management", "Medium"),
        # === CUSTOMER SUPPORT LEAD ===
        ("Customer Support Lead", "Behavioral", "Describe a time you handled an extremely angry customer. How did you de-escalate the situation and resolve their issue?", "empathy, active listening, de-escalation, solution, follow-up, patience", "Customer service excellence, conflict de-escalation, emotional intelligence, problem resolution", "Medium"),
        ("Customer Support Lead", "Situational", "Your team's customer satisfaction scores have dropped by 10 points. Walk me through how you would diagnose and fix the issue.", "data analysis, team feedback, training, quality assurance, process improvement, metrics", "Quality management, team development, root cause analysis, customer experience improvement", "Hard"),
        ("Customer Support Lead", "Behavioral", "Tell me about a time you had to train a new team member and help them meet performance standards quickly.", "mentorship, training, onboarding, feedback, milestones, coaching", "Team development, training methodology, performance management, leadership", "Medium"),
        # === HEALTHCARE ADMINISTRATOR ===
        ("Healthcare Administrator", "Behavioral", "Describe a time you had to manage a crisis in a healthcare setting, such as a staffing shortage or equipment failure.", "crisis management, patient safety, staffing, resource allocation, communication, protocol", "Healthcare operations, crisis leadership, patient-centered decision making, team coordination", "Hard"),
        ("Healthcare Administrator", "Technical", "How do you ensure compliance with healthcare regulations such as HIPAA while maintaining efficient operations?", "compliance, HIPAA, privacy, audit, training, protocols, confidentiality", "Healthcare regulations, compliance management, operational efficiency, risk mitigation", "Medium"),
        ("Healthcare Administrator", "Situational", "Your facility is facing budget cuts while patient volumes are increasing. How would you allocate resources effectively?", "budgeting, prioritization, efficiency, resource allocation, stakeholder input, cost optimization", "Financial management, healthcare operations, strategic prioritization, stakeholder collaboration", "Hard"),
        # === PROJECT MANAGER ===
        ("Project Manager", "Behavioral", "Tell me about a project that was falling behind schedule. How did you get it back on track?", "schedule, risk mitigation, resources, communication, prioritization, timeline", "Project recovery, stakeholder management, risk management, adaptive planning", "Medium"),
        ("Project Manager", "Technical", "What project management methodologies have you used? Compare Agile, Scrum, and Waterfall approaches.", "Agile, Scrum, Waterfall, sprint, iteration, documentation, ceremonies, deliverables", "Project methodology, lifecycle comparison, process selection, team framework knowledge", "Medium"),
        ("Project Manager", "Behavioral", "Describe a conflict between team members on a project you managed. How did you resolve it?", "mediation, communication, resolution, teamwork, project goals, collaboration", "Conflict resolution, team dynamics, leadership, maintaining project momentum", "Medium"),
        # === BUSINESS ANALYST ===
        ("Business Analyst", "Technical", "How do you gather and document requirements from stakeholders? What techniques do you use?", "interviews, surveys, workshops, documentation, BRD, user stories, acceptance criteria", "Requirements gathering, stakeholder elicitation, documentation standards, validation", "Medium"),
        ("Business Analyst", "Behavioral", "Describe a time when you identified a gap between business needs and the proposed solution. How did you bridge it?", "gap analysis, solution assessment, communication, alternatives, recommendation", "Business process analysis, solution evaluation, stakeholder negotiation, critical thinking", "Medium"),
        ("Business Analyst", "Situational", "A stakeholder is insisting on a feature that you know will not solve the underlying business problem. How do you handle this?", "analysis, data-driven reasoning, alternative proposal, stakeholder management, influence", "Stakeholder management, analytical communication, problem reframing, negotiation", "Hard"),
        # === TEACHER / EDUCATOR ===
        ("Teacher/Educator", "Behavioral", "Describe a time you had to adapt your teaching style to accommodate a student with different learning needs.", "adaptation, differentiation, inclusive, engagement, assessment, support", "Differentiated instruction, inclusive education, student-centered learning, adaptability", "Medium"),
        ("Teacher/Educator", "Situational", "How would you handle a classroom where students are disengaged and not participating in lessons?", "engagement strategies, interactive learning, rapport, feedback, motivation, variety", "Classroom management, student engagement, pedagogical approaches, motivational techniques", "Medium"),
        ("Teacher/Educator", "Behavioral", "Tell me about a time you implemented a new teaching method or technology that improved student outcomes.", "innovation, technology, curriculum, assessment, improvement, results", "Educational innovation, technology integration, curriculum development, outcome measurement", "Medium"),
        # === RETAIL MANAGER ===
        ("Retail Manager", "Behavioral", "Describe a time you improved the customer experience in your store, leading to increased sales or customer loyalty.", "customer experience, sales growth, loyalty, service, training, merchandising", "Retail operations, customer experience management, sales strategy, team leadership", "Medium"),
        ("Retail Manager", "Situational", "Your store is consistently missing its monthly sales targets. Walk me through your plan to turn performance around.", "sales strategy, training, inventory, promotions, staffing, analysis, targets", "Retail turnaround, performance management, strategic planning, team motivation", "Hard"),
        ("Retail Manager", "Technical", "How do you manage inventory to minimize shrinkage while ensuring popular items are always in stock?", "inventory management, shrinkage, stock levels, forecasting, audits, supply chain", "Inventory optimization, loss prevention, demand forecasting, supply chain coordination", "Medium"),
        # === LEGAL ASSOCIATE ===
        ("Legal Associate", "Behavioral", "Describe a time you had to manage multiple high-priority cases with conflicting deadlines. How did you prioritize?", "prioritization, deadlines, organization, case management, communication, efficiency", "Legal workflow management, time management, attention to detail, professional judgment", "Medium"),
        ("Legal Associate", "Technical", "What steps do you take to ensure legal documents are accurate, thorough, and compliant with regulations?", "document review, compliance, accuracy, research, attention to detail, verification", "Legal documentation, regulatory compliance, quality assurance, research methodology", "Medium"),
        ("Legal Associate", "Situational", "You discover a potential conflict of interest in a case you have been working on. What do you do?", "conflict of interest, ethics, disclosure, recusal, compliance, integrity", "Legal ethics, professional responsibility, transparency, regulatory compliance", "Hard"),
        # === GRAPHIC DESIGNER ===
        ("Graphic Designer", "Behavioral", "Tell me about a time a client rejected your design concept. How did you handle the feedback and what was the outcome?", "feedback, revision, communication, client management, compromise, creativity", "Design process, client communication, constructive feedback handling, creative problem-solving", "Medium"),
        ("Graphic Designer", "Technical", "Walk me through your design process from client brief to final deliverable. What tools and methodologies do you use?", "wireframe, mockup, prototyping, Figma, Adobe, iteration, user testing, feedback", "Design thinking, tools proficiency, workflow structure, user-centered design", "Medium"),
        ("Graphic Designer", "Behavioral", "Describe a project where you had to balance creative vision with tight budget or timeline constraints.", "trade-offs, prioritization, efficiency, creativity under constraints, resourcefulness", "Creative resource management, deadline management, adaptive design, client satisfaction", "Hard"),
        # === CONTENT WRITER ===
        ("Content Writer", "Behavioral", "Describe a time your content strategy significantly increased audience engagement. What did you do differently?", "strategy, engagement, analytics, audience, SEO, storytelling, metrics", "Content marketing, audience development, performance analysis, strategic writing", "Medium"),
        ("Content Writer", "Technical", "How do you approach SEO keyword research while maintaining high-quality, reader-friendly content?", "SEO, keywords, readability, search intent, headers, meta, quality, engagement", "SEO writing, content optimization, reader experience balance, search engine best practices", "Medium"),
        ("Content Writer", "Situational", "You are assigned a topic you know very little about with a tight deadline. How do you deliver quality content?", "research, efficiency, structure, expert consultation, prioritization, learning agility", "Research methodology, time management, adaptability, content quality standards", "Hard"),
    ]

    for q in questions_data:
        cur.execute('''
        INSERT INTO questions (role, category, question_text, optimal_keywords, expected_concepts, difficulty)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', q)


def seed_users_and_history(cur):
    """Seed a demo user with mock interview history."""
    # Demo user
    cur.execute('''
    INSERT INTO users (name, email, target_role)
    VALUES (?, ?, ?)
    ''', ('Demo User', 'demo@example.com', 'Software Engineer'))
    user_id = cur.lastrowid

    # Resolve the real question ids instead of hardcoding 1/2/4/5 below. Those
    # literals only line up on a database that has never been reset: SQLite's
    # AUTOINCREMENT never reuses ids, so after a "Danger Zone" reset the first
    # questions are numbered far higher and the hardcoded references became
    # orphaned answers (SQLite did not enforce foreign keys, so nothing
    # complained; PostgreSQL does, and this keeps the seed honest on both).
    cur.execute('SELECT id FROM questions ORDER BY id LIMIT 5')
    question_ids = [row['id'] for row in cur.fetchall()]
    if len(question_ids) < 5:
        return  # question bank is not in the expected shape — skip the demo history

    # Interview 1: 5 days ago, score 72.5%
    date_1 = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('''
    INSERT INTO interviews (user_id, role, date, overall_score, summary)
    VALUES (?, 'Software Engineer', ?, 72.5,
    'The candidate demonstrated solid database knowledge but struggled slightly with the behavioral conflict resolution question, showing average communication structure.')
    ''', (user_id, date_1))
    interview_1_id = cur.lastrowid

    # Answer Q1
    fb_q1 = {
        "score": 65, "clarity": 70, "grammar": 80, "relevance": 60, "filler_count": 8,
        "strengths": ["Clear timeline explanation of the conflict details", "Identified the technical root cause clearly"],
        "weaknesses": ["Struggled to show structural compromise", "High usage of filler words ('like', 'um') during the compromise stage"],
        "tips": ["Use the STAR method: Situation, Task, Action, Result", "Focus on what you learned from the resolution process"]
    }
    cur.execute('''
    INSERT INTO answers (interview_id, question_id, transcript, feedback_json, score)
    VALUES (?, ?,
    'So, we had this issue where my coworker wanted to use Mongo and I wanted Postgres. We argued about it for like two days and it was really annoying. Eventually, the tech lead decided we should use Postgres because we needed strong relational integrity, so we did that and it worked out ok I guess.',
    ?, 65.0)
    ''', (interview_1_id, question_ids[0], json.dumps(fb_q1)))

    # Answer Q2
    fb_q2 = {
        "score": 80, "clarity": 85, "grammar": 90, "relevance": 82, "filler_count": 3,
        "strengths": ["Accurate definition of ACID properties in SQL database systems", "Excellent contrast of vertical and horizontal scaling properties"],
        "weaknesses": ["Missed discussing document-based versus table-based visual formatting differences"],
        "tips": ["Mention specific database options like MongoDB or Redis to ground NoSQL descriptions in practical examples"]
    }
    cur.execute('''
    INSERT INTO answers (interview_id, question_id, transcript, feedback_json, score)
    VALUES (?, ?,
    'Relational databases use structured tables with fixed schemas and support ACID compliance, which makes them great for transaction consistency. Non-relational databases, or NoSQL, are schema-less, support key-value or document formats, and scale horizontally very well. I would choose SQL for financial systems and NoSQL for catalog or high-volume logging data.',
    ?, 80.0)
    ''', (interview_1_id, question_ids[1], json.dumps(fb_q2)))

    # Interview 2: 2 days ago, score 84%
    date_2 = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('''
    INSERT INTO interviews (user_id, role, date, overall_score, summary)
    VALUES (?, 'Software Engineer', ?, 84.0,
    'The candidate showed excellent growth in communication structure and presented highly structured algorithmic and architectural comparisons. Very few filler words were detected.')
    ''', (user_id, date_2))
    interview_2_id = cur.lastrowid

    # Answer Q4
    fb_q4 = {
        "score": 82, "clarity": 85, "grammar": 88, "relevance": 80, "filler_count": 2,
        "strengths": ["Well-structured explanation of REST endpoints vs GraphQL queries", "Excellent description of the over-fetching data problem in mobile clients"],
        "weaknesses": ["Could have detailed caching differences (HTTP level for REST vs application level for GraphQL)"],
        "tips": ["Highlight standard status codes like 200 OK or 404 Not Found to make REST technical points concrete"]
    }
    cur.execute('''
    INSERT INTO answers (interview_id, question_id, transcript, feedback_json, score)
    VALUES (?, ?,
    'REST APIs rely on fixed endpoints representing resources, which can lead to over-fetching or under-fetching of data. GraphQL solves this by allowing clients to write custom queries to request exactly what fields they need in a single round-trip. REST is simpler to cache at the HTTP layer, while GraphQL offers much more flexibility for complex frontend layouts.',
    ?, 82.0)
    ''', (interview_2_id, question_ids[3], json.dumps(fb_q4)))

    # Answer Q5
    fb_q5 = {
        "score": 86, "clarity": 90, "grammar": 90, "relevance": 88, "filler_count": 1,
        "strengths": ["Extremely clear description of Big O as asymptotic runtime boundary notation", "Accurate execution examples (O(1) dictionary lookups, O(log n) binary search)"],
        "weaknesses": ["Did not mention the distinction between average case and worst case analysis scenarios"],
        "tips": ["Always state that Big O focuses on worst-case bounds unless specified otherwise"]
    }
    cur.execute('''
    INSERT INTO answers (interview_id, question_id, transcript, feedback_json, score)
    VALUES (?, ?,
    'Big O notation measures how the runtime or memory requirements of an algorithm scale with the input size N. O(1) represents constant time, like looking up an item in a hash map. O(n) is linear time, like a simple loop through an array. O(log n) is logarithmic time, where the search space is halved at each step, which is seen in binary search algorithms.',
    ?, 86.0)
    ''', (interview_2_id, question_ids[4], json.dumps(fb_q5)))


if __name__ == '__main__':
    init_db()
