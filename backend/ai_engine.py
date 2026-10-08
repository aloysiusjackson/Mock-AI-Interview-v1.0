import os
import json
import re
from dotenv import load_dotenv

# Load env variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Try to import google-generativeai, but don't fail if not available
genai = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai_module
        genai_module.configure(api_key=GEMINI_API_KEY)
        genai = genai_module
    except (ImportError, Exception) as e:
        print(f"Warning: google-generativeai not available ({e}). Falling back to local NLP analysis.")
        GEMINI_API_KEY = None

def analyze_answer(question_text, category, optimal_keywords, expected_concepts, transcript):
    """
    Main entrypoint for grading answers. Checks if Gemini is available, otherwise falls back to local NLP engine.
    """
    if not transcript or len(transcript.strip()) == 0:
        return {
            "score": 0,
            "clarity": 0,
            "grammar": 0,
            "relevance": 0,
            "filler_count": 0,
            "strengths": ["None (No response provided)"],
            "weaknesses": ["You did not provide a transcript response."],
            "tips": ["Make sure to speak clearly into your microphone to record your response."]
        }

    if GEMINI_API_KEY:
        try:
            return analyze_with_gemini(question_text, category, optimal_keywords, expected_concepts, transcript)
        except Exception as e:
            print(f"Gemini API analysis failed: {e}. Falling back to local NLP engine.")
            return analyze_locally(question_text, category, optimal_keywords, expected_concepts, transcript)
    else:
        return analyze_locally(question_text, category, optimal_keywords, expected_concepts, transcript)

def analyze_with_gemini(question_text, category, optimal_keywords, expected_concepts, transcript):
    if genai is None:
        raise ImportError("google-generativeai not available")
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    You are an expert AI Job Interviewer. Analyze the following candidate's answer to the given question.
    
    Question: {question_text}
    Category: {category}
    Expected Keywords/Concepts: {optimal_keywords} | {expected_concepts}
    Candidate's Transcript: "{transcript}"
    
    Evaluate the response and output a JSON object EXACTLY in the following format. Ensure all values are filled. Do not include any markdown wrappers or backticks in the response. Output raw JSON only.
    
    Format:
    {{
        "score": <overall score integer between 0 and 100>,
        "clarity": <clarity/structure score integer between 0 and 100>,
        "grammar": <grammar/vocabulary score integer between 0 and 100>,
        "relevance": <relevance/technical accuracy score integer between 0 and 100>,
        "filler_count": <integer representing count of filler words like 'uh', 'um', 'like', 'actually', 'so' used unnecessary as crutches>,
        "strengths": [<list of 2-3 specific strengths of this response>],
        "weaknesses": [<list of 1-2 constructive weaknesses or missed points>],
        "tips": [<list of 2-3 actionable tips for improvement (e.g. using the STAR method for behavioral, or describing trade-offs for technical)>]
    }}
    """
    
    response = model.generate_content(prompt)
    response_text = response.text.strip()
    
    # Strip markdown code blocks if the model wrapped it in ```json ... ```
    if response_text.startswith("```"):
        # Match anything inside backticks
        match = re.search(r"```(?:json)?(.*?)```", response_text, re.DOTALL)
        if match:
            response_text = match.group(1).strip()
            
    # Try parsing
    try:
        data = json.loads(response_text)
        # Validate keys
        required_keys = ["score", "clarity", "grammar", "relevance", "filler_count", "strengths", "weaknesses", "tips"]
        if all(k in data for k in required_keys):
            return data
    except Exception as parse_error:
        print(f"Error parsing Gemini response JSON: {parse_error}. Response content: {response_text}")
        
    # Fallback if parsing failed
    return analyze_locally(question_text, category, optimal_keywords, expected_concepts, transcript)

def generate_questions(role, count=3):
    """
    Generates interview questions for a given role using Gemini AI.
    Falls back to template-based questions if AI is unavailable.
    """
    if GEMINI_API_KEY:
        try:
            return generate_questions_with_gemini(role, count)
        except Exception as e:
            print(f"Gemini question generation failed: {e}. Using template fallback.")
            return generate_questions_locally(role, count)
    else:
        return generate_questions_locally(role, count)

def generate_questions_with_gemini(role, count=3):
    if genai is None:
        raise ImportError("google-generativeai not available")
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    You are an expert interview question generator. Generate {count} interview questions for a candidate applying for a "{role}" position.
    
    For each question, provide:
    1. The question text
    2. The category (one of: Behavioral, Technical, Situational)
    3. Optimal keywords that should appear in a good answer (comma-separated)
    4. Expected concepts that should be covered (comma-separated)
    5. Difficulty level (one of: Easy, Medium, Hard)
    
    Output a JSON array EXACTLY in the following format. Do not include any markdown wrappers or backticks. Output raw JSON only.
    
    Format:
    [
        {{
            "question_text": "The interview question text here",
            "category": "Behavioral",
            "optimal_keywords": "keyword1, keyword2, keyword3",
            "expected_concepts": "concept1, concept2, concept3",
            "difficulty": "Medium"
        }},
        ...
    ]
    """
    
    response = model.generate_content(prompt)
    response_text = response.text.strip()
    
    # Strip markdown code blocks if the model wrapped it
    if response_text.startswith("```"):
        match = re.search(r"```(?:json)?(.*?)```", response_text, re.DOTALL)
        if match:
            response_text = match.group(1).strip()
    
    try:
        data = json.loads(response_text)
        if isinstance(data, list):
            # Add id and role fields
            for i, q in enumerate(data):
                q["id"] = -(i + 1)  # Use negative IDs for AI-generated questions
                q["role"] = role
                if "category" not in q:
                    q["category"] = "Behavioral"
                if "difficulty" not in q:
                    q["difficulty"] = "Medium"
                if "optimal_keywords" not in q:
                    q["optimal_keywords"] = ""
                if "expected_concepts" not in q:
                    q["expected_concepts"] = ""
            return data[:count]
    except Exception as e:
        print(f"Error parsing Gemini generated questions JSON: {e}")
        
    return generate_questions_locally(role, count)

