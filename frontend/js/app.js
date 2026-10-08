const app = {
    init() {
        this.setupTheme();
        this.setupUserSession();
        this.setupSidebarCollapse();
        this.setupNavigation();
        this.setupDashboardForm();
        this.setupAssessment();
        this.setupSettings();
        this.setupResourceButtons();
        this.setupResumeImport();
        
        // Load initial dashboard metrics
        dashboard.load();
    },

    /**
     * User Session Management
     */
    setupUserSession() {
        const user = this.getUser();
        const sidebarAuthBtns = document.getElementById('sidebar-auth-btns');
        const sidebarUsername = document.getElementById('sidebar-username');
        const sidebarRole = document.getElementById('sidebar-role');
        const avatarTag = document.getElementById('user-avatar-tag');

        if (user) {
            // User is logged in
            const name = user.name || 'User';
            const role = user.target_role || 'Software Engineer';
            const initial = name.charAt(0).toUpperCase();

            if (sidebarUsername) sidebarUsername.textContent = name;
            if (sidebarRole) sidebarRole.textContent = `Target: ${role}`;
            if (avatarTag) avatarTag.textContent = initial;

            // Also update mobile nav panel user info
            const mobileUsername = document.getElementById('mobile-username');
            const mobileUserrole = document.getElementById('mobile-userrole');
            const mobileAvatar = document.getElementById('user-avatar-tag-mobile');
            if (mobileUsername) mobileUsername.textContent = name;
            if (mobileUserrole) mobileUserrole.textContent = `Target: ${role}`;
            if (mobileAvatar) mobileAvatar.textContent = initial;
            this.syncFloatingUserIcon(name);

            const logoutBtnHTML = `
                <button class="sidebar-logout-btn" onclick="app.logout()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
                    Log Out
                </button>
            `;

            if (sidebarAuthBtns) sidebarAuthBtns.innerHTML = logoutBtnHTML;
            const mobileAuthBtns = document.getElementById('mobile-sidebar-auth-btns');
            if (mobileAuthBtns) mobileAuthBtns.innerHTML = logoutBtnHTML;

            // Redirect admins to admin panel after login
            if (user && user.role === 'admin') {
                window.location.href = '/admin.html';
                return;
            }

            // Show admin nav link for admin users (sidebar + mobile drawer)
            const userRole = user.role;
            if (userRole === 'admin') {
                const adminNavItem = document.getElementById('admin-nav-item');
                if (adminNavItem) adminNavItem.style.display = 'block';
                const adminNavItemMobile = document.getElementById('admin-nav-item-mobile');
                if (adminNavItemMobile) adminNavItemMobile.style.display = 'block';
            }

            // Show welcome popup only ONCE ever (not on refresh)
            if (!localStorage.getItem('welcome-dismissed')) {
                this.showWelcomePopup(name);
                // Flag is set when user clicks 'Let's Go' in closeWelcomePopup()
            }
        } else {
            // User is not logged in — show Guest Account
            if (sidebarUsername) sidebarUsername.textContent = 'Guest Account';
            if (sidebarRole) sidebarRole.textContent = 'Target: Software Engineer';
            if (avatarTag) avatarTag.textContent = 'G';

            const mobileUsername = document.getElementById('mobile-username');
            const mobileUserrole = document.getElementById('mobile-userrole');
            const mobileAvatar = document.getElementById('user-avatar-tag-mobile');
            if (mobileUsername) mobileUsername.textContent = 'Guest Account';
            if (mobileUserrole) mobileUserrole.textContent = 'Target: Software Engineer';
            if (mobileAvatar) mobileAvatar.textContent = 'G';
            // Redirect admins to admin panel after login
            if (user && user.role === 'admin') {
                window.location.href = '/admin.html';
                return;
            }

            this.syncFloatingUserIcon(null);

            const loginBtnHTML = `
                <a href="/login.html" class="sidebar-login-btn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
                    Log In / Sign Up
                </a>
            `;

            if (sidebarAuthBtns) sidebarAuthBtns.innerHTML = loginBtnHTML;
            const mobileAuthBtns = document.getElementById('mobile-sidebar-auth-btns');
            if (mobileAuthBtns) mobileAuthBtns.innerHTML = loginBtnHTML;
        }
    },

    /**
     * The mobile account button doubles as the user icon: signed-in visitors see
     * their own initial, guests see a generic person glyph.
     */
    syncFloatingUserIcon(name) {
        const icon = document.getElementById('floating-user-icon');
        const badge = document.getElementById('floating-user-initial');
        const btn = document.getElementById('floating-nav-btn');
        if (!icon || !badge) return;

        const initial = name ? String(name).trim().charAt(0).toUpperCase() : '';

        // Toggle the attribute rather than `.hidden`: the property is an
        // HTMLElement member and is silently ignored on SVG elements.
        if (initial) {
            icon.setAttribute('hidden', '');
            badge.removeAttribute('hidden');
        } else {
            icon.removeAttribute('hidden');
            badge.setAttribute('hidden', '');
        }
        badge.textContent = initial;

        if (btn) {
            btn.setAttribute('aria-label', initial ? `Open account menu for ${name}` : 'Open account menu');
        }
    },

    setupSidebarCollapse() {
        const btn = document.getElementById('sidebar-collapse-btn');
        const container = document.querySelector('.app-container');
        if (!btn || !container) return;

        // Tablets: default to the compact icons-only rail (user can re-expand)
        const mqTablet = window.matchMedia('(min-width: 769px) and (max-width: 1024px)');
        if (mqTablet.matches && localStorage.getItem('sidebar-collapsed') === null) {
            localStorage.setItem('sidebar-collapsed', '1');
        }

        // Restore the saved state so the choice survives refresh
        if (localStorage.getItem('sidebar-collapsed') === '1') {
            container.classList.add('sidebar-collapsed');
        }
        this.syncCollapseButton(btn, container.classList.contains('sidebar-collapsed'));

        btn.addEventListener('click', () => {
            const collapsed = container.classList.toggle('sidebar-collapsed');
            localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0');
            this.syncCollapseButton(btn, collapsed);
        });
    },

    syncCollapseButton(btn, collapsed) {
        btn.setAttribute('aria-expanded', String(!collapsed));
        btn.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    },

    // Turn off the shimmer on metric values and hide chart placeholders
    // once real data has rendered (or the fallback has).
    clearSkeletons() {
        document.querySelectorAll('.metric-val.skeleton').forEach(el => el.classList.add('is-loaded'));
        document.querySelectorAll('.chart-skeleton').forEach(el => { el.hidden = true; });
    },

    getUser() {
        try {
            const data = localStorage.getItem('user');
            return data ? JSON.parse(data) : null;
        } catch { return null; }
    },

    getUserRole() {
        const user = this.getUser();
        return user ? user.role : null;
    },

    showWelcomePopup(name) {
        const popup = document.getElementById('welcome-popup');
        const nameEl = document.getElementById('welcome-user-name');
        if (popup && nameEl) {
            nameEl.textContent = name;
            popup.classList.add('active');
        }
    },

    logout() {
        localStorage.removeItem('user');
        localStorage.removeItem('interview_current_user');
        localStorage.removeItem('welcome-dismissed');
        window.location.href = 'login.html';
    },

    /**
     * Show login prompt when user tries to access protected features
     */
    showLoginPrompt(feature) {
        // Remove existing prompt if any
        const existing = document.getElementById('login-prompt-modal');
        if (existing) existing.remove();

        const modal = document.createElement('div');
        modal.id = 'login-prompt-modal';
        modal.className = 'login-prompt-overlay';
        modal.innerHTML = `
            <div class="login-prompt-card">
                <div class="login-prompt-icon">🔐</div>
                <h3 class="login-prompt-title">Login Required</h3>
                <p class="login-prompt-text">You need to log in to ${feature}. Create a free account to track your progress and unlock all features.</p>
                <div class="login-prompt-actions">
                    <a href="/login.html" class="btn btn-primary btn-full">Log In / Sign Up</a>
                    <button class="btn btn-secondary btn-full" onclick="document.getElementById('login-prompt-modal').remove()">Maybe Later</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Close on overlay click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.remove();
        });

        // Close on Escape key
        const escHandler = (e) => {
            if (e.key === 'Escape') {
                modal.remove();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    },

    /**
     * Theme (Light/Dark) Toggle System
     */
    setupTheme() {
        const themeToggle = document.getElementById('setting-dark-mode');
        const floatingThemeBtn = document.getElementById('floating-theme-btn');
        const themeStatusText = document.getElementById('theme-status-text');

        // Load saved theme or default to dark
        const savedTheme = localStorage.getItem('app-theme') || 'dark';
        this.applyTheme(savedTheme);

        // Settings toggle handler
        if (themeToggle) {
            themeToggle.checked = savedTheme === 'dark';
            themeToggle.addEventListener('change', () => {
                const newTheme = themeToggle.checked ? 'dark' : 'light';
                this.applyTheme(newTheme);
            });
        }

        // Floating button handler
        if (floatingThemeBtn) {
            floatingThemeBtn.addEventListener('click', () => {
                const current = document.documentElement.getAttribute('data-theme') || 'dark';
                const newTheme = current === 'dark' ? 'light' : 'dark';
                this.applyTheme(newTheme);
                // Sync settings toggle
                if (themeToggle) themeToggle.checked = newTheme === 'dark';
            });
        }
    },

    applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('app-theme', theme);
        const themeStatusText = document.getElementById('theme-status-text');
        if (themeStatusText) {
            themeStatusText.textContent = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
        }
    },

    setupNavigation() {
        const navLinks = document.querySelectorAll('.nav-link');
        const mobileMenuBtn = document.getElementById('mobile-menu-btn');
        const mobileNav = document.getElementById('mobile-nav');
        const floatingNavBtn = document.getElementById('floating-nav-btn');
        const mobileNavPanel = document.getElementById('mobile-nav-panel');
        const mobileNavOverlay = document.getElementById('mobile-nav-overlay');
        const mobileNavCloseBtn = document.getElementById('mobile-nav-close-btn');

        // Helper: open mobile nav panel
        const openMobileNav = () => {
            if (mobileNavPanel) mobileNavPanel.classList.add('active');
            if (mobileNavOverlay) mobileNavOverlay.classList.add('active');
            document.body.style.overflow = 'hidden';
        };

        // Helper: close mobile nav panel
        const closeMobileNav = () => {
            if (mobileNavPanel) mobileNavPanel.classList.remove('active');
            if (mobileNavOverlay) mobileNavOverlay.classList.remove('active');
            document.body.style.overflow = '';
            if (floatingNavBtn && floatingNavBtn.dataset.drawerOpen === '1') {
                floatingNavBtn.dataset.drawerOpen = '0';
                floatingNavBtn.setAttribute('aria-expanded', 'false');
                // Return focus to the trigger for keyboard/screen-reader users
                floatingNavBtn.focus();
            }
        };

        // Escape closes the drawer; Backdrop click closes it (bound below)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && mobileNavPanel && mobileNavPanel.classList.contains('active')) {
                closeMobileNav();
            }
        });

        // Sidebar mobile menu toggle (inside sidebar)
        if (mobileMenuBtn && mobileNav) {
            mobileMenuBtn.addEventListener('click', () => {
                mobileNav.classList.toggle('active');
                mobileMenuBtn.classList.toggle('active');
            });
        }

        // Floating hamburger button (top-right on mobile)
        if (floatingNavBtn) {
            floatingNavBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openMobileNav();
                floatingNavBtn.dataset.drawerOpen = '1';
                floatingNavBtn.setAttribute('aria-expanded', 'true');
                if (mobileNavCloseBtn) mobileNavCloseBtn.focus();
            });
        }

        // Close button inside mobile nav panel
        if (mobileNavCloseBtn) {
            mobileNavCloseBtn.addEventListener('click', () => {
                closeMobileNav();
            });
        }

        // Click overlay to close
        if (mobileNavOverlay) {
            mobileNavOverlay.addEventListener('click', () => {
                closeMobileNav();
            });
        }
        
        navLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                // External routes (Admin panel): let the browser navigate.
                // The old code preventDefault'ed these and crashed switchView.
                if (link.classList.contains('nav-external') || !link.getAttribute('href').startsWith('#')) {
                    closeMobileNav();
                    if (mobileNav && mobileNav.classList.contains('active')) {
                        mobileNav.classList.remove('active');
                        if (mobileMenuBtn) mobileMenuBtn.classList.remove('active');
                    }
                    return; // target=_blank opens the admin panel in a new tab
                }
                // Close mobile menu when link is clicked
                if (mobileNav && mobileNav.classList.contains('active')) {
                    mobileNav.classList.remove('active');
                    if (mobileMenuBtn) mobileMenuBtn.classList.remove('active');
                }
                // Close mobile nav panel (slide-in) when link is clicked
                closeMobileNav();
                
                e.preventDefault();
                
                // If exiting interview view unexpectedly, cleanup streams
                // (but not when clicking Live Interview - that IS the session)
                const stayingInSession = link.closest('[data-live="true"]') !== null;
                if (document.getElementById('interview-view').classList.contains('active') && !stayingInSession) {
                    interview.stopRecording();
                    interview.stopWebcam();
                    antiCheat.stopMonitoring();
                    if (window.proctor) proctor.endSession();
                    this.resetTopbarContext();
                }

                let targetId = link.getAttribute('href').substring(1);
                const sessionActive = document.getElementById('interview-view').dataset.sessionActive === '1';
                if (link.dataset.intent === 'practice') {
                    // The practice setup panel lives on the Dashboard
                    targetId = 'dashboard-view';
                } else if (link.dataset.intent === 'live' && !sessionActive) {
                    // Nothing running yet - send them where a session is configured
                    targetId = 'dashboard-view';
                }
                this.switchView(targetId);

                // Update active state across BOTH navs (sidebar + drawer share
                // the same structure, so every matching item lights up)
                document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
                document.querySelectorAll('.nav-link').forEach(l => {
                    if (l.getAttribute('href') === link.getAttribute('href')
                        && (l.dataset.intent || '') === (link.dataset.intent || '')) {
                        l.parentElement.classList.add('active');
                    }
                });

                // Update topbar context based on view
                this.updateTopbarForView(targetId);

                 // Load appropriate module data
                 if (targetId === 'dashboard-view') {
                     dashboard.load();
                 } else if (targetId === 'scoreboard-view') {
                     scoreboard.load(); // Added awaits for async functions
                 } else if (targetId === 'history-view') {
                    this.loadFullHistory();
                } else if (targetId === 'achievements-view') {
                    this.loadAchievements();
                }

                // Practice Interview: guide toward the config panel
                if (link.dataset.intent === 'practice') {
                    const setup = document.querySelector('.setup-interview-panel');
                    if (setup) setup.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    this.showNavToast('Pick a role, then press Start Practice Session.');
                }

                // Live Interview: jump into the running session, or explain
                if (link.dataset.intent === 'live' && !sessionActive) {
                    this.showNavToast('No live session running - configure one on the Dashboard.');
                }
            });
        });

        // Non-blocking toast element used by the nav (auto-dismisses)
        if (!document.getElementById('nav-toast-el')) {
            const toast = document.createElement('div');
            toast.id = 'nav-toast-el';
            toast.className = 'nav-toast';
            toast.setAttribute('role', 'status');
            toast.setAttribute('aria-live', 'polite');
            document.body.appendChild(toast);
        }

        // "Back to Dashboard" button in feedback screen
        document.getElementById('back-to-dash-btn').addEventListener('click', () => {
            this.switchView('dashboard-view');
            document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
            document.querySelector('.nav-link[href="#dashboard-view"]').parentElement.classList.add('active');
            this.updateTopbarForView('dashboard-view');
            dashboard.load();
        });
    },

    showNavToast(message) {
        const toast = document.getElementById('nav-toast-el');
        if (!toast) return;
        toast.textContent = message;
        toast.classList.add('show');
        clearTimeout(this._navToastTimer);
        this._navToastTimer = setTimeout(() => toast.classList.remove('show'), 2800);
    },

    /**
     * Update topbar context based on current view
     */
    updateTopbarForView(viewId) {
        const titles = {
            'dashboard-view': { title: 'Mock AI Interview', desc: 'Polish your responses and master behavioral and technical roles.' },
            'interview-view': { title: 'Live Interview Session', desc: 'Active interview session — configure from Dashboard to start' },
            'feedback-view': { title: 'Interview Feedback', desc: 'Detailed AI analysis of your performance' },
            'history-view': { title: 'Practice History', desc: 'Review your past interview sessions and track your progress' },
            'scoreboard-view': { title: 'Score Board', desc: 'Track your score analytics, trends, and performance metrics' },
            'assessment-view': { title: 'Skill Assessment', desc: 'Gauge your interview readiness with a quick 3-question assessment' },
            'resources-view': { title: 'Resources & Tips', desc: 'Guides, strategies, and expert tips to ace your interviews' },
            'achievements-view': { title: 'Achievements', desc: 'Milestones and rewards for your interview practice journey' },
            'settings-view': { title: 'Settings', desc: 'Customize your interview preparation experience' }
        };

        const context = titles[viewId] || { title: 'Mock AI Interview', desc: '' };
        document.getElementById('topbar-view-title').textContent = context.title;
        document.getElementById('topbar-view-desc').textContent = context.desc;
    },

    /**
     * Update topbar context for interview mode with anti-cheat indicator
     */
    setInterviewTopbarContext(role) {
        document.getElementById('topbar-view-title').textContent = 'Live Interview Session';
        document.getElementById('topbar-view-desc').textContent = `Active interview for ${role} — Anti-Cheat monitoring is ON`;
    },

    /**
     * Reset topbar context back to default dashboard view
     */
    resetTopbarContext() {
        document.getElementById('topbar-view-title').textContent = 'Mock AI Interview';
        document.getElementById('topbar-view-desc').textContent = 'Polish your responses and master behavioral and technical roles.';
    },

    async setupDashboardForm() {
        const select = document.getElementById('interview-role-select');
        const startBtn = document.getElementById('start-interview-btn');

        // Role emoji mapping for visual enhancement
        const roleEmojis = {
            'Software Engineer': '💻',
            'Product Manager': '📋',
            'Data Analyst': '📊',
            'Sales Executive': '📈',
            'Marketing Manager': '📣',
            'HR Manager': '👥',
            'Financial Analyst': '💰',
            'Operations Manager': '⚙️',
            'Customer Support Lead': '🎧',
            'Healthcare Administrator': '🏥',
            'Project Manager': '📐',
            'Business Analyst': '🔍',
            'Teacher/Educator': '📚',
            'Retail Manager': '🏬',
            'Legal Associate': '⚖️',
            'Graphic Designer': '🎨',
            'Content Writer': '✍️'
        };

        // Load roles from backend - preserve full list if backend fails
        try {
            const data = await api.getRoles();
            if (data.roles && data.roles.length > 3) {
                // Backend returned a full list - use it
                select.innerHTML = '';
                data.roles.forEach(role => {
                    const opt = document.createElement('option');
                    opt.value = role;
                    const emoji = roleEmojis[role] || '📌';
                    opt.textContent = `${emoji} ${role}`;
                    select.appendChild(opt);
                });
            }
            // If backend returns only 3 fallback roles, keep the original HTML options
        } catch (error) {
            console.error('Failed to load roles from backend, keeping default list:', error);
        }

        // Start button trigger
        startBtn.addEventListener('click', () => {
            if (!this.getUser()) {
                this.showLoginPrompt('Start a practice interview');
                return;
            }
            const selectedRole = select.value;
            if (selectedRole) {
                interview.start(selectedRole);
            }
        });
    },

    /**
     * Setup skill assessment interaction
     */
    setupAssessment() {
        const startBtn = document.getElementById('start-assessment-btn');
        const levelCards = document.querySelectorAll('.level-card');
        let selectedLevel = 'intermediate';

        // Level card selection
        levelCards.forEach(card => {
            card.addEventListener('click', () => {
                levelCards.forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                selectedLevel = card.dataset.level;
            });
        });

        // Select intermediate by default
        document.querySelector('.level-card[data-level="intermediate"]').classList.add('selected');

        // Start assessment
        if (startBtn) {
            startBtn.addEventListener('click', () => {
                this.runAssessment(selectedLevel);
            });
        }
    },

    /**
     * Run a quick 3-question skill assessment
     */
    runAssessment(level) {
        const startSection = document.getElementById('assessment-start');
        const questionsSection = document.getElementById('assessment-questions');
        const resultsSection = document.getElementById('assessment-results');

        startSection.style.display = 'none';
        resultsSection.style.display = 'none';
        questionsSection.style.display = 'block';

        const difficultyLabels = {
            beginner: 'Beginner-Friendly',
            intermediate: 'Intermediate',
            advanced: 'Advanced'
        };

        const questions = {
            beginner: [
                { q: 'Tell me about yourself.', hint: 'Focus on your background, skills, and what you\'re looking for.' },
                { q: 'Why do you want this job?', hint: 'Connect your skills and interests to the role requirements.' },
                { q: 'What are your strengths and weaknesses?', hint: 'Be honest about weaknesses but show how you\'re improving.' }
            ],
            intermediate: [
                { q: 'Describe a challenging project you worked on.', hint: 'Use the STAR method: Situation, Task, Action, Result.' },
                { q: 'How do you handle conflict in a team?', hint: 'Show emotional intelligence and problem-solving approach.' },
                { q: 'Where do you see yourself in 5 years?', hint: 'Align your goals with the company\'s growth path.' }
            ],
            advanced: [
                { q: 'Describe a time you led a major initiative.', hint: 'Show leadership, strategic thinking, and measurable impact.' },
                { q: 'How would you improve our current processes?', hint: 'Demonstrate analytical thinking and business acumen.' },
                { q: 'Tell me about a failure and what you learned.', hint: 'Show accountability, reflection, and growth mindset.' }
            ]
        };

        const selectedQuestions = questions[level] || questions.intermediate;
        
        let html = `
            <div style="margin-bottom: 1.5rem;">
                <h4 style="margin-bottom: 0.5rem;">Assessment: ${difficultyLabels[level]}</h4>
                <p style="color: var(--text-secondary); font-size: 0.9rem;">Answer each question mentally or out loud, then rate your own response.</p>
            </div>
        `;

        selectedQuestions.forEach((item, idx) => {
            html += `
                <div class="card" style="margin-bottom: 1rem; padding: 1.25rem;">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.75rem;">
                        <h4 style="font-size: 1rem;">Q${idx + 1}: ${item.q}</h4>
                    </div>
                    <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 1rem;">
                        💡 <em>${item.hint}</em>
                    </p>
                    <div style="display: flex; align-items: center; gap: 1rem;">
                        <span style="font-size: 0.85rem; color: var(--text-secondary);">How confident are you with your answer?</span>
                        <div class="confidence-buttons" style="display: flex; gap: 0.5rem;">
                            <button class="btn btn-outline confidence-btn" data-question="${idx}" data-score="1" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">😬 Weak</button>
                            <button class="btn btn-outline confidence-btn" data-question="${idx}" data-score="2" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">🙂 Okay</button>
                            <button class="btn btn-outline confidence-btn" data-question="${idx}" data-score="3" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">💪 Strong</button>
                        </div>
                    </div>
                </div>
            `;
        });

        html += `
            <button class="btn btn-primary" id="submit-assessment-btn" style="width: 100%; margin-top: 1rem;">
                See My Results
            </button>
        `;

        questionsSection.innerHTML = html;

        // Handle confidence button clicks
        document.querySelectorAll('.confidence-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const group = btn.parentElement;
                group.querySelectorAll('.confidence-btn').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-outline');
                });
                btn.classList.remove('btn-outline');
                btn.classList.add('btn-primary');
            });
        });

        // Submit assessment
        document.getElementById('submit-assessment-btn').addEventListener('click', () => {
            this.completeAssessment(level);
        });
    },

    /**
     * Complete assessment and show results
     */
    completeAssessment(level) {
        const selectedBtns = document.querySelectorAll('.confidence-btn.btn-primary');
        // Each question has its own card wrapper div
        const totalQuestions = document.querySelectorAll('#assessment-questions .card').length || 3;
        
        // Calculate score based on confidence selections
        let totalScore = 0;
        selectedBtns.forEach(btn => {
            totalScore += parseInt(btn.dataset.score);
        });

        // If no buttons were selected, default to mid-range
        const maxScore = totalQuestions * 3;
        const actualScore = selectedBtns.length > 0 ? totalScore : Math.ceil(totalQuestions * 2);
        const percentage = Math.round((actualScore / maxScore) * 100);

        const questionsSection = document.getElementById('assessment-questions');
        const resultsSection = document.getElementById('assessment-results');

        questionsSection.style.display = 'none';
        resultsSection.style.display = 'block';

        let levelLabel, levelDesc, nextSteps;
        if (percentage >= 80) {
            levelLabel = '🌟 Advanced';
            levelDesc = 'You\'re well-prepared! Focus on refining specific areas and practicing under timed conditions.';
            nextSteps = 'Try a full-length interview with 8-10 questions to simulate real conditions.';
        } else if (percentage >= 55) {
            levelLabel = '📈 Intermediate';
            levelDesc = 'Good foundation! Keep practicing to build confidence and expand your answer depth.';
            nextSteps = 'Focus on the STAR method and practice with 5-question sessions.';
        } else {
            levelLabel = '🌱 Beginner';
            levelDesc = 'Great start! Focus on building your confidence with common interview questions first.';
            nextSteps = 'Start with 3-question quick sessions and review the Resources & Tips section.';
        }

        // Update the dashboard skill level stat
        document.getElementById('stat-rating').textContent = levelLabel.split(' ')[1] || levelLabel;

        resultsSection.innerHTML = `
            <div style="text-align: center; padding: 2rem 0;">
                <div style="font-size: 4rem; margin-bottom: 1rem;">${percentage >= 80 ? '🎉' : percentage >= 55 ? '👍' : '💪'}</div>
                <h3 style="font-size: 1.8rem; margin-bottom: 0.5rem;">${levelLabel}</h3>
                <div class="radial-progress-wrapper" style="margin: 1.5rem auto;">
                    <svg class="radial-svg" style="width: 120px; height: 120px;">
                        <circle class="radial-bg" cx="60" cy="60" r="52" style="stroke-width: 6;"></circle>
                        <circle class="radial-fill" id="assessment-radial" cx="60" cy="60" r="52" style="stroke-width: 6; stroke-dasharray: 327; stroke-dashoffset: ${327 - (percentage/100) * 327};"></circle>
                    </svg>
                    <div class="radial-text">
                        <span class="radial-score" style="font-size: 1.8rem;">${percentage}%</span>
                        <span class="radial-label">Readiness</span>
                    </div>
                </div>
                <p style="color: var(--text-secondary); line-height: 1.6; max-width: 500px; margin: 0 auto 1rem;">${levelDesc}</p>
                <div class="card" style="background: rgba(99, 102, 241, 0.05); border-color: rgba(99, 102, 241, 0.15); margin-bottom: 1.5rem;">
                    <p style="font-size: 0.9rem;"><strong>Next Step:</strong> ${nextSteps}</p>
                </div>
                <button class="btn btn-primary" id="retake-assessment-btn" style="margin-right: 0.75rem;">
                    Retake Assessment
                </button>
                <button class="btn btn-secondary" id="assessment-to-dash-btn">
                    Go to Dashboard
                </button>
            </div>
        `;

        document.getElementById('retake-assessment-btn').addEventListener('click', () => {
            resultsSection.style.display = 'none';
            document.getElementById('assessment-start').style.display = 'block';
        });

        document.getElementById('assessment-to-dash-btn').addEventListener('click', () => {
            this.switchView('dashboard-view');
            document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
            document.querySelector('.nav-link[href="#dashboard-view"]').parentElement.classList.add('active');
            this.updateTopbarForView('dashboard-view');
            dashboard.load();
        });
    },

    /**
     * Setup settings page
     */
    async setupSettings() {
        const saveBtn = document.getElementById('save-settings-btn');
        const user = this.getUser() || {};
        const saved = this.readLocalSettings();

        // The account is the source of truth for the display name and the default
        // role; localStorage only carries the per-browser preferences.
        await this.loadSettingsRoles();

        const roleEl = document.getElementById('settings-role');
        if (roleEl) {
            const current = user.target_role || saved.role || roleEl.value;
            if (current) roleEl.value = current;
        }
        const nameEl = document.getElementById('settings-name');
        if (nameEl) nameEl.value = user.name || saved.name || 'User';
        const questions = document.getElementById('settings-questions');
        if (questions) questions.value = saved.questions || '5';
        const autoAdvance = document.getElementById('setting-auto-advance');
        if (autoAdvance) autoAdvance.checked = saved.autoAdvance !== false;
        const showTranscript = document.getElementById('setting-show-transcript');
        if (showTranscript) showTranscript.checked = saved.showTranscript !== false;

        if (!saveBtn) return;
        saveBtn.addEventListener('click', async () => {
            const name = document.getElementById('settings-name').value || 'User';
            const role = document.getElementById('settings-role').value;
            const questions = document.getElementById('settings-questions').value;
            const autoAdvance = document.getElementById('setting-auto-advance').checked;
            const showTranscript = document.getElementById('setting-show-transcript').checked;
            const originalText = saveBtn.textContent;

            // Per-browser preferences.
            localStorage.setItem('interview-settings', JSON.stringify(
                { name, role, questions, autoAdvance, showTranscript }
            ));

            // The display name and default role belong to the account, so they are
            // persisted server-side too. Without this the dashboard keeps showing
            // the seeded default role no matter what was picked here.
            if (this.getUser()) {
                saveBtn.disabled = true;
                saveBtn.textContent = 'Saving...';
                try {
                    await api.updateProfile({ name, target_role: role });
                } catch (error) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = originalText;
                    this.showNavToast(error.message || 'Could not save your profile');
                    return;
                }
                saveBtn.disabled = false;
                this.cacheProfile({ name, target_role: role });
            }

            this.applyProfileToChrome(name, role);

            saveBtn.textContent = '✓ Settings Saved!';
            setTimeout(() => {
                saveBtn.textContent = originalText;
            }, 2000);
        });
    },

    /** Read the per-browser settings blob, tolerating absent or corrupt data. */
    readLocalSettings() {
        try {
            return JSON.parse(localStorage.getItem('interview-settings')) || {};
        } catch (error) {
            return {};
        }
    },

    /** Fill the settings role dropdown from the question bank's role list. */
    async loadSettingsRoles() {
        const roleEl = document.getElementById('settings-role');
        if (!roleEl) return;
        try {
            const data = await api.getRoles();
            const roles = (data && data.roles) || [];
            if (Array.isArray(roles) && roles.length) {
                const current = roleEl.value;
                roleEl.innerHTML = '';
                roles.forEach(role => {
                    const opt = document.createElement('option');
                    opt.value = role;
                    opt.textContent = role;
                    roleEl.appendChild(opt);
                });
                // Keep a stored role that is no longer in the bank selectable.
                if (current && !roles.includes(current)) {
                    const opt = document.createElement('option');
                    opt.value = current;
                    opt.textContent = current;
                    roleEl.appendChild(opt);
                }
            }
        } catch (error) {
            console.error('Failed to load roles for settings:', error);
        }
    },

    /** Mirror a saved profile into the cached session the rest of the app reads. */
    cacheProfile(changes) {
        const cached = this.getUser();
        if (!cached) return;
        Object.assign(cached, changes);
        localStorage.setItem('user', JSON.stringify(cached));
        localStorage.setItem('interview_current_user', JSON.stringify(cached));
    },

    /** Keep every place the name and role appear in step (sidebar + drawer). */
    applyProfileToChrome(name, role) {
        document.querySelectorAll('.username').forEach(el => { el.textContent = name; });
        document.querySelectorAll('.userrole').forEach(el => { el.textContent = 'Target: ' + role; });
        const initial = (name || 'U').charAt(0).toUpperCase();
        ['user-avatar-tag', 'user-avatar-tag-mobile'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = initial;
        });
    },

    /**
     * Setup resume import functionality
     */
    setupResumeImport() {
        const importBtn = document.getElementById('import-resume-btn');
        const modal = document.getElementById('resume-import-modal');
        const closeBtn = document.getElementById('close-resume-modal');
        const dropZone = document.getElementById('resume-drop-zone');
        const fileInput = document.getElementById('resume-file-input');
        const browseBtn = document.getElementById('browse-resume-btn');
        const startInterviewBtn = document.getElementById('start-resume-interview-btn');
        const loader = document.getElementById('resume-loader');
        const result = document.getElementById('resume-result');
        const preview = document.getElementById('resume-preview');
        const analysisText = document.getElementById('resume-analysis-text');
        const questionsList = document.getElementById('resume-questions-list');

        // Open modal
        if (importBtn) {
            importBtn.addEventListener('click', () => {
                if (!this.getUser()) {
                    this.showLoginPrompt('import your resume for custom questions');
                    return;
                }
                modal.style.display = 'flex';
                modal.style.alignItems = 'center';
                modal.style.justifyContent = 'center';
                this.resetResumeModal();
            });
        }

        // Close modal
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                modal.style.display = 'none';
            });
        }

        // Close modal on backdrop click
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.style.display = 'none';
                }
            });
        }

        // Drag and drop handlers
        if (dropZone) {
            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.classList.add('dragover');
            });

            dropZone.addEventListener('dragleave', () => {
                dropZone.classList.remove('dragover');
            });

            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.classList.remove('dragover');
                const file = e.dataTransfer.files[0];
                if (file) this.processResumeFile(file);
            });
        }

        // Browse button
        if (browseBtn) {
            browseBtn.addEventListener('click', () => {
                if (fileInput) fileInput.click();
            });
        }

        // File input change
        if (fileInput) {
            fileInput.addEventListener('change', () => {
                if (fileInput.files[0]) {
                    this.processResumeFile(fileInput.files[0]);
                }
            });
        }

        // Start interview with resume questions
        if (startInterviewBtn) {
            startInterviewBtn.addEventListener('click', () => {
                if (!this.getUser()) {
                    this.showLoginPrompt('Start a resume-based interview');
                    return;
                }
                if (window.resumeQuestions && window.resumeQuestions.length > 0) {
                    if (modal) modal.style.display = 'none';
                    interview.startWithResumeQuestions(window.resumeQuestions, window.resumeRole, window.resumeText || '');
                }
            });
        }
    },

    readFileAsText(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = reject;
            reader.readAsText(file);
        });
    },

    resetResumeModal() {
        document.getElementById('resume-drop-zone').style.display = 'block';
        document.getElementById('resume-preview').style.display = 'none';
        document.getElementById('resume-loader').style.display = 'none';
        document.getElementById('resume-result').style.display = 'none';
        document.getElementById('resume-file-input').value = '';
    },

    async processResumeFile(file) {
        const loader = document.getElementById('resume-loader');
        const preview = document.getElementById('resume-preview');
        const result = document.getElementById('resume-result');
        const dropZone = document.getElementById('resume-drop-zone');
        const analysisText = document.getElementById('resume-analysis-text');
        const questionsList = document.getElementById('resume-questions-list');

        // Validate file type
        const allowedTypes = ['.pdf', '.docx', '.txt'];
        const fileExt = '.' + file.name.split('.').pop().toLowerCase();
        if (!allowedTypes.includes(fileExt)) {
            alert('Please upload a PDF, DOCX, or TXT file.');
            return;
        }

        // Show loader
        dropZone.style.display = 'none';
        preview.style.display = 'none';
        result.style.display = 'none';
        loader.style.display = 'flex';

        try {
            // Read file content for Q&A (only works for TXT files on frontend)
            let fileContent = '';
            if (fileExt === '.txt') {
                fileContent = await this.readFileAsText(file);
            } else {
                // For PDF/DOCX, we'll use a placeholder - backend handles extraction
                fileContent = `Resume content (${file.name}) - analyzed by backend.`;
            }
            
            const resumeData = await api.analyzeResume(file, 3);

            // Hide loader, show results
            loader.style.display = 'none';
            preview.style.display = 'block';
            result.style.display = 'block';

            // Store resume text for Q&A
            window.resumeText = resumeData.full_text || fileContent;

            // Display what was actually found. Built with DOM nodes rather than
            // innerHTML because all of this text comes from the uploaded resume.
            const foundSkills = resumeData.skills || [];
            analysisText.innerHTML = '';

            const roleRow = document.createElement('div');
            roleRow.style.marginBottom = '0.5rem';
            const roleLabel = document.createElement('strong');
            roleLabel.textContent = 'Detected Role: ';
            roleRow.appendChild(roleLabel);
            roleRow.appendChild(document.createTextNode(resumeData.detected_role || 'Professional'));
            analysisText.appendChild(roleRow);

            if (foundSkills.length) {
                const skillRow = document.createElement('div');
                skillRow.style.marginBottom = '0.5rem';
                const skillLabel = document.createElement('strong');
                skillLabel.textContent = 'Skills found on your resume: ';
                skillRow.appendChild(skillLabel);
                skillRow.appendChild(document.createTextNode(foundSkills.join(', ')));
                analysisText.appendChild(skillRow);
            }

            const summaryRow = document.createElement('div');
            const summaryLabel = document.createElement('strong');
            summaryLabel.textContent = 'Summary: ';
            summaryRow.appendChild(summaryLabel);
            summaryRow.appendChild(document.createTextNode(resumeData.summary || 'Resume analyzed successfully'));
            analysisText.appendChild(summaryRow);

            // Display questions
            questionsList.innerHTML = '';
            window.resumeQuestions = resumeData.questions || [];
            window.resumeRole = resumeData.detected_role || 'Based on Resume';

            window.resumeQuestions.forEach((q) => {
                const div = document.createElement('div');
                div.className = 'resume-question-item';
                const category = document.createElement('div');
                category.className = 'resume-question-category';
                category.textContent = q.category || 'Question';
                const text = document.createElement('div');
                text.className = 'resume-question-text';
                text.textContent = q.question_text || '';
                div.appendChild(category);
                div.appendChild(text);
                questionsList.appendChild(div);
            });

        } catch (error) {
            console.error('Failed to analyze resume:', error);
            loader.style.display = 'none';
            dropZone.style.display = 'block';
            let errorMsg = 'Failed to analyze resume. Please try again with a different file.';
            if (error.message) {
                errorMsg = `Error: ${error.message}. ${errorMsg}`;
            }
            alert(errorMsg);
        }
    },

    /**
     * Setup resource button interactions
     */
    setupResourceButtons() {
        document.querySelectorAll('.resource-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const resource = btn.dataset.resource;
                this.showResourceModal(resource);
            });
        });
    },

    /**
     * Show resource content in a modal
     */
    showResourceModal(resource) {
        const resourceContent = {
            questions: {
                title: 'Common Interview Questions',
                content: `
                    <h4 style="margin-bottom: 1rem;">🎯 <strong>By Category</strong></h4>
                    <div style="margin-bottom: 1.5rem;">
                        <p style="font-weight: 600; color: var(--primary); margin-bottom: 0.5rem;">Behavioral Questions</p>
                        <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                            <li>Tell me about yourself.</li>
                            <li>Why do you want to work here?</li>
                            <li>Describe a challenge you overcame.</li>
                            <li>Where do you see yourself in 5 years?</li>
                        </ul>
                    </div>
                    <div style="margin-bottom: 1.5rem;">
                        <p style="font-weight: 600; color: var(--primary); margin-bottom: 0.5rem;">Technical Questions</p>
                        <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                            <li>Walk me through your technical experience.</li>
                            <li>How do you stay updated with industry trends?</li>
                            <li>Describe a complex technical problem you solved.</li>
                        </ul>
                    </div>
                    <hr style="border-color: var(--border-glass); margin: 1rem 0;">
                    <p style="color: var(--text-muted); font-size: 0.85rem;">Practice answering these with our AI interviewer for personalized feedback!</p>
                `
            },
            star: {
                title: 'STAR Method Guide',
                content: `
                    <h4 style="margin-bottom: 1rem;">🎯 <strong>The STAR Framework</strong></h4>
                    <div style="display: grid; gap: 1rem; margin-bottom: 1.5rem;">
                        <div class="card" style="padding: 1rem; background: rgba(99, 102, 241, 0.05);">
                            <strong style="color: var(--primary);">S</strong> - <strong>Situation</strong>
                            <p style="color: var(--text-secondary); font-size: 0.9rem;">Set the context. Describe the scenario you were in.</p>
                            <p style="color: var(--text-muted); font-size: 0.85rem;"><em>Example: "In my previous role as a team lead..."</em></p>
                        </div>
                        <div class="card" style="padding: 1rem; background: rgba(99, 102, 241, 0.05);">
                            <strong style="color: var(--primary);">T</strong> - <strong>Task</strong>
                            <p style="color: var(--text-secondary); font-size: 0.9rem;">What needed to be done? What was your responsibility?</p>
                            <p style="color: var(--text-muted); font-size: 0.85rem;"><em>Example: "I was responsible for delivering the project by Q3..."</em></p>
                        </div>
                        <div class="card" style="padding: 1rem; background: rgba(99, 102, 241, 0.05);">
                            <strong style="color: var(--primary);">A</strong> - <strong>Action</strong>
                            <p style="color: var(--text-secondary); font-size: 0.9rem;">What specific steps did you take? Focus on YOUR contribution.</p>
                            <p style="color: var(--text-muted); font-size: 0.85rem;"><em>Example: "I organized a cross-functional team and implemented a new workflow..."</em></p>
                        </div>
                        <div class="card" style="padding: 1rem; background: rgba(99, 102, 241, 0.05);">
                            <strong style="color: var(--primary);">R</strong> - <strong>Result</strong>
                            <p style="color: var(--text-secondary); font-size: 0.9rem;">What was the outcome? Use metrics when possible.</p>
                            <p style="color: var(--text-muted); font-size: 0.85rem;"><em>Example: "We delivered 2 weeks early with a 15% cost reduction..."</em></p>
                        </div>
                    </div>
                `
            },
            'body-language': {
                title: 'Body Language Tips',
                content: `
                    <h4 style="margin-bottom: 1rem;">🧠 <strong>Non-Verbal Communication</strong></h4>
                    <ul style="color: var(--text-secondary); line-height: 2.2; padding-left: 1.5rem;">
                        <li><strong>Eye Contact:</strong> Maintain 60-70% eye contact. Too much can seem intense, too little seems disinterested.</li>
                        <li><strong>Posture:</strong> Sit up straight, lean slightly forward to show engagement.</li>
                        <li><strong>Hand Gestures:</strong> Use natural hand movements to emphasize points. Avoid crossing arms.</li>
                        <li><strong>Facial Expressions:</strong> Smile genuinely, nod to show understanding.</li>
                        <li><strong>Voice Tone:</strong> Vary your pitch and pace. Monotone voice signals disinterest.</li>
                        <li><strong>Mirroring:</strong> Subtly mirror the interviewer's body language to build rapport.</li>
                    </ul>
                    <div class="card" style="margin-top: 1rem; padding: 1rem; background: rgba(245, 158, 11, 0.05);">
                        <p style="font-size: 0.85rem; color: var(--text-secondary);">💡 <strong>Pro Tip:</strong> Record yourself answering questions and review your body language. Practice in front of a mirror!</p>
                    </div>
                `
            },
            fillers: {
                title: 'Filler Words Guide',
                content: `
                    <h4 style="margin-bottom: 1rem;">💡 <strong>Eliminate Filler Words</strong></h4>
                    <p style="color: var(--text-secondary); margin-bottom: 1rem;">Filler words like <em>"um", "uh", "like", "you know", "actually", "basically"</em> make you sound less confident and prepared.</p>
                    <h5 style="margin-bottom: 0.75rem;">Strategies to Reduce Fillers:</h5>
                    <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                        <li><strong>Pause instead of filler:</strong> Silence is better than "um". Take a breath to collect your thoughts.</li>
                        <li><strong>Slow down:</strong> Speaking too fast leads to filler words. Aim for a steady, measured pace.</li>
                        <li><strong>Record yourself:</strong> Listen back and count your filler words. Awareness is the first step.</li>
                        <li><strong>Practice with structure:</strong> Use the STAR method to organize thoughts before speaking.</li>
                        <li><strong>Replace fillers with pauses:</strong> Practice saying nothing for 1-2 seconds instead of "um".</li>
                    </ul>
                `
            },
            salary: {
                title: 'Salary Negotiation',
                content: `
                    <h4 style="margin-bottom: 1rem;">📊 <strong>Negotiate Like a Pro</strong></h4>
                    <div style="margin-bottom: 1rem;">
                        <h5 style="color: var(--primary); margin-bottom: 0.5rem;">Before the Interview</h5>
                        <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                            <li>Research market rates for the role and location (Glassdoor, levels.fyi, LinkedIn)</li>
                            <li>Know your minimum acceptable number</li>
                            <li>Prepare talking points about your value</li>
                        </ul>
                    </div>
                    <div style="margin-bottom: 1rem;">
                        <h5 style="color: var(--primary); margin-bottom: 0.5rem;">During Negotiation</h5>
                        <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                            <li>Never give the first number if possible</li>
                            <li>Provide a range (low = your minimum, high = aspirational)</li>
                            <li>Consider total compensation: base + bonus + equity + benefits</li>
                            <li>Use phrases like "Based on my research and experience..."</li>
                        </ul>
                    </div>
                    <div class="card" style="padding: 1rem; background: rgba(16, 185, 129, 0.05);">
                        <p style="font-size: 0.85rem; color: var(--text-secondary);">🎯 <strong>Script:</strong> "I'm very excited about this role. Based on my experience in [skill] and market research, I was hoping for a compensation package around [range]. Is that aligned with your budget?"</p>
                    </div>
                `
            },
            voice: {
                title: 'Voice & Pace Control',
                content: `
                    <h4 style="margin-bottom: 1rem;">🎙️ <strong>Voice Exercises</strong></h4>
                    <div style="margin-bottom: 1rem;">
                        <h5 style="color: var(--primary); margin-bottom: 0.5rem;">Pacing Exercises</h5>
                        <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                            <li><strong>The Pause Practice:</strong> Read a paragraph aloud. Pause for 2 seconds after every period.</li>
                            <li><strong>Slow Down Drill:</strong> Read at half your normal speed. Gradually increase while maintaining clarity.</li>
                            <li><strong>Metronome Method:</strong> Use a metronome at 120 BPM and speak one word per beat.</li>
                        </ul>
                    </div>
                    <div style="margin-bottom: 1rem;">
                        <h5 style="color: var(--primary); margin-bottom: 0.5rem;">Tone & Clarity</h5>
                        <ul style="color: var(--text-secondary); line-height: 2; padding-left: 1.5rem;">
                            <li><strong>Breathing:</strong> Practice diaphragmatic breathing to support your voice.</li>
                            <li><strong>Tongue Twisters:</strong> "She sells seashells" and "Peter Piper" improve articulation.</li>
                            <li><strong>Record & Review:</strong> Listen for monotone patterns and work on varying pitch.</li>
                        </ul>
                    </div>
                `
            }
        };

        const data = resourceContent[resource];
        if (!data) return;

        // Create modal overlay
        const overlay = document.createElement('div');
        overlay.className = 'anti-cheat-overlay resource-modal';
        overlay.style.zIndex = '10000';
        overlay.style.cursor = 'pointer';
        overlay.innerHTML = `
            <div class="ac-overlay-content" style="max-width: 600px; max-height: 80vh; overflow-y: auto; text-align: left; cursor: default;" onclick="event.stopPropagation()">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
                    <h2 style="margin: 0; color: var(--text-primary);">${data.title}</h2>
                    <button class="btn btn-secondary" id="close-resource-modal" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">✕ Close</button>
                </div>
                <div>${data.content}</div>
            </div>
        `;

        document.body.appendChild(overlay);

        // Close handlers
        overlay.addEventListener('click', () => overlay.remove());
        document.getElementById('close-resource-modal').addEventListener('click', () => overlay.remove());
    },

    switchView(viewId) {
        document.querySelectorAll('.content-view').forEach(view => {
            view.classList.remove('active');
        });
        document.getElementById(viewId).classList.add('active');
    },

    showLoader(text = 'Processing...') {
        document.getElementById('loader-text-detail').textContent = text;
        document.getElementById('loader-view').classList.add('active');
    },

    hideLoader() {
        document.getElementById('loader-view').classList.remove('active');
    },

    // Results feedback rendering
    showFeedbackDetail(result) {
        this.switchView('feedback-view');
        this.updateTopbarForView('feedback-view');

        // Title and summary details
        document.getElementById('feedback-role').textContent = `${result.role || interview.state.role || 'Mock'} Interview Results`;
        document.getElementById('feedback-date').textContent = result.date;
        document.getElementById('feedback-summary').textContent = result.summary;

        // Animate overall score radial ring
        this.animateRadialScore(result.overall_score);

        // Render detailed feedback cards
        const container = document.getElementById('answers-review-container');
        container.innerHTML = '';

        result.answers.forEach((ans, idx) => {
            const item = document.createElement('div');
            item.className = 'question-feedback-item';
            
            const fb = ans.feedback;
            
            let scoreClass = 'low';
            if (ans.score >= 80) scoreClass = 'high';
            else if (ans.score >= 65) scoreClass = 'mid';

            item.innerHTML = `
                <div class="q-fb-header" onclick="app.toggleFeedbackAccordion(this)">
                    <span class="q-fb-title">Q${idx + 1}: ${ans.question_text}</span>
                    <span class="score-badge ${scoreClass}">${ans.score}%</span>
                </div>
                <div class="q-fb-body" style="display: none;">
                    <div class="transcript-section">
                        <div class="transcript-label">Your Transcript Response:</div>
                        <div class="transcript-quote">"${ans.transcript || 'No response recorded.'}"</div>
                    </div>
                    
                    <div class="q-fb-subscores">
                        <div class="subscore-bar-item">
                            <div class="subscore-bar-header">
                                <span>Clarity</span>
                                <span>${fb.clarity}%</span>
                            </div>
                            <div class="subscore-bar-track">
                                <div class="subscore-bar-fill" style="width: ${fb.clarity}%"></div>
                            </div>
                        </div>
                        <div class="subscore-bar-item">
                            <div class="subscore-bar-header">
                                <span>Grammar</span>
                                <span>${fb.grammar}%</span>
                            </div>
                            <div class="subscore-bar-track">
                                <div class="subscore-bar-fill" style="width: ${fb.grammar}%"></div>
                            </div>
                        </div>
                        <div class="subscore-bar-item">
                            <div class="subscore-bar-header">
                                <span>Relevance</span>
                                <span>${fb.relevance}%</span>
                            </div>
                            <div class="subscore-bar-track">
                                <div class="subscore-bar-fill" style="width: ${fb.relevance}%"></div>
                            </div>
                        </div>
                        <div class="subscore-bar-item">
                            <div class="subscore-bar-header">
                                <span>Filler Words</span>
                                <span>${fb.filler_count} used</span>
                            </div>
                            <div class="subscore-bar-track">
                                <div class="subscore-bar-fill" style="width: ${Math.max(0, 100 - (fb.filler_count * 10))}%"></div>
                            </div>
                        </div>
                    </div>

                    <div class="critique-box strengths-box">
                        <div class="critique-title">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                            Strengths
                        </div>
                        <ul class="critique-list">
                            ${fb.strengths.map(s => `<li>${s}</li>`).join('')}
                        </ul>
                    </div>

                    <div class="critique-box weaknesses-box">
                        <div class="critique-title">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            Improvement Areas
                        </div>
                        <ul class="critique-list">
                            ${fb.weaknesses.map(w => `<li>${w}</li>`).join('')}
                        </ul>
                    </div>

                    <div class="actionable-tips-box">
                        <div class="tips-title">💡 Actionable Tips for Next Time</div>
                        <ul class="critique-list">
                            ${fb.tips.map(t => `<li>${t}</li>`).join('')}
                        </ul>
                    </div>
                </div>
            `;
            container.appendChild(item);
        });

        // Show feedback rating modal after a short delay
        setTimeout(() => {
            if (typeof showFeedbackModal === 'function') {
                showFeedbackModal();
            }
        }, 1500);
    },

    animateRadialScore(score) {
        const fill = document.getElementById('radial-fill-circle');
        const text = document.getElementById('radial-score-val');
        
        // Circumference of standard SVG circle is 2 * pi * r = 2 * 3.14159 * 70 = ~440px
        const circumference = 440;
        const offset = circumference - (score / 100) * circumference;
        
        // Trigger style transition
        fill.style.strokeDashoffset = offset;
        
        // Counter animation
        let count = 0;
        const interval = setInterval(() => {
            if (count >= score) {
                text.textContent = `${score}%`;
                clearInterval(interval);
            } else {
                count += 1;
                text.textContent = `${count}%`;
            }
        }, 12);
    },

    toggleFeedbackAccordion(headerElement) {
        const body = headerElement.nextElementSibling;
        const isOpen = body.style.display !== 'none';
        
        // Slide / Toggle display
        body.style.display = isOpen ? 'none' : 'grid';
    },

    async navigateToInterviewDetail(id) {
        this.showLoader('Loading interview review detail...');
        try {
            const detail = await api.getInterviewDetail(id);
            this.hideLoader();
            this.showFeedbackDetail(detail);
        } catch (error) {
            console.error('Error fetching detail:', error);
            this.hideLoader();
        }
    },

    // Load full history inside History view list tab
    async loadFullHistory() {
        const tableBody = document.getElementById('history-table-body');
        tableBody.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-secondary);">Loading full practice log history...</div>';

        try {
            const data = await api.getDashboard();
            tableBody.innerHTML = '';

            if (!data.history || data.history.length === 0) {
                tableBody.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-muted);">No records found. Complete a mock interview to populate history!</div>';
                return;
            }

            data.history.forEach(item => {
                const div = document.createElement('div');
                div.className = 'card history-card-item';
                div.style.cursor = 'pointer';

                let scoreClass = 'low';
                if (item.overall_score >= 80) scoreClass = 'high';
                else if (item.overall_score >= 65) scoreClass = 'mid';

                div.innerHTML = `
                    <div class="role">${item.role} Practice</div>
                    <div class="date">${item.date}</div>
                    <div class="score-badge ${scoreClass}">${item.overall_score}%</div>
                    <div class="summary-text">Click to review detailed feedback and metrics</div>
                    <button class="btn btn-secondary" style="padding:0.5rem 1rem; font-size:0.85rem;">View Review</button>
                `;

                div.addEventListener('click', () => {
                    this.navigateToInterviewDetail(item.id);
                });

                tableBody.appendChild(div);
            });
        } catch (error) {
            console.error('Failed to load history metrics:', error);
            tableBody.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--danger);">Error connecting to the database. Please check your backend is running on port 5000.</div>';
        }
    },

    // Load achievements from dashboard data
    async loadAchievements() {
        const achievementCount = document.getElementById('achievement-count');
        
        try {
            const data = await api.getDashboard();
            const totalSessions = data.metrics?.total_interviews || 0;
            const avgScore = data.metrics?.average_score || 0;
            
            // Count unlocked achievements
            let unlocked = 0;
            
            // First Steps: Complete first interview
            const firstSteps = document.querySelector('[data-achievement="first-interview"]');
            if (totalSessions >= 1) {
                firstSteps.querySelector('.achievement-icon').classList.add('unlocked');
                firstSteps.querySelector('.achievement-status').textContent = '✅';
                firstSteps.querySelector('.achievement-status').classList.add('unlocked');
                unlocked++;
            }
            
            // Getting Serious: 5 sessions
            const fiveSessions = document.querySelector('[data-achievement="five-sessions"]');
            if (totalSessions >= 5) {
                fiveSessions.querySelector('.achievement-icon').classList.add('unlocked');
                fiveSessions.querySelector('.achievement-status').textContent = '✅';
                fiveSessions.querySelector('.achievement-status').classList.add('unlocked');
                unlocked++;
            }
            
            // Perfect Score: 90%+
            const perfectScore = document.querySelector('[data-achievement="perfect-score"]');
            if (avgScore >= 90) {
                perfectScore.querySelector('.achievement-icon').classList.add('unlocked');
                perfectScore.querySelector('.achievement-status').textContent = '✅';
                perfectScore.querySelector('.achievement-status').classList.add('unlocked');
                unlocked++;
            }
            
            // Veteran: 25 sessions
            const veteran = document.querySelector('[data-achievement="veteran"]');
            if (totalSessions >= 25) {
                veteran.querySelector('.achievement-icon').classList.add('unlocked');
                veteran.querySelector('.achievement-status').textContent = '✅';
                veteran.querySelector('.achievement-status').classList.add('unlocked');
                unlocked++;
            }
            
            achievementCount.textContent = `${unlocked} unlocked`;
            
        } catch (error) {
            console.error('Failed to load achievements:', error);
            achievementCount.textContent = '0 unlocked';
        }
    }
};

// Initialize when DOM content is loaded (or already loaded)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { app.init(); });
} else {
    app.init();
}

// Global welcome popup close
function closeWelcomePopup() {
    const popup = document.getElementById('welcome-popup');
    if (popup) {
        popup.style.opacity = '0';
        popup.style.transition = 'opacity 0.3s ease';
        setTimeout(() => {
            popup.classList.remove('active');
            popup.style.opacity = '';
        }, 300);
    }
    // Mark welcome as permanently dismissed so it never shows again
    localStorage.setItem('welcome-dismissed', '1');
}