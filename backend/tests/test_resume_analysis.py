"""Resume analysis: the local engine has to read the resume, not invent it.

Two regressions prompted these tests:

1.  The offline analyzer matched skills with a substring test, so "Built"
    contained "ui" and "digital" contained "git". Nearly every resume came back
    with the same invented skills, and the role was guessed with a first-match
    chain, which labelled a data scientist "Backend Developer" purely because the
    resume mentioned Python.
2.  `interview.startWithResumeQuestions` did not exist: an unterminated JSDoc
    comment swallowed the signature and body, so pressing "Start Interview"
    after uploading a resume silently did nothing.

The suite always runs the local engine - `support.py` blanks GEMINI_API_KEY and
refuses to start if one is set.
"""

from __future__ import annotations

import io
import json
import os
import re
import unittest

import ai_engine
from support import FRONTEND_DIR, ApiTestCase

ML_RESUME = """PRIYA RAMAN
Data Scientist

SUMMARY
Machine learning engineer with 5 years building predictive models in Python.

SKILLS
Python, TensorFlow, PyTorch, pandas, numpy, scikit-learn, SQL, PostgreSQL, AWS, Docker, Airflow

EXPERIENCE
Senior Data Scientist, Acme Analytics (2021-present)
- Built a churn prediction model in Python with TensorFlow that cut customer loss by 18%
- Designed an Airflow pipeline loading 40M rows/day into PostgreSQL
- Deployed models to AWS with Docker
"""

MARKETING_RESUME = """JOHN OKORO
Marketing Manager

SKILLS
SEO, SEM, Google Analytics, campaign management, social media, content, brand strategy, HubSpot

EXPERIENCE
Marketing Manager, Zeta Foods (2020-present)
- Ran an SEO programme that lifted organic traffic 140%
- Managed a paid campaign budget of $250k per quarter
"""

NURSE_RESUME = """Registered Nurse
Patient care, clinical assessment, EHR documentation, medication administration, ICU experience.
"""


class ResumeSkillExtractionTests(unittest.TestCase):
    """Skill matching must respect word boundaries."""

    def skills(self, text):
        return ai_engine._extract_resume_skills(ai_engine._normalise_resume(text))

    def test_substrings_do_not_become_skills(self):
        # Every skill-ish token the old substring test used to find here comes
        # from inside a longer word: ui(Built), ai(paid), git(digital).
        text = "Built a new tool. Got paid for it. Maintained the digital guidance documents."
        self.assertEqual(self.skills(text), [])

    def test_real_skills_are_found(self):
        skills = self.skills(ML_RESUME)
        for expected in ("machine learning", "tensorflow", "pytorch", "pandas", "sql"):
            self.assertIn(expected, skills)

    def test_skills_are_not_duplicated(self):
        skills = self.skills(ML_RESUME)
        self.assertEqual(len(skills), len(set(skills)))

    def test_a_skills_list_is_not_treated_as_an_achievement(self):
        # The MARKETING_RESUME skills line is comma-heavy; it is not a result.
        highlights = ai_engine._extract_resume_highlights(MARKETING_RESUME)
        self.assertTrue(highlights)
        for line in highlights:
            self.assertNotIn("HubSpot", line)

    def test_achievements_come_from_the_resume(self):
        highlights = ai_engine._extract_resume_highlights(ML_RESUME)
        self.assertTrue(any("churn prediction" in line for line in highlights))


class ResumeRoleDetectionTests(unittest.TestCase):
    """The detected role must follow the resume, not a first-match chain."""

    def role(self, text):
        return ai_engine.analyze_resume_text(text, 3)["detected_role"]

    def test_data_scientist_is_not_labelled_a_backend_developer(self):
        role = self.role(ML_RESUME)
        self.assertNotEqual(role, "Backend Developer")
        self.assertIn("Data Scientist", role)

    def test_marketing_resume_keeps_its_own_title(self):
        self.assertIn("Marketing", self.role(MARKETING_RESUME))

    def test_non_technical_resumes_are_recognised(self):
        self.assertIn("Nurse", self.role(NURSE_RESUME))

    def test_skills_alone_still_produce_a_role(self):
        # No title line anywhere, so this has to be scored from the skills.
        text = "SKILLS\nPython, TensorFlow, pandas, scikit-learn, statistics, numpy"
        role = self.role(text)
        self.assertIn(role, ("Data Scientist", "Machine Learning Engineer"))

    def test_unrecognisable_resume_falls_back_honestly(self):
        self.assertEqual(self.role("Just a name and nothing else."), "Professional")