def generate_questions_locally(role, count=3):
    """
    Template-based question generation for roles not in the database.
    Creates relevant questions based on the role name.
    """
    templates = [
        {
            "category": "Behavioral",
            "question": f"Tell me about a time you demonstrated leadership skills in a {role} context. What was the outcome?",
            "keywords": "leadership, team, outcome, initiative, responsibility",
            "concepts": "Leadership experience, team collaboration, measurable results, initiative"
        },
        {
            "category": "Behavioral",
            "question": f"Describe a challenging situation you faced as a {role} and how you overcame it.",
            "keywords": "challenge, problem-solving, solution, adaptation, result",
            "concepts": "Problem-solving, resilience, critical thinking, adaptability"
        },
        {
            "category": "Technical",
            "question": f"What are the most important skills and tools for a {role} to master, and why?",
            "keywords": "skills, tools, proficiency, expertise, best practices",
            "concepts": "Domain knowledge, tool proficiency, industry best practices, continuous learning"
        },
        {
            "category": "Situational",
            "question": f"You are working as a {role} and your team misses a critical deadline. How do you handle the situation?",
            "keywords": "deadline, accountability, communication, recovery, plan",
            "concepts": "Crisis management, accountability, team communication, process improvement"
        },
        {
            "category": "Behavioral",
            "question": f"Describe a time you had to learn a new skill or tool quickly to succeed in a {role} position.",
            "keywords": "learning, adaptation, skill development, initiative, growth",
            "concepts": "Learning agility, self-development, initiative, adaptability"
        },
        {
            "category": "Technical",
            "question": f"What metrics or KPIs do you consider most important when evaluating success in a {role} position?",
            "keywords": "metrics, KPI, evaluation, success, measurement, performance",
            "concepts": "Performance measurement, analytical thinking, results orientation, domain metrics"
        },
        {
            "category": "Situational",
            "question": f"As a {role}, you are given a project with limited resources and a tight deadline. How do you prioritize?",
            "keywords": "prioritization, resource management, deadline, efficiency, trade-offs",
            "concepts": "Resource allocation, priority setting, time management, strategic thinking"
        },
        {
            "category": "Behavioral",
            "question": f"Tell me about a time you received constructive criticism in a {role} role. How did you respond and grow from it?",
            "keywords": "feedback, improvement, growth, reflection, adaptation",
            "concepts": "Receptiveness to feedback, continuous improvement, self-awareness, professional growth"
        },
    ]
    
    # Shuffle and pick requested count
    import random
    random.shuffle(templates)
    selected = templates[:count]
    
    questions = []
    for i, t in enumerate(selected):
        questions.append({
            "id": -(i + 1),
            "role": role,
            "question_text": t["question"],
            "category": t["category"],
            "optimal_keywords": t["keywords"],
            "expected_concepts": t["concepts"],
            "difficulty": "Medium"
        })
    
    return questions

def analyze_resume_text(resume_text, question_count=3):
    """
    Analyzes resume text and generates interview questions tailored to the candidate's experience.
    """
    if GEMINI_API_KEY:
        try:
            return analyze_resume_with_gemini(resume_text, question_count)
        except Exception as e:
            print(f"Gemini resume analysis failed: {e}. Using local analysis.")
            return analyze_resume_locally(resume_text, question_count)
    else:
        return analyze_resume_locally(resume_text, question_count)

def analyze_resume_with_gemini(resume_text, question_count=3):
    if genai is None:
        raise ImportError("google-generativeai not available")
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Generate unique questions each time by including timestamp
    import time
    unique_id = int(time.time() * 1000) % 10000
    
    prompt = f"""
    You are an expert AI interview coach. Analyze the following resume UNIQUELY and generate {question_count} different, highly specific interview questions 
    that would help this candidate prepare for interviews based on their specific background and experience.
    
    This is analysis request #{unique_id}. Generate fresh, unique questions not asked before.
    
    Resume Content:
    {resume_text[:4000]}
    
    For each question, provide:
    1. The question text - make it specific to technologies/skills mentioned in THIS resume
    2. The category (one of: Behavioral, Technical, Situational)
    3. Optimal keywords that should appear in a good answer (comma-separated)
    4. Expected concepts that should be covered (comma-separated)
    5. Difficulty level (one of: Easy, Medium, Hard)
    6. Also provide a brief summary of the candidate's background (key skills, experience, role)
    
    Output a JSON object EXACTLY in the following format. Do not include any markdown wrappers or backticks. Output raw JSON only.
    
    Format:
    {{
        "summary": "Brief summary of candidate's background (1-2 sentences)",
        "detected_role": "Inferred target role",
        "questions": [
            {{
                "question_text": "The interview question text here",
                "category": "Behavioral",
                "optimal_keywords": "keyword1, keyword2, keyword3",
                "expected_concepts": "concept1, concept2, concept3",
                "difficulty": "Medium"
            }},
            ...
        ]
    }}
    """
    
    try:
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        # Strip markdown code blocks if the model wrapped it
        if response_text.startswith("```"):
            match = re.search(r"```(?:json)?(.*?)```", response_text, re.DOTALL)
            if match:
                response_text = match.group(1).strip()
        
        data = json.loads(response_text)
        # Validate and process questions
        if 'questions' in data and isinstance(data['questions'], list):
            for i, q in enumerate(data['questions']):
                q["id"] = -(i + 1000 + (unique_id % 100))  # Unique IDs incorporating request ID
                q["role"] = data.get('detected_role', 'Based on Resume')
                if 'category' not in q:
                    q['category'] = 'Behavioral'
                if 'difficulty' not in q:
                    q['difficulty'] = 'Medium'
                if 'optimal_keywords' not in q:
                    q['optimal_keywords'] = ''
                if 'expected_concepts' not in q:
                    q['expected_concepts'] = ''
            return data
    except Exception as e:
        print(f"Error parsing Gemini resume response JSON: {e}. Falling back to local.")
        return analyze_resume_locally(resume_text, question_count)

