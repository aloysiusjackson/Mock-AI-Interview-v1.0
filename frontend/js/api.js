const API_BASE_URL = window.location.hostname.includes('github.io')
    ? 'https://mock-ai-interview-backend.onrender.com/api'  // Static hosting: point at the Render backend
    : '/api';  // Same origin — Flask serves the frontend and the API together,
               // so no need to hardcode localhost (which breaks on 127.0.0.1 and deploys).

const api = {
    async getRoles() {
        try {
            const res = await fetch(`${API_BASE_URL}/roles`);
            if (!res.ok) throw new Error('Failed to fetch roles');
            return await res.json();
        } catch (error) {
            console.error('Error fetching roles:', error);
            return { roles: ['Software Engineer', 'Product Manager', 'Data Analyst'] }; // Fallback
        }
    },

    async updateProfile(profile) {
        try {
            const res = await fetch(`${API_BASE_URL}/auth/update-profile`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(profile)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'Failed to update your profile');
            return data;
        } catch (error) {
            console.error('Error updating profile:', error);
            throw error;
        }
    },

    async getQuestions(role) {
        try {
            const res = await fetch(`${API_BASE_URL}/questions?role=${encodeURIComponent(role)}`, { credentials: 'include' });
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                const err = new Error(errData.error || 'Failed to fetch questions');
                err.interviewBlocked = !!errData.interview_blocked;
                throw err;
            }
            return await res.json();
        } catch (error) {
            console.error('Error fetching questions:', error);
            throw error;
        }
    },

    async getAntiCheatStatus() {
        try {
            const res = await fetch(`${API_BASE_URL}/anti-cheat/status`, { credentials: 'include' });
            if (!res.ok) return { cheat_strikes: 0, interview_blocked: false, max_strikes: 3 };
            return await res.json();
        } catch (error) {
            return { cheat_strikes: 0, interview_blocked: false, max_strikes: 3 };
        }
    },

    async submitInterview(role, answers) {
        try {
            const res = await fetch(`${API_BASE_URL}/submit-interview`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ role, answers })
            });
            if (!res.ok) throw new Error('Failed to submit interview responses');
            return await res.json();
        } catch (error) {
            console.error('Error submitting interview responses:', error);
            throw error;
        }
    },

    async getDashboard() {
        try {
            // The server resolves the account from the session cookie. The old
            // `?user_id=` parameter is deliberately ignored on the backend (it
            // let anyone read someone else's history by changing a number), so
            // sending it only misleads the next reader.
            const res = await fetch(`${API_BASE_URL}/dashboard`);
            if (!res.ok) throw new Error('Failed to load dashboard metrics');
            return await res.json();
        } catch (error) {
            console.error('Error loading dashboard metrics:', error);
            throw error;
        }
    },

    async getInterviewDetail(id) {
        try {
            const res = await fetch(`${API_BASE_URL}/interview/${id}`);
            if (!res.ok) throw new Error(`Failed to load interview ${id} details`);
            return await res.json();
        } catch (error) {
            console.error(`Error loading interview ${id} details:`, error);
            throw error;
        }
    },

    async autoGrade(data) {
        try {
            const res = await fetch(`${API_BASE_URL}/auto-grade`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            });
            if (!res.ok) throw new Error('Failed to auto-grade response');
            return await res.json();
        } catch (error) {
            console.error('Error auto-grading response:', error);
            throw error;
        }
    },

    async analyzeResume(file, questionCount = 3) {
        try {
            const formData = new FormData();
            formData.append('resume', file);
            formData.append('question_count', questionCount);
            
            const res = await fetch(`${API_BASE_URL}/analyze-resume`, {
                method: 'POST',
                body: formData
            });
            if (!res.ok) throw new Error('Failed to analyze resume');
            return await res.json();
        } catch (error) {
            console.error('Error analyzing resume:', error);
            throw error;
        }
    },

    async askResumeQuestion(question, resumeText) {
        try {
            const res = await fetch(`${API_BASE_URL}/ask-resume-question`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ question, resume_text: resumeText })
            });
            if (!res.ok) throw new Error('Failed to get answer');
            return await res.json();
        } catch (error) {
            console.error('Error asking resume question:', error);
            throw error;
        }
    }
};