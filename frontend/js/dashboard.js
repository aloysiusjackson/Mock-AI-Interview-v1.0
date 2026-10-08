let categoryRadarChart = null;

const dashboard = {
    async load() {
        try {
            const data = await api.getDashboard();
            this.render(data);
        } catch (error) {
            console.error('Error loading dashboard:', error);
            // Fallback mock visuals in case backend fails or hasn't started yet
            this.renderFallback();
        } finally {
            app.clearSkeletons();
        }
    },

    render(data) {
        // Update user profile info — use logged-in user if available, otherwise Guest Account
        const loggedInUser = app.getUser();
        const displayName = loggedInUser ? loggedInUser.name : 'Guest Account';
        const displayRole = loggedInUser ? (loggedInUser.target_role || 'Software Engineer') : 'Software Engineer';
        document.querySelectorAll('.username').forEach(el => el.textContent = displayName);
        document.querySelectorAll('.userrole').forEach(el => el.textContent = `Target: ${displayRole}`);

        // Update basic metrics - ensure scores are properly rounded integers
        const totalSessions = Number(data.metrics.total_interviews) || 0;
        const hasSessions = totalSessions > 0;
        const avgScore = Math.round(data.metrics.average_score);

        // A brand-new account has no history yet. Show a dash instead of a
        // misleading "0 / 0% / Beginner" that reads like a failed score.
        document.getElementById('stat-total-interviews').textContent = hasSessions ? totalSessions : '—';
        document.getElementById('stat-avg-score').textContent = hasSessions ? `${avgScore}%` : '—';
        document.getElementById('stat-avg-fillers').textContent =
            hasSessions ? data.metrics.subscores.avg_fillers_per_answer : '—';

        // Calculate rating based on average score
        let rating = 'Beginner';
        if (avgScore >= 85) rating = 'Expert';
        else if (avgScore >= 70) rating = 'Intermediate';
        document.getElementById('stat-rating').textContent = hasSessions ? rating : 'New';

        // Banner + muted metric cards for accounts with no sessions yet
        this.renderEmptyState(hasSessions);

        // Render Recent Activity List
        this.renderRecentActivity(data.history);

        // Render Charts (only radar now - progression chart moved to scoreboard)
        this.renderPerformanceChart(data.metrics.subscores, hasSessions);
    },

    renderEmptyState(hasSessions) {
        const banner = document.getElementById('dashboard-empty-state');
        if (banner) banner.hidden = hasSessions;

        const row = document.querySelector('.metric-row');
        if (row) row.classList.toggle('is-empty', !hasSessions);

        // Re-bind each render so the CTA never gets a duplicate handler.
        const cta = document.getElementById('empty-state-start-btn');
        if (cta) {
            cta.onclick = () => {
                const startBtn = document.getElementById('start-interview-btn');
                if (startBtn) startBtn.click();
            };
        }
    },

    renderRecentActivity(history) {
        const list = document.getElementById('recent-activity-list');
        if (!list) return;
        list.innerHTML = '';

        if (!history || history.length === 0) {
            list.innerHTML = `<div class="activity-item"><div class="activity-title">No practice sessions yet — pick a role above and press <strong>Start Practice Session</strong>.</div></div>`;
            return;
        }

        history.forEach(item => {
            const div = document.createElement('div');
            div.className = 'activity-item';
            div.style.cursor = 'pointer';
            
            let scoreClass = 'low';
            if (item.overall_score >= 80) scoreClass = 'high';
            else if (item.overall_score >= 65) scoreClass = 'mid';

            div.innerHTML = `
                <div class="activity-details">
                    <span class="activity-title">${item.role} Practice</span>
                    <span class="activity-meta">${item.date}</span>
                </div>
                <div class="score-badge ${scoreClass}">${item.overall_score}%</div>
            `;

            div.addEventListener('click', () => {
                app.navigateToInterviewDetail(item.id);
            });

            list.appendChild(div);
        });
    },

    renderPerformanceChart(subscores, hasSessions = true) {
        const canvas = document.getElementById('categoryRadarChart');
        const placeholder = document.getElementById('chart-empty-state');
        const wrapper = canvas ? canvas.parentElement : null;

        if (categoryRadarChart) {
            categoryRadarChart.destroy();
            categoryRadarChart = null;
        }

        // No sessions yet: hide the canvas and show a placeholder rather than
        // drawing an all-zero polygon that looks like a score of nothing.
        if (!hasSessions || !canvas) {
            if (wrapper) wrapper.style.display = 'none';
            if (placeholder) placeholder.hidden = false;
            return;
        }

        if (wrapper) wrapper.style.display = '';
        if (placeholder) placeholder.hidden = true;

        const ctx = canvas.getContext('2d');

        categoryRadarChart = new Chart(ctx, {
            type: 'radar',
            data: {
                labels: ['Clarity', 'Grammar', 'Relevance'],
                datasets: [{
                    label: 'Your Average Skills',
                    data: [subscores.clarity, subscores.grammar, subscores.relevance],
                    backgroundColor: 'rgba(91, 155, 213, 0.22)',
                    borderColor: '#5B9BD5',
                    borderWidth: 2,
                    pointBackgroundColor: '#134074',
                    pointBorderColor: '#5B9BD5',
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    r: {
                        grid: {
                            color: 'rgba(91, 155, 213, 0.22)'
                        },
                        angleLines: {
                            color: 'rgba(91, 155, 213, 0.22)'
                        },
                        pointLabels: {
                            color: '#94a3b8',
                            font: {
                                size: 11,
                                family: 'Plus Jakarta Sans',
                                weight: '600'
                            }
                        },
                        ticks: {
                            display: false,
                            stepSize: 20
                        },
                        min: 0,
                        max: 100
                    }
                }
            }
        });
    },

    renderFallback() {
        const loggedInUser = app.getUser();
        const userName = loggedInUser ? loggedInUser.name : 'Guest Account';
        const userRole = loggedInUser ? (loggedInUser.target_role || 'Software Engineer') : 'Software Engineer';
        const freshData = {
            progression: [],
            metrics: {
                total_interviews: 0,
                average_score: 0,
                subscores: { clarity: 0, grammar: 0, relevance: 0, avg_fillers_per_answer: 0 },
                categories: { "Behavioral": 0, "Technical": 0, "Situational": 0 }
            },
            user: { name: userName, target_role: userRole },
            history: []
        };
        this.render(freshData);
    }
};

// Score Board Module
const scoreboard = {
    scoreProgressChart: null,
    scoreboardCategoryChart: null,

    async load() {
        try {
            const data = await api.getDashboard();
            this.render(data);
        } catch (error) {
            console.error('Error loading scoreboard:', error);
            this.renderFallback();
        } finally {
            app.clearSkeletons();
        }
    },

    render(data) {
        // Update summary stats
        const progression = data.progression || [];
        const scores = progression.map(p => p.score);
        const bestScore = scores.length > 0 ? Math.round(Math.max(...scores)) : 0;
        const avgScore = scores.length > 0 ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0;
        const latestScore = scores.length > 0 ? Math.round(scores[scores.length - 1]) : 0;

        document.getElementById('scoreboard-best').textContent = `${bestScore}%`;
        document.getElementById('scoreboard-avg').textContent = `${avgScore}%`;
        document.getElementById('scoreboard-latest').textContent = `${latestScore}%`;
        document.getElementById('scoreboard-sessions').textContent = data.metrics.total_interviews;

        // Render progression chart
        this.renderProgressionChart(progression);

        // Render category chart (use categories data, not subscores)
        this.renderCategoryChart(data.metrics.categories);

        // Render score history list
        this.renderScoreHistory(data.history);
    },

    renderProgressionChart(progression) {
        const ctx = document.getElementById('scoreProgressChart').getContext('2d');
        
        if (this.scoreProgressChart) {
            this.scoreProgressChart.destroy();
        }

        const labels = progression.map(p => p.date);
        const scores = progression.map(p => p.score);

        if (labels.length === 0) {
            labels.push('Start');
            scores.push(0);
        }

        this.scoreProgressChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Overall Score (%)',
                    data: scores,
                    borderColor: '#6366f1',
                    borderWidth: 3,
                    backgroundColor: 'rgba(99, 102, 241, 0.05)',
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#8b5cf6',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 6,
                    pointHoverRadius: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        titleColor: '#fff',
                        bodyColor: '#e2e8f0',
                        borderColor: 'rgba(255, 255, 255, 0.08)',
                        borderWidth: 1,
                        displayColors: false,
                        padding: 10
                    }
                },
                scales: {
                    y: {
                        min: 0,
                        max: 100,
                        grid: {
                            color: 'rgba(255, 255, 255, 0.04)'
                        },
                        ticks: {
                            color: '#94a3b8',
                            font: { family: 'Plus Jakarta Sans' }
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            color: '#94a3b8',
                            font: { family: 'Plus Jakarta Sans' }
                        }
                    }
                }
            }
        });
    },

    renderCategoryChart(categories) {
        const ctx = document.getElementById('scoreboardCategoryChart').getContext('2d');
        
        if (this.scoreboardCategoryChart) {
            this.scoreboardCategoryChart.destroy();
        }

        // Handle categories data (Behavioral, Technical, Situational) or fallback to subscores
        const chartData = {
            labels: ['Behavioral', 'Technical', 'Situational'],
            values: [
                categories?.Behavioral || 0,
                categories?.Technical || 0,
                categories?.Situational || 0
            ]
        };

        this.scoreboardCategoryChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: chartData.labels,
                datasets: [{
                    data: chartData.values,
                    backgroundColor: ['#6366f1', '#10b981', '#f59e0b'],
                    borderWidth: 0,
                    hoverOffset: 10
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: '#94a3b8',
                            font: { family: 'Plus Jakarta Sans', size: 11 },
                            padding: 12,
                            usePointStyle: true
                        }
                    }
                }
            }
        });
    },

    renderScoreHistory(history) {
        const list = document.getElementById('scoreboard-history-list');
        list.innerHTML = '';

        if (!history || history.length === 0) {
            list.innerHTML = `<div class="activity-item"><div class="activity-title">No scores recorded yet. Complete an interview to see your scores here!</div></div>`;
            return;
        }

        history.forEach(item => {
            const div = document.createElement('div');
            div.className = 'activity-item';
            div.style.cursor = 'pointer';
            
            let scoreClass = 'low';
            if (item.overall_score >= 80) scoreClass = 'high';
            else if (item.overall_score >= 65) scoreClass = 'mid';

            div.innerHTML = `
                <div class="activity-details">
                    <span class="activity-title">${item.role} Practice</span>
                    <span class="activity-meta">${item.date}</span>
                </div>
                <div class="score-badge ${scoreClass}">${item.overall_score}%</div>
            `;

            div.addEventListener('click', () => {
                app.navigateToInterviewDetail(item.id);
            });

            list.appendChild(div);
        });
    },

    renderFallback() {
        const loggedInUser = app.getUser();
        const userName = loggedInUser ? loggedInUser.name : 'Guest Account';
        const userRole = loggedInUser ? (loggedInUser.target_role || 'Software Engineer') : 'Software Engineer';
        const freshData = {
            progression: [],
            metrics: {
                total_interviews: 0,
                average_score: 0,
                subscores: { clarity: 0, grammar: 0, relevance: 0, avg_fillers_per_answer: 0 },
                categories: { "Behavioral": 0, "Technical": 0, "Situational": 0 }
            },
            user: { name: userName, target_role: userRole },
            history: []
        };
        this.render(freshData);
    }
};