# ── Resume analysis: local (offline) engine ──────────────────────────────
# GEMINI_API_KEY is optional, so on a default install this engine is what every
# candidate actually sees. It has to read the resume for real:
#   * skills match on word boundaries, so "Built" is never read as the skill
#     "ui" (the previous substring test produced that bogus skill for every
#     resume that contained the word "built"),
#   * the role comes from the resume's own job title when it states one,
#   * the questions quote the candidate's real achievements and name the
#     technologies their resume actually lists.

# Canonical skill -> the spellings that appear in real resumes.
RESUME_SKILLS = {
    'machine learning': ('machine learning', 'ml'),
    'deep learning': ('deep learning',),
    'tensorflow': ('tensorflow',),
    'pytorch': ('pytorch', 'torch'),
    'scikit-learn': ('scikit-learn', 'scikit learn', 'sklearn'),
    'pandas': ('pandas',),
    'numpy': ('numpy',),
    'data science': ('data science', 'data scientist'),
    'nlp': ('nlp', 'natural language processing'),
    'computer vision': ('computer vision', 'opencv'),
    'statistics': ('statistics', 'statistical'),
    'a/b testing': ('a/b testing', 'ab testing'),
    'tableau': ('tableau',),
    'power bi': ('power bi', 'powerbi'),
    'excel': ('excel',),
    'sql': ('sql',),
    'mysql': ('mysql',),
    'postgresql': ('postgresql', 'postgres'),
    'mongodb': ('mongodb', 'mongo'),
    'redis': ('redis',),
    'spark': ('spark',),
    'airflow': ('airflow',),
    'etl': ('etl',),
    'react': ('react',),
    'angular': ('angular',),
    'vue': ('vue', 'vuejs'),
    'svelte': ('svelte',),
    'next.js': ('next.js', 'nextjs', 'next js'),
    'javascript': ('javascript',),
    'typescript': ('typescript',),
    'html': ('html',),
    'css': ('css', 'scss', 'sass'),
    'tailwind': ('tailwind',),
    'redux': ('redux',),
    'node.js': ('node.js', 'nodejs', 'node js'),
    'express': ('express',),
    'django': ('django',),
    'flask': ('flask',),
    'fastapi': ('fastapi',),
    'spring': ('spring', 'spring boot'),
    'laravel': ('laravel',),
    'php': ('php',),
    '.net': ('.net', 'dotnet', 'asp.net'),
    'java': ('java',),
    'c#': ('c#',),
    'c++': ('c++',),
    'golang': ('golang',),
    'rust': ('rust',),
    'kotlin': ('kotlin',),
    'swift': ('swift',),
    'graphql': ('graphql',),
    'rest api': ('rest api', 'restful', 'rest apis'),
    'microservices': ('microservices', 'microservice'),
    'aws': ('aws', 'amazon web services'),
    'azure': ('azure',),
    'gcp': ('gcp', 'google cloud'),
    'docker': ('docker',),
    'kubernetes': ('kubernetes', 'k8s'),
    'terraform': ('terraform',),
    'ansible': ('ansible',),
    'jenkins': ('jenkins',),
    'ci/cd': ('ci/cd', 'ci cd', 'continuous integration'),
    'linux': ('linux',),
    'git': ('git', 'github', 'gitlab'),
    'android': ('android',),
    'ios': ('ios',),
    'flutter': ('flutter',),
    'react native': ('react native',),
    'xamarin': ('xamarin',),
    'selenium': ('selenium',),
    'cypress': ('cypress',),
    'playwright': ('playwright',),
    'jest': ('jest',),
    'pytest': ('pytest',),
    'junit': ('junit',),
    'postman': ('postman',),
    'test automation': ('test automation', 'automation testing'),
    'manual testing': ('manual testing',),
    'figma': ('figma',),
    'sketch': ('sketch',),
    'adobe xd': ('adobe xd',),
    'photoshop': ('photoshop',),
    'illustrator': ('illustrator',),
    'wireframe': ('wireframe', 'wireframing'),
    'prototyping': ('prototyping', 'prototype'),
    'user research': ('user research', 'usability testing'),
    'design system': ('design system',),
    'ux': ('ux',),
    'seo': ('seo',),
    'sem': ('sem',),
    'google analytics': ('google analytics',),
    'google ads': ('google ads', 'adwords'),
    'social media': ('social media',),
    'content marketing': ('content marketing',),
    'email marketing': ('email marketing',),
    'brand': ('brand', 'branding'),
    'hubspot': ('hubspot',),
    'copywriting': ('copywriting',),
    'salesforce': ('salesforce',),
    'crm': ('crm',),
    'sales pipeline': ('sales pipeline',),
    'quota': ('quota',),
    'cold calling': ('cold calling', 'cold call'),
    'lead generation': ('lead generation',),
    'negotiation': ('negotiation', 'negotiating'),
    'roadmap': ('roadmap',),
    'stakeholder': ('stakeholder', 'stakeholders'),
    'agile': ('agile',),
    'scrum': ('scrum',),
    'kanban': ('kanban',),
    'jira': ('jira',),
    'okr': ('okr', 'okrs'),
    'pmp': ('pmp',),
    'risk management': ('risk management',),
    'accounting': ('accounting',),
    'bookkeeping': ('bookkeeping',),
    'quickbooks': ('quickbooks',),
    'financial reporting': ('financial reporting',),
    'budgeting': ('budgeting', 'budget'),
    'audit': ('audit', 'auditing'),
    'taxation': ('taxation', 'tax'),
    'gst': ('gst',),
    'tally': ('tally',),
    'sap': ('sap',),
    'customer service': ('customer service',),
    'helpdesk': ('helpdesk', 'help desk'),
    'zendesk': ('zendesk',),
    'call center': ('call center', 'call centre'),
    'ticketing': ('ticketing',),
    'recruitment': ('recruitment', 'recruiting'),
    'talent acquisition': ('talent acquisition',),
    'onboarding': ('onboarding',),
    'payroll': ('payroll',),
    'employee engagement': ('employee engagement',),
    'patient care': ('patient care',),
    'nursing': ('nursing', 'nurse'),
    'clinical': ('clinical',),
    'ehr': ('ehr', 'electronic health records'),
    'teaching': ('teaching', 'taught'),
    'curriculum': ('curriculum',),
    'lesson planning': ('lesson planning',),
    'classroom': ('classroom',),
    'autocad': ('autocad',),
    'revit': ('revit',),
    'solidworks': ('solidworks',),
    'catia': ('catia',),
    'ansys': ('ansys',),
    'plc': ('plc',),
    'manufacturing': ('manufacturing',),
    'six sigma': ('six sigma',),
    'technical writing': ('technical writing',),
    'editing': ('editing', 'proofreading'),
    'leadership': ('leadership',),
    'mentoring': ('mentoring', 'mentorship'),
}

