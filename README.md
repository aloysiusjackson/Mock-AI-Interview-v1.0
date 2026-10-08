# Mock AI Interview Prep Platform

An interactive, high-fidelity web application built for students and job seekers to practice job interviews. Features a dark-themed glassmorphism interface, responsive dashboard visualization charts, real-time speech-to-text transcribing, and AI-driven feedback grading.

![Mock AI Interview](https://img.shields.io/badge/AI-Powered%20Interview%20Prep-blueviolet?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-ES6+-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)

## 🚀 Key Features

* **Responsive Dashboard** - Shows overall average scoring progress, total interviews completed, and crutch filler words tracking. Uses **Chart.js** to show progression line charts and subscore radar graphs.
* **Simulated Interview Room** - Complete with simulated camera overlays, active question teleprompter widgets, real-time timer readouts, and active visual voice wave pulses.
* **Built-in Speech-to-Text** - Uses the browser's native **Web Speech API** (`SpeechRecognition`) to transcribe answers in real-time, completely free of charge. Includes a manual text editor fallback.
* **Text-to-Speech Question Readout** - Reads the active interview questions out loud using the browser's synthesis engines.
* **AI Feedback Evaluator** - Grades each question response out of 100 on **Clarity**, **Grammar**, and **Relevance**. Generates bulleted lists of specific Strengths, Improvement Areas, and Actionable Tips.
* **Modular Grading Engine** - Runs immediately out of the box using a local rule-based NLP simulator. Can easily be updated to run full LLM evaluations using a Gemini API Key!
* **Resume Analysis & Q&A** - Upload PDF/DOCX/TXT resumes to generate personalized interview questions based on your experience. Ask AI questions about your resume during the interview.
* **SQLite Persistence** - Keeps record history logs of all past mock interview sessions, questions, and detail results.
* **Anti-Cheat Monitoring** - Detects when users switch tabs during interview sessions.
* **Achievement System** - Unlock milestones as you complete practice sessions.

## 🛠️ Technology Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | HTML5, Vanilla CSS3 (Custom Glassmorphism design), JavaScript (ES6+) |
| **Backend** | Python, Flask, Flask-CORS |
| **Database** | SQLite3 |
| **Charts** | Chart.js |
| **AI Integration** | Google Gemini 1.5 Flash (optional) |
| **Document Processing** | PyPDF2, python-docx |

## 📂 Project Structure

```
mock-ai-interview/
├── backend/
│   ├── app.py           # Flask API server with all endpoints
│   ├── ai_engine.py     # AI grading engine (Gemini + local fallback)
│   ├── database.py      # SQLite database initialization and seeding
│   ├── requirements.txt # Python dependencies
│   └── interview.db     # SQLite database (auto-generated)
├── frontend/
│   ├── index.html       # Main SPA entry point
│   ├── css/
│   │   └── styles.css   # Glassmorphism styles and responsive design
│   └── js/
│       ├── app.js       # Core application logic and routing
│       ├── api.js       # API communication layer
│       ├── dashboard.js # Dashboard charts and metrics
│       ├── interview.js # Interview session management
│       └── anti-cheat.js # Tab monitoring for interview integrity
├── api/
│   ├── index.py         # Serverless entrypoint — serves backend/app.py as-is
│   └── requirements.txt # Serverless dependencies
├── .env.example         # Environment configuration template
├── .gitignore           # Git ignore rules
├── run.bat             # Windows startup script
├── start.sh            # Unix/Linux startup script
├── render.yaml         # Render deployment configuration
└── vercel.json         # Vercel deployment configuration
```

## 💻 Setup & Installation

### 1. Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Modern web browser (Chrome, Edge, or Safari recommended for Speech-to-Text features)

### 2. Install Dependencies

```bash
# Navigate to the project directory
cd mock-ai-interview

# Install backend dependencies
pip install -r backend/requirements.txt
```

### 3. Initialize Database

```bash
# Initialize SQLite and seed mock questions
python backend/database.py
```

This creates `backend/mock_interview.db` with pre-seeded interview questions for:
- Software Engineer
- Product Manager
- Data Analyst
- Sales Executive
- Marketing Manager
- HR Manager
- Financial Analyst
- Operations Manager
- And more...

### 4. Run the Flask Backend

```bash
# Start the API server
python backend/app.py
```

The server will run at `http://127.0.0.1:5000`

### 5. Open the app

Go to **http://127.0.0.1:5000** (or http://localhost:5000).

Flask serves the frontend *and* the API from the same origin, which is why the
pages call `/api/...` and why no API URL has to be configured. Do not open
`frontend/index.html` as a file and do not serve `frontend/` with a plain static
server: the pages would load, but every API call would fail.

Everything the runtime needs is in this one command — local development stays on
SQLite unless you set `DATABASE_URL` (see
[PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)).

## 🔑 Optional: Enable Gemini AI Grading

By default, the application uses a rule-based NLP grading engine. To enable advanced Gemini AI evaluation:

1. Copy `.env.example` to `.env`:
   ```bash
   copy .env.example .env
   ```

2. Add your Gemini API key to `.env`:
   ```env
   GEMINI_API_KEY=your_actual_api_key_here
   ```

3. Restart the Flask server. The application will automatically detect the key and use Gemini for feedback.

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/roles` | GET | Returns available job roles |
| `/api/questions` | GET | Fetches interview questions by role |
| `/api/submit-interview` | POST | Submits completed interview for grading |
| `/api/auto-grade` | POST | Real-time answer grading |
| `/api/dashboard` | GET | User metrics and interview history |
| `/api/interview/<id>` | GET | Detailed interview results |
| `/api/analyze-resume` | POST | Analyze resume and generate questions |
| `/api/ask-resume-question` | POST | Ask questions about uploaded resume |

## 🎯 Supported Interview Roles

- Software Engineer
- Product Manager
- Data Analyst
- Sales Executive
- Marketing Manager
- HR Manager
- Financial Analyst
- Operations Manager
- Customer Support Lead
- Healthcare Administrator
- Project Manager
- Business Analyst
- Teacher/Educator
- Retail Manager
- Legal Associate
- Graphic Designer
- Content Writer

## 📊 Analytics & Tracking

The platform tracks:
- Overall interview scores and trends
- Category-specific performance (Behavioral, Technical, Situational)
- Clarity, Grammar, and Relevance subscores
- Filler word frequency
- Interview history with detailed feedback

## 🚀 Deployment

### Render Deployment

The project includes `render.yaml` for easy deployment:

1. Fork this repository
2. Create a new Web Service on Render
3. Connect your fork and deploy

### Vercel Deployment

For serverless deployment, `api/index.py` exposes the same hardened Flask app that
Render runs — it imports `backend/app.py` rather than keeping a second copy of the
API, so authentication, ownership checks, rate limits and error handling are
identical on both hosts. Set `DATABASE_URL` in the Vercel project, otherwise the
function falls back to the SQLite file that resets on every cold start.

See [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) for the production
checklist (environment variables, HTTPS/proxy settings, Supabase RLS, rollback).

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

MIT License - Feel free to use and modify for your own projects.