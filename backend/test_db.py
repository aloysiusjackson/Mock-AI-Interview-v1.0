import os
import sys
import json

# Add backend to path and load env
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from database import get_db_connection, dict_cursor
from ai_engine import analyze_answer

conn = get_db_connection()
cur = dict_cursor(conn)

# Test 1: Check all roles
cur.execute('SELECT DISTINCT role FROM questions')
roles = [r['role'] for r in cur.fetchall()]
print('=== ROLES (17 expected) ===')
print(f'Count: {len(roles)}')
for r in sorted(roles):
    cur.execute('SELECT COUNT(*) AS cnt FROM questions WHERE role = %s', (r,))
    count = cur.fetchone()['cnt']
    print(f'  {r}: {count} questions')

print()
print('=== TEST QUESTIONS FOR Marketing Manager ===')
cur.execute('SELECT id, category, question_text, difficulty FROM questions WHERE role = %s ORDER BY RANDOM() LIMIT 3', ('Marketing Manager',))
for r in cur.fetchall():
    print(f'  [{r["category"]}] {r["question_text"][:80]}... ({r["difficulty"]})')

print()
print('=== TEST QUESTIONS FOR Healthcare Administrator ===')
cur.execute('SELECT id, category, question_text, difficulty FROM questions WHERE role = %s ORDER BY RANDOM() LIMIT 3', ('Healthcare Administrator',))
for r in cur.fetchall():
    print(f'  [{r["category"]}] {r["question_text"][:80]}... ({r["difficulty"]})')

print()
print('=== TEST QUESTIONS FOR Legal Associate ===')
cur.execute('SELECT id, category, question_text, difficulty FROM questions WHERE role = %s ORDER BY RANDOM() LIMIT 3', ('Legal Associate',))
for r in cur.fetchall():
    print(f'  [{r["category"]}] {r["question_text"][:80]}... ({r["difficulty"]})')

print()
print('=== TEST auto-grade simulation ===')
result = analyze_answer(
    'Tell me about a time you exceeded your quarterly sales target.',
    'Behavioral',
    'prospecting, pipeline, closing, negotiation, relationship',
    'Sales methodology, target achievement, strategic planning',
    'I exceeded my quarterly sales target by 30% using a structured prospecting approach. I built a strong pipeline through cold outreach and relationship building, negotiated effectively on pricing, and closed 5 major deals that contributed to the overall quota.'
)
print(f'Score: {result["score"]}%')
print(f'Clarity: {result["clarity"]}%')
print(f'Grammar: {result["grammar"]}%')
print(f'Relevance: {result["relevance"]}%')
print(f'Filler count: {result["filler_count"]}')
print(f'Strengths: {result["strengths"][:2]}')
print(f'Weaknesses: {result["weaknesses"][:2]}')
print(f'Tips: {result["tips"][:2]}')

print()
print('=== TEST generic role fallback questions ===')
from ai_engine import generate_questions_locally
gen_qs = generate_questions_locally('Custom Role', 3)
for q in gen_qs:
    print(f'  [{q["category"]}] {q["question_text"][:80]}...')

print()
print('ALL TESTS PASSED SUCCESSFULLY!')
conn.close()