# Role -> the skills (and their weight) that point at it. Ordered by specificity,
# so the first profile to reach the top score wins a tie.
RESUME_ROLE_PROFILES = (
    ('Machine Learning Engineer', {'machine learning': 4, 'deep learning': 4, 'tensorflow': 3, 'pytorch': 3, 'nlp': 2, 'computer vision': 3, 'scikit-learn': 3}),
    ('Data Scientist', {'data science': 4, 'pandas': 3, 'numpy': 2, 'scikit-learn': 2, 'statistics': 3, 'a/b testing': 2, 'python': 1}),
    ('Data Analyst', {'sql': 3, 'excel': 3, 'tableau': 3, 'power bi': 3, 'a/b testing': 2, 'etl': 2, 'pandas': 1, 'statistics': 1}),
    ('Frontend Developer', {'react': 4, 'angular': 4, 'vue': 4, 'svelte': 4, 'javascript': 3, 'typescript': 3, 'html': 2, 'css': 2, 'next.js': 3, 'tailwind': 2, 'redux': 2}),
    ('Backend Developer', {'django': 4, 'flask': 4, 'fastapi': 4, 'spring': 4, 'express': 4, 'node.js': 4, 'laravel': 4, 'php': 3, '.net': 4, 'java': 3, 'c#': 3, 'golang': 2, 'rust': 2, 'graphql': 2, 'rest api': 3, 'microservices': 3}),
    ('Mobile Developer', {'android': 4, 'ios': 4, 'flutter': 4, 'react native': 4, 'swift': 3, 'kotlin': 3, 'xamarin': 3}),
    ('DevOps Engineer', {'docker': 3, 'kubernetes': 4, 'terraform': 4, 'ansible': 3, 'jenkins': 3, 'ci/cd': 4, 'aws': 2, 'azure': 2, 'gcp': 2, 'linux': 2}),
    ('QA / Test Engineer', {'selenium': 4, 'cypress': 4, 'playwright': 3, 'jest': 2, 'pytest': 3, 'junit': 3, 'postman': 2, 'test automation': 4, 'manual testing': 3}),
    ('UI/UX Designer', {'figma': 4, 'sketch': 3, 'adobe xd': 4, 'wireframe': 4, 'user research': 4, 'prototyping': 3, 'design system': 3, 'ux': 3, 'photoshop': 1, 'illustrator': 1}),
    ('Product Manager', {'roadmap': 3, 'stakeholder': 3, 'okr': 3, 'agile': 2, 'scrum': 2, 'jira': 2, 'a/b testing': 1}),
    ('Project Manager', {'pmp': 4, 'risk management': 3, 'stakeholder': 2, 'agile': 2, 'scrum': 2, 'jira': 2, 'budgeting': 1}),
    ('Marketing Manager', {'seo': 3, 'sem': 3, 'google analytics': 3, 'google ads': 3, 'social media': 2, 'content marketing': 3, 'email marketing': 3, 'brand': 2, 'hubspot': 2, 'copywriting': 2}),
    ('Sales Executive', {'salesforce': 4, 'crm': 2, 'sales pipeline': 3, 'quota': 4, 'cold calling': 3, 'lead generation': 3, 'negotiation': 2}),
    ('Accountant', {'accounting': 4, 'bookkeeping': 4, 'quickbooks': 4, 'financial reporting': 4, 'audit': 3, 'taxation': 4, 'gst': 3, 'tally': 3, 'sap': 1}),
    ('Customer Support Specialist', {'customer service': 4, 'helpdesk': 3, 'zendesk': 4, 'call center': 4, 'ticketing': 2}),
    ('HR / Recruiter', {'recruitment': 4, 'talent acquisition': 4, 'onboarding': 3, 'payroll': 3, 'employee engagement': 3}),
    ('Nurse', {'patient care': 4, 'nursing': 4, 'clinical': 4, 'ehr': 2}),
    ('Teacher', {'teaching': 4, 'curriculum': 4, 'lesson planning': 4, 'classroom': 4}),
    ('Mechanical Engineer', {'solidworks': 4, 'catia': 4, 'ansys': 4, 'manufacturing': 3, 'six sigma': 2, 'autocad': 1}),
    ('Civil Engineer', {'revit': 4, 'autocad': 3, 'plc': 2, 'manufacturing': 1}),
    ('Content Writer', {'technical writing': 4, 'copywriting': 3, 'editing': 3, 'content marketing': 2}),
)

