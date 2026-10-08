/**
 * Authentication System — Login, Signup, Email Verification, Google OAuth
 */

const API = window.location.origin + '/api';

// ===================== INIT =====================
document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    setupForms();
    setupGoogleLogin();
    checkExistingSession();
});

// ===================== TAB SWITCHING =====================
function setupTabs() {
    const loginTab = document.getElementById('login-tab');
    const signupTab = document.getElementById('signup-tab');
    const loginForm = document.getElementById('login-form');
    const signupForm = document.getElementById('signup-form');
    const switchToSignup = document.getElementById('switch-to-signup');
    const switchToLogin = document.getElementById('switch-to-login');

    function showTab(tab) {
        loginTab.classList.toggle('active', tab === 'login');
        signupTab.classList.toggle('active', tab === 'signup');
        loginForm.classList.toggle('active', tab === 'login');
        signupForm.classList.toggle('active', tab === 'signup');
    }

    loginTab.addEventListener('click', () => showTab('login'));
    signupTab.addEventListener('click', () => showTab('signup'));
    switchToSignup.addEventListener('click', (e) => { e.preventDefault(); showTab('signup'); });
    switchToLogin.addEventListener('click', (e) => { e.preventDefault(); showTab('login'); });

    document.getElementById('back-to-login').addEventListener('click', (e) => {
        e.preventDefault();
        document.getElementById('verify-notice').classList.remove('active');
        showTab('login');
    });
}

// ===================== FORM HANDLERS =====================
function setupForms() {
    // Login form
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('login-email').value.trim();
        const password = document.getElementById('login-password').value;
        const btn = e.target.querySelector('button[type="submit"]');

        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Logging in...';

        try {
            const res = await fetch(`${API}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                showToast('Login successful! Welcome back 👋', 'success');
                localStorage.setItem('user', JSON.stringify(data.user));
                setTimeout(() => { window.location.href = '/'; }, 800);
            } else if (data.needs_verification) {
                document.getElementById('verify-email-text').innerHTML =
                    `We sent a verification link to <strong>${email}</strong>. Click the link in your email to activate your account.`;
                showVerifyNotice();
                showToast('Please verify your email first', 'info');
            } else {
                showToast(data.error || 'Invalid email or password', 'error');
            }
        } catch (err) {
            showToast('Connection error. Please try again.', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Log In';
        }
    });

    // Signup form
    document.getElementById('signup-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('signup-name').value.trim();
        const email = document.getElementById('signup-email').value.trim();
        const password = document.getElementById('signup-password').value;
        const confirm = document.getElementById('signup-confirm').value;
        const btn = e.target.querySelector('button[type="submit"]');

        if (password !== confirm) {
            showToast('Passwords do not match', 'error');
            return;
        }

        if (password.length < 8) {
            showToast('Password must be at least 8 characters', 'error');
            return;
        }

        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Creating account...';

        try {
            const res = await fetch(`${API}/auth/signup`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, email, password })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                document.getElementById('verify-email-text').innerHTML =
                    `We sent a verification link to <strong>${email}</strong>. Click the link in your email to activate your account.`;
                showVerifyNotice();
                showToast('Account created! Please check your email 📧', 'success');
            } else {
                showToast(data.error || 'Failed to create account', 'error');
            }
        } catch (err) {
            showToast('Connection error. Please try again.', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Create Account';
        }
    });
}

// ===================== GOOGLE LOGIN =====================
function setupGoogleLogin() {
    const googleLoginBtn = document.getElementById('google-login-btn');
    const googleSignupBtn = document.getElementById('google-signup-btn');

    // Simulated Google OAuth for demo (in production, use real Google OAuth)
    [googleLoginBtn, googleSignupBtn].forEach(btn => {
        if (btn) {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                btn.innerHTML = '<span class="spinner"></span> Connecting to Google...';

                try {
                    // In production, this would redirect to Google OAuth
                    // For now, simulate Google login
                    const res = await fetch(`${API}/auth/google`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            // In production, these come from Google OAuth callback
                            google_id: 'google_' + Date.now(),
                            email: 'demo@gmail.com',
                            name: 'Google User',
                            picture: ''
                        })
                    });
                    const data = await res.json();

                    if (res.ok && data.success) {
                        showToast('Logged in with Google! 🎉', 'success');
                        localStorage.setItem('user', JSON.stringify(data.user));
                        setTimeout(() => { window.location.href = '/'; }, 800);
                    } else {
                        showToast(data.error || 'Google login failed', 'error');
                    }
                } catch (err) {
                    showToast('Connection error', 'error');
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = btn === googleLoginBtn
                        ? 'Continue with Google'
                        : 'Continue with Google';
                }
            });
        }
    });
}

// ===================== SESSION CHECK =====================
function checkExistingSession() {
    const user = localStorage.getItem('user');
    if (user) {
        // Already logged in, redirect to dashboard
        window.location.href = '/';
    }
}

// ===================== HELPERS =====================
function showVerifyNotice() {
    document.getElementById('login-form').classList.remove('active');
    document.getElementById('signup-form').classList.remove('active');
    document.getElementById('verify-notice').classList.add('active');
    document.querySelector('.auth-tabs').style.display = 'none';
}

function togglePassword(inputId, btn) {
    const input = document.getElementById(inputId);
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = '🙈';
    } else {
        input.type = 'password';
        btn.textContent = '👁';
    }
}

async function resendVerification() {
    showToast('Verification email resent! 📧', 'info');
}

function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    setTimeout(() => { toast.className = 'toast'; }, 3500);
}