class ResumeQuestionTests(unittest.TestCase):
    """Questions must be built from the resume and keep the expected shape."""

    def analyse(self, text, count=3):
        return ai_engine.analyze_resume_text(text, count)

    def test_questions_quote_the_candidates_own_achievement(self):
        questions = self.analyse(ML_RESUME, 3)["questions"]
        self.assertTrue(
            any("churn prediction" in q["question_text"] for q in questions),
            questions,
        )

    def test_technical_question_is_tied_to_a_resume_skill(self):
        questions = self.analyse(ML_RESUME, 3)["questions"]
        technical = [q for q in questions if q["category"] == "Technical"]
        self.assertTrue(technical)
        # A curated prompt reads naturally instead of shoehorning the skill name
        # into the sentence, so the skill must still be carried in the scoring
        # metadata that the grader uses.
        haystack = " ".join(
            q["question_text"] + " " + q["optimal_keywords"] + " " + q["expected_concepts"]
            for q in technical
        ).lower()
        self.assertTrue(
            any(
                skill in haystack
                for skill in ("tensorflow", "pytorch", "machine learning", "deep learning", "python", "data science")
            ),
            haystack,
        )

    def test_invented_skills_never_reach_the_questions(self):
        for question in self.analyse(ML_RESUME, 5)["questions"]:
            self.assertNotIn("best practices in ui", question["question_text"].lower())

    def test_question_count_is_respected_and_clamped(self):
        for count in (1, 2, 3, 5, 10):
            with self.subTest(count=count):
                questions = self.analyse(ML_RESUME, count)["questions"]
                self.assertLessEqual(len(questions), count)
                self.assertGreaterEqual(len(questions), 1)

    def test_thin_resume_still_yields_the_requested_count(self):
        result = self.analyse("Just a name and nothing else.", 3)
        self.assertEqual(len(result["questions"]), 3)
        self.assertEqual(result["detected_role"], "Professional")

    def test_question_payload_matches_the_frontend_contract(self):
        questions = self.analyse(ML_RESUME, 3)["questions"]
        required = {
            "id", "role", "question_text", "category",
            "optimal_keywords", "expected_concepts", "difficulty",
        }
        self.assertEqual(len({q["id"] for q in questions}), len(questions))
        for question in questions:
            with self.subTest(question=question["question_text"][:40]):
                self.assertTrue(required.issubset(question))
                self.assertTrue(str(question["id"]).startswith("-"))
                self.assertIn(question["category"], ("Behavioral", "Technical", "Situational"))
                self.assertIn(question["difficulty"], ("Easy", "Medium", "Hard"))
                self.assertTrue(question["question_text"].strip())

    def test_result_carries_the_findings_the_modal_shows(self):
        result = self.analyse(ML_RESUME, 3)
        self.assertTrue(result["summary"])
        self.assertTrue(result["skills"])
        self.assertEqual(result["detected_role"], result["questions"][0]["role"])


class ResumeQATests(unittest.TestCase):
    """In-interview Q&A must answer from the resume text it was given."""

    def answer(self, question, resume=ML_RESUME):
        return ai_engine.ask_resume_question(question, resume)["answer"]

    def test_skills_question_lists_the_real_skills(self):
        answer = self.answer("What are my skills?")
        self.assertIn("tensorflow", answer.lower())

    def test_achievement_question_quotes_the_resume(self):
        answer = self.answer("Tell me about my projects")
        self.assertIn("churn prediction", answer)

    def test_years_of_experience_is_extracted(self):
        self.assertIn("5", self.answer("How many years of experience do I have?"))

    def test_education_is_reported_as_missing_rather_than_invented(self):
        answer = self.answer("What is my education?")
        self.assertIn("could not find", answer.lower())

    def test_a_technology_absent_from_the_resume_is_reported_honestly(self):
        answer = self.answer("Do I have Kubernetes experience?")
        self.assertIn("kubernetes", answer.lower())
        self.assertIn("could not find", answer.lower())

    def test_a_technology_present_in_the_resume_is_answered_positively(self):
        answer = self.answer("Do I have SQL experience?")
        self.assertNotIn("could not find", answer.lower())

    def test_qa_never_crashes_on_an_empty_resume(self):
        self.assertTrue(self.answer("What are my skills?", resume="").strip())


class ResumeApiTests(ApiTestCase):
    """The upload endpoint the modal actually calls."""

    def upload(self, text, filename="resume.txt", question_count="3", client=None):
        client = client or self.client
        payload = {"resume": (io.BytesIO(text.encode("utf-8")), filename)}
        if question_count is not None:
            payload["question_count"] = question_count
        return client.post(
            "/api/analyze-resume",
            data=payload,
            content_type="multipart/form-data",
        )

    def test_upload_returns_resume_derived_questions(self):
        response = self.upload(ML_RESUME)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["detected_role"], "Data Scientist")
        self.assertIn("machine learning", body["skills"])
        self.assertTrue(body["questions"])
        self.assertIn("churn prediction", json.dumps(body["questions"]))
        # The Q&A in the interview needs the raw text back.
        self.assertIn("PRIYA RAMAN", body["full_text"])

    def test_question_count_from_the_form_is_honoured(self):
        body = self.upload(ML_RESUME, question_count="5").get_json()
        self.assertEqual(len(body["questions"]), 5)

    def test_empty_file_is_rejected(self):
        response = self.upload("")
        self.assertEqual(response.status_code, 400)

    def test_missing_file_is_rejected(self):
        response = self.client.post("/api/analyze-resume", data={}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)


class ResumeInterviewWiringTests(unittest.TestCase):
    """The frontend hook the backend cannot cover."""

    def read(self, relative):
        path = os.path.join(FRONTEND_DIR, *relative.split("/"))
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_resume_interview_method_is_actually_defined(self):
        # If the JSDoc above the method is not closed first, the signature lands
        # inside the comment and the method silently disappears - which is
        # exactly how this feature broke.
        source = self.read("js/interview.js")
        self.assertRegex(
            source,
            r"\*/\s*\n\s*async\s+startWithResumeQuestions\s*\(\s*questions\s*,\s*role\s*,\s*resumeText\s*\)",
        )

    def test_every_method_the_resume_flow_calls_exists(self):
        app_js = self.read("js/app.js")
        interview_js = self.read("js/interview.js")
        called = set(re.findall(r"interview\.(\w+)\s*\(", app_js))
        defined = set(re.findall(r"^\s{4}(?:async\s+)?(\w+)\s*\(", interview_js, re.MULTILINE))
        self.assertTrue(called, "no interview.*() calls found - did app.js change?")
        self.assertEqual(called - defined, set())

    def test_resume_upload_wires_the_analysis_into_the_modal(self):
        app_js = self.read("js/app.js")
        self.assertIn("api.analyzeResume(", app_js)
        self.assertIn("resumeData.skills", app_js)


if __name__ == "__main__":
    unittest.main()