# Words that appear in a job title line, used to spot the resume's own title.
_RESUME_TITLE_HINTS = (
    'engineer', 'developer', 'manager', 'analyst', 'scientist', 'designer',
    'consultant', 'specialist', 'administrator', 'architect', 'accountant',
    'recruiter', 'nurse', 'teacher', 'writer', 'executive', 'technician',
    'supervisor', 'coordinator', 'intern', 'lead',
)

# Whole words, matched on boundaries: 'managed' must not fire on 'management'.
_RESUME_ACTION_VERBS = (
    'built', 'build', 'developed', 'designed', 'created', 'led', 'managed',
    'managing', 'launched', 'implemented', 'improved', 'increased', 'reduced',
    'automated', 'migrated', 'delivered', 'shipped', 'optimised', 'optimized',
    'architected', 'owned', 'drove', 'spearheaded', 'negotiated', 'analysed',
    'analyzed', 'achieved', 'streamlined', 'introduced', 'scaled', 'grew', 'won',
    'trained', 'mentored', 'taught', 'produced', 'established', 'refactored',
    'wrote', 'ran', 'maintained', 'coordinated', 'presented', 'completed',
    'earned', 'secured',
)

_RESUME_STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'into', 'over', 'then',
    'than', 'while', 'which', 'who', 'what', 'when', 'where', 'how', 'also',
    'more', 'most', 'other', 'such', 'can', 'could', 'would', 'should', 'may',
    'might', 'will', 'was', 'were', 'are', 'been', 'their', 'its', 'our', 'you',
    'your', 'his', 'her', 'they', 'them', 'use', 'used', 'using', 'new', 'all',
    'across', 'worked', 'working', 'team', 'teams', 'project', 'projects',
    'year', 'years', 'company', 'role', 'resume', 'about', 'within', 'each',
}

# A genuinely specific technical question for the skills we see most often.
_RESUME_SKILL_QUESTIONS = {
    'react': 'How do you decide between local state, lifted state and context in a React app, and what would make you reach for an external store?',
    'javascript': 'Which JavaScript behaviours still surprise you in production, and how do you guard against them?',
    'typescript': 'How do you use TypeScript to keep an API boundary safe without drowning the codebase in types?',
    'node.js': 'How do you keep a Node.js service responsive under load, and where does the event loop bite you?',
    'django': 'How do you structure a Django project once it outgrows a single app?',
    'flask': 'How do you organise a Flask codebase once it grows past a handful of routes?',
    'sql': 'Walk me through how you would diagnose and fix a slow SQL query in production.',
    'postgresql': 'Which PostgreSQL features have you relied on beyond simple CRUD, and why?',
    'mongodb': 'When would you model data in MongoDB rather than in a relational database?',
    'aws': 'Which AWS services have you run in production, and how do you keep cost and reliability under control?',
    'docker': 'How do you keep container images small, reproducible and secure?',
    'kubernetes': 'How do you roll out and roll back a Kubernetes workload safely?',
    'ci/cd': 'What does a good CI/CD pipeline look like for the team you work in?',
    'machine learning': 'How do you decide a model is good enough to ship, beyond its headline accuracy score?',
    'deep learning': 'How do you keep a deep network from overfitting, and how do you know the evaluation is trustworthy?',
    'tensorflow': 'How do you move a TensorFlow model from a notebook into production, and what breaks on the way?',
    'pytorch': 'How do you structure a PyTorch training loop so it is reproducible and debuggable?',
    'pandas': 'What are the practical limits of pandas, and what do you do when the data exceeds them?',
    'nlp': 'What are the hard parts of shipping an NLP feature to real users?',
    'figma': 'How do you keep a Figma file usable once several designers and developers are working in it?',
    'user research': 'How do you turn user research into decisions the team actually acts on?',
    'seo': 'How do you prioritise SEO work when technical and content levers pull in different directions?',
    'google analytics': 'How do you make sure the metrics you report actually reflect business outcomes?',
    'salesforce': 'How do you keep a Salesforce pipeline accurate enough to forecast from?',
    'excel': 'What have you automated in Excel, and at what point do you move that work somewhere else?',
    'tableau': 'How do you design a dashboard that people actually use?',
    'agile': 'How do you keep an agile process honest rather than performative?',
    'stakeholder': 'How do you handle a stakeholder whose priorities conflict with the roadmap?',
    'patient care': 'How do you prioritise when several patients need you at the same time?',
    'teaching': 'How do you adapt a lesson when a class is clearly not following it?',
    'customer service': 'How do you handle an angry customer who is also factually wrong?',
    'accounting': 'How do you keep month-end close accurate when the deadline is tight?',
    'autocad': 'How do you manage revisions so the drawing set people build from is always the right version?',
    'solidworks': 'How do you validate a SolidWorks design before committing to a prototype?',
}

# Behavioural prompts that stay honest when the resume is thin.
_RESUME_BEHAVIOURAL_QUESTIONS = (
    'Walk me through the piece of work on your resume you are proudest of. What was your specific contribution, and what was the measurable outcome?',
    'Tell me about a decision you owned that turned out to be wrong. How did you find out, and what did you change?',
    'Describe a time you had to influence someone without any authority over them. How did you approach it?',
    'Tell me about a time you had to deliver against a deadline that was too tight. What did you change about how you worked?',
    'Which part of your resume best shows you are ready for this role, and what would you need to learn first?',
)


def _normalise_resume(text):
    """Lowercase and collapse punctuation, keeping + and # so c++/c# survive."""
    cleaned = re.sub(r'[^a-z0-9+#\s]', ' ', text.lower())
    return re.sub(r'\s+', ' ', cleaned).strip()


def _phrase_position(normalised, phrase):
    """Where a phrase first appears, or None.

    Matching is anchored on word boundaries, which is the whole point: the old
    substring test matched 'ui' inside 'built' and 'ai' inside 'paid', so nearly
    every resume came out with the same invented skills.
    """
    parts = [re.escape(part) for part in phrase.split()]
    if not parts:
        return None
    pattern = r'(?<![a-z0-9+#])' + r'\s+'.join(parts) + r'(?![a-z0-9+#])'
    match = re.search(pattern, normalised)
    return match.start() if match else None


def _phrase_in(normalised, phrase):
    return _phrase_position(normalised, phrase) is not None


def _extract_resume_skills(normalised):
    """Canonical skills genuinely present, most relevant first.

    Ordering by first appearance uses the resume's own structure: a resume
    normally puts its headline skills near the top, so the strongest matches
    lead the list.
    """
    found = []
    for canonical, aliases in RESUME_SKILLS.items():
        positions = [p for p in (_phrase_position(normalised, a) for a in aliases) if p is not None]
        if positions:
            found.append((min(positions), canonical))
    found.sort(key=lambda item: (item[0], item[1]))
    return [canonical for _, canonical in found]


def _resume_job_title(resume_text):
    """The role the resume itself claims, from the line under the candidate's name."""
    for raw in resume_text.splitlines()[:12]:
        line = raw.strip().strip('-*|•, ').strip()
        if not line or len(line) > 60 or ':' in line or '@' in line:
            continue
        if not any(hint in line.lower() for hint in _RESUME_TITLE_HINTS):
            continue
        # "Senior Data Scientist, Acme Analytics (2021-present)" -> "Senior Data Scientist"
        line = re.split(r'[|,(]', line)[0].strip()
        line = re.sub(r'\s{2,}', ' ', line)
        if 2 <= len(line) <= 60:
            return line
    return ''


def _detect_resume_role(resume_text, skills):
    """Pick the role from the resume's own title, else from weighted skill scores."""
    titled = _resume_job_title(resume_text)
    if titled:
        return titled

    best_role, best_score = 'Professional', 0
    for role, weights in RESUME_ROLE_PROFILES:
        score = sum(weight for skill, weight in weights.items() if skill in skills)
        if score > best_score:
            best_role, best_score = role, score

    if not best_score:
        return 'Professional'

    frontend = dict(RESUME_ROLE_PROFILES)['Frontend Developer']
    backend = dict(RESUME_ROLE_PROFILES)['Backend Developer']
    frontend_score = sum(w for s, w in frontend.items() if s in skills)
    backend_score = sum(w for s, w in backend.items() if s in skills)
    if frontend_score and backend_score and abs(frontend_score - backend_score) <= 3 and best_role in ('Frontend Developer', 'Backend Developer'):
        return 'Full Stack Developer'
    return best_role


def _extract_resume_highlights(resume_text, limit=4):
    """Real achievement lines from the resume - the raw material for questions."""
    highlights = []
    for raw in resume_text.splitlines():
        line = raw.strip().lstrip('-•*·◦‣> ').strip()
        if not 25 <= len(line) <= 200:
            continue
        lowered = line.lower()
        has_metric = re.search(
            r'\d+\s*(?:%|percent|users|customers|clients|rows|records|people|students|patients|hours|days|weeks|months)|\$[\d,]+',
            lowered,
        )
        # A comma-heavy line without a number is a skills list, not an achievement.
        if not has_metric and line.count(',') >= 3:
            continue
        normalised_line = _normalise_resume(line)
        has_verb = any(_phrase_in(normalised_line, verb) for verb in _RESUME_ACTION_VERBS)
        if has_verb or has_metric:
            if line not in highlights:
                highlights.append(line)
        if len(highlights) >= limit:
            break
    return highlights


def _resume_keywords_from(text, limit=6):
    """Distinctive words from a resume line, used as answer-scoring keywords."""
    words, seen = [], set()
    for token in re.findall(r'[A-Za-z][A-Za-z+#.\-]{2,}', text):
        cleaned = token.strip('.-').lower()
        if cleaned in _RESUME_STOPWORDS or cleaned in seen:
            continue
        seen.add(cleaned)
        words.append(token.strip('.-'))
        if len(words) >= limit:
            break
    return ', '.join(words)


def _resume_quote_question(role, line):
    return {
        'role': role,
        'question_text': (
            'Your resume says: "' + line + '". Walk me through how you approached it - the '
            'decisions you made, the trade-offs, and what you would do differently now.'
        ),
        'category': 'Behavioral',
        'optimal_keywords': _resume_keywords_from(line),
        'expected_concepts': 'Ownership of the work, specific decisions, measurable outcome',
        'difficulty': 'Medium',
    }


def _resume_skill_question(role, skill):
    prompt = _RESUME_SKILL_QUESTIONS.get(skill)
    if not prompt:
        prompt = (
            'Your resume lists ' + skill + '. Walk me through how you have applied it on real '
            'work, and the trade-offs you had to make.'
        )
    return {
        'role': role,
        'question_text': prompt,
        'category': 'Technical',
        'optimal_keywords': skill + ', trade-offs, decisions, production, results',
        'expected_concepts': 'Depth in ' + skill + ', engineering judgement, real-world constraints',
        'difficulty': 'Medium',
    }


def _interleave(groups):
    """Round-robin the groups so a short list still gets a mix of question types."""
    merged, depth = [], 0
    while any(depth < len(group) for group in groups):
        for group in groups:
            if depth < len(group):
                merged.append(group[depth])
        depth += 1
    return merged


def _build_resume_questions(role, skills, highlights, count):
    quote_questions = [_resume_quote_question(role, line) for line in highlights[:2]]
    # Lead the technical slots with skills we have a genuinely specific question
    # for, so "data science" never crowds out TensorFlow. sorted() is stable, so
    # the resume's own ordering still decides within each group.
    ranked_skills = sorted(skills[:6], key=lambda skill: 0 if skill in _RESUME_SKILL_QUESTIONS else 1)
    skill_questions = [_resume_skill_question(role, skill) for skill in ranked_skills[:3]]
    behaviour_questions = [
        {
            'role': role,
            'question_text': prompt,
            'category': 'Behavioral',
            'optimal_keywords': 'situation, action, result, impact',
            'expected_concepts': 'Structured story, personal contribution, measurable result',
            'difficulty': 'Medium',
        }
        for prompt in _RESUME_BEHAVIOURAL_QUESTIONS
    ]

    pool = _interleave((quote_questions, skill_questions, behaviour_questions))

    # Two resumes can surface the same prompt (e.g. no recognised skills), so
    # de-duplicate before trimming to the requested count.
    unique, seen = [], set()
    for question in pool:
        if question['question_text'] in seen:
            continue
        seen.add(question['question_text'])
        unique.append(question)

    selected = unique[:count]
    for index, question in enumerate(selected):
        question['id'] = -(1001 + index)
    return selected


def analyze_resume_locally(resume_text, question_count=3):
    """Offline resume analysis: real skills, a real role, questions from the resume."""
    try:
        count = int(question_count or 3)
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(count, 10))

    normalised = _normalise_resume(resume_text)
    skills = _extract_resume_skills(normalised)
    role = _detect_resume_role(resume_text, skills)
    highlights = _extract_resume_highlights(resume_text)
    questions = _build_resume_questions(role, skills, highlights, count)

    if skills:
        summary = 'Resume reads as a ' + role + ', listing ' + ', '.join(skills[:6]) + ' among its skills.'
    else:
        summary = (
            'Resume reads as a ' + role + '. No specific tools or technologies were recognised, '
            'so the questions focus on the experience described.'
        )
    if highlights:
        summary += ' The questions are drawn from the achievements written on the resume.'

    return {
        'summary': summary,
        'detected_role': role,
        'skills': skills[:15],
        'highlights': highlights,
        'questions': questions,
    }


def ask_resume_question(question, resume_text):
    """
    Answers questions about a resume using AI or rule-based fallback.
    """
    if GEMINI_API_KEY:
        try:
            return ask_resume_question_with_gemini(question, resume_text)
        except Exception as e:
            print(f"Gemini Q&A failed: {e}. Using local fallback.")
            return ask_resume_question_locally(question, resume_text)
    else:
        return ask_resume_question_locally(question, resume_text)


def ask_resume_question_with_gemini(question, resume_text):
    if genai is None:
        raise ImportError("google-generativeai not available")
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    You are an expert AI interview coach. A candidate has asked a question about their resume.
    Answer the question based on the resume content provided.
    
    Resume Content:
    {resume_text[:2000]}
    
    Candidate's Question: {question}
    
    Provide a helpful, concise, and professional answer. If the question cannot be answered from the resume,
    indicate what additional information would be helpful. Keep your response under 150 words.
    Output raw text only, no markdown formatting.
    """
    
    response = model.generate_content(prompt)
    return { "answer": response.text.strip() }


def _resume_matching_lines(resume_text, needles, limit=3):
    """Resume lines that mention any of the given words."""
    hits = []
    for raw in resume_text.splitlines():
        line = raw.strip()
        # Skip fragments and section headers such as "EXPERIENCE".
        if len(line) < 8 or (len(line) < 20 and line.isupper()):
            continue
        lowered = line.lower()
        if any(needle in lowered for needle in needles):
            if line not in hits:
                hits.append(line)
        if len(hits) >= limit:
            break
    return hits


def ask_resume_question_locally(question, resume_text):
    """Answer a question about the resume using the resume's own content.

    The previous version replied with canned advice such as "your resume mentions
    project experience" without reading the resume at all, which is what made the
    in-interview Q&A feel fake.
    """
    normalised = _normalise_resume(resume_text)
    skills = _extract_resume_skills(normalised)
    question_lower = question.lower()

    if any(word in question_lower for word in ('year', 'how long', 'seniority')):
        match = re.search(r'(\d+)\s*\+?\s*(?:years?|yrs?)', resume_text, re.IGNORECASE)
        if match:
            return {'answer': 'Your resume states about ' + match.group(1) + ' years of experience. Lead with your most recent and most relevant work.'}
        return {'answer': 'Your resume does not state a total number of years. A one-line summary with your total experience would help.'}

    # Naming a technology the resume never mentions deserves an honest "not found"
    # rather than a generic list of skills.
    asked = [
        canonical for canonical, aliases in RESUME_SKILLS.items()
        if any(_phrase_in(_normalise_resume(question), alias) for alias in aliases)
    ]
    if asked and not any(skill in skills for skill in asked):
        return {'answer': 'I could not find ' + ', '.join(asked[:3]) + ' anywhere in your resume. What it does show is ' + (', '.join(skills[:5]) if skills else 'no recognised tools or technologies') + '.'}

    if any(word in question_lower for word in ('skill', 'experience', 'background', 'qualif', 'strength', 'technolog', 'stack')):
        if skills:
            return {'answer': 'Your resume names these skills: ' + ', '.join(skills[:8]) + '. Pick the two or three most relevant to the role and have a concrete example ready for each.'}
        return {'answer': 'Your resume does not name specific tools or technologies yet. A short skills section would make it much easier to match you to a role.'}

    if any(word in question_lower for word in ('project', 'achievement', 'accomplish', 'impact', 'result')):
        highlights = _extract_resume_highlights(resume_text, limit=3)
        if highlights:
            quoted = ' | '.join('"' + line + '"' for line in highlights)
            return {'answer': 'Your resume lists these achievements: ' + quoted + ' Pick the one with the clearest measurable outcome and tell it with the STAR method (situation, task, action, result).'}
        return {'answer': 'Your resume does not list measurable projects or results yet. Adding one or two with numbers would give you far more to talk about.'}

    if any(word in question_lower for word in ('education', 'degree', 'university', 'college', 'study', 'qualification')):
        lines = _resume_matching_lines(resume_text, ('university', 'college', 'degree', 'bachelor', 'master', 'b.tech', 'b.e', 'mba', 'phd', 'diploma', 'school'))
        if lines:
            return {'answer': 'From your resume: ' + ' | '.join(lines[:2]) + ' Connect that directly to the role you are targeting.'}
        return {'answer': 'I could not find education details in your resume. Add your degree, institution and year if it strengthens your case.'}

    # Otherwise answer from the resume lines that actually mention what was asked.
    generic = {
        'have', 'has', 'does', 'did', 'work', 'working', 'experience', 'skill',
        'skills', 'resume', 'project', 'projects', 'tell', 'about', 'what', 'which',
        'many', 'much', 'years', 'year', 'role', 'this', 'that', 'with', 'from',
        'your', 'mine', 'list', 'show', 'give',
    }
    topic_words = [
        word for word in re.findall(r'[a-z][a-z+#.]{2,}', question_lower)
        if word not in _RESUME_STOPWORDS and word not in generic
    ]
    lines = _resume_matching_lines(resume_text, topic_words) if topic_words else []
    if lines:
        return {'answer': 'Here is what your resume says that is relevant: ' + ' | '.join(lines[:3]) + ' Build your answer around that.'}

    if skills:
        return {'answer': 'I could not find that in your resume. What it does show is ' + ', '.join(skills[:5]) + ' - answer from that angle.'}
    return {'answer': 'I could not find that in your resume. Lead with specific, measurable examples from your experience.'}


def analyze_locally(question_text, category, optimal_keywords, expected_concepts, transcript):
    """
    Rule-based local NLP grading logic. Evaluates filler words, length, keyword matching, and readability.
    """
    words = transcript.lower().split()
    word_count = len(words)
    
    # 1. Count filler words
    filler_words = ["um", "uh", "like", "actually", "basically", "so", "you know", "sort of", "stuff"]
    filler_count = 0
    # Simple check for phrases and standalone words
    transcript_lower = transcript.lower()
    for filler in filler_words:
        # Match word boundaries
        matches = re.findall(rf"\b{re.escape(filler)}\b", transcript_lower)
        filler_count += len(matches)
    
    # 2. Check keywords match
    keyword_list = [k.strip().lower() for k in optimal_keywords.split(",")] if optimal_keywords else []
    matches_found = []
    for kw in keyword_list:
        if re.search(rf"\b{re.escape(kw)}\b", transcript_lower):
            matches_found.append(kw)
            
    keyword_match_ratio = len(matches_found) / len(keyword_list) if keyword_list else 1.0
    
    # 3. Calculate scores
    # Relevance Score: based on keyword matching
    relevance = int(40 + (keyword_match_ratio * 60))
    if word_count < 15:
        relevance = max(10, relevance - 30)

    # Clarity Score: based on length stability and filler word frequency relative to total words
    filler_ratio = filler_count / max(1, word_count)
    filler_penalty = min(40, int(filler_ratio * 150))
    
    if word_count < 25:
        clarity = 50 - filler_penalty
    elif word_count > 250:
        clarity = 85 - filler_penalty  # Slight penalty for rambling
    else:
        clarity = 95 - filler_penalty
    clarity = max(20, min(100, clarity))

    # Grammar & Vocabulary Score: base check for sentences, length, filler words
    grammar = max(30, min(100, 95 - filler_penalty - (5 if word_count < 15 else 0)))

    # Overall Score: weighted average
    if category == "Technical":
        score = int((relevance * 0.5) + (clarity * 0.3) + (grammar * 0.2))
    else:
        score = int((relevance * 0.3) + (clarity * 0.4) + (grammar * 0.3))

    # Cap scores
    score = max(0, min(100, score))
    relevance = max(0, min(100, relevance))
    clarity = max(0, min(100, clarity))
    grammar = max(0, min(100, grammar))

    # 4. Generate Strengths, Weaknesses, Tips dynamically
    strengths = []
    weaknesses = []
    tips = []

    # Strengths
    if word_count >= 40:
        strengths.append("Provided a detailed explanation with good response length.")
    else:
        strengths.append("Answer was concise and direct.")
        
    if len(matches_found) >= 2:
        strengths.append(f"Successfully integrated key terminology: {', '.join(matches_found[:3])}.")
    else:
        strengths.append("Presented structured layout flow.")

    if filler_count <= 2:
        strengths.append("Spoke fluently with minimal crutch words.")

    # Weaknesses
    if word_count < 30:
        weaknesses.append("Response was too brief, missing opportunities to elaborate on details.")
    elif word_count > 250:
        weaknesses.append("Rambled slightly, which reduced the impact and conciseness of the main point.")

    if len(matches_found) < len(keyword_list) / 2:
        missed = [k for k in keyword_list if k not in matches_found]
        if missed:
            weaknesses.append(f"Missed addressing core concepts like: {', '.join(missed[:2])}.")

    if filler_count > 5:
        weaknesses.append(f"Used a high volume of crutch words ({filler_count} detected), making the delivery sound hesitant.")

    # Ensure we always have at least one weakness/strength
    if not weaknesses:
        weaknesses.append("Minor lack of specific metrics or quantitative details in the response.")
        
    # Tips
    if category == "Behavioral":
        tips.append("Use the STAR method: describe the Situation, Task, Action you took, and final quantitative Result.")
        tips.append("Focus more on your individual contributions using 'I' statements rather than general 'we' statements.")
    else:
        tips.append("Whenever describing technical terms, explicitly state the engineering tradeoffs (pros/cons) of your approach.")
        tips.append("Use a real-world project example to illustrate this concept in action.")

    if filler_count > 3:
        tips.append("Practice pausing silently instead of using verbal filler words when organizing your next sentence.")

    return {
        "score": score,
        "clarity": clarity,
        "grammar": grammar,
        "relevance": relevance,
        "filler_count": filler_count,
        "strengths": strengths[:3],
        "weaknesses": weaknesses[:2],
        "tips": tips[:3]
    }