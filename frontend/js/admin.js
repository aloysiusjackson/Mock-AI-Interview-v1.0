/**
 * Admin Panel JavaScript
 * Handles admin dashboard, user management, and activity monitoring
 */

const admin = {
    currentPage: 1,
    currentSection: 'dashboard',
    currentUserId: null,
    userPage: 1,
    activityPage: 1,
    activityTypes: [],
    userSearch: '',
    userStatusFilter: '',
    userRoleFilter: '',
    activityTypeFilter: '',
    userSearchTimeout: null,

    init() {
        this.checkAuth();
        this.setupNavigation();
        this.setupEventListeners();
        this.loadDashboard();

        // Check if user is admin, redirect if not
        setTimeout(() => this.checkAdminAccess(), 500);
    },

    checkAuth() {
        // Check if user is logged in
        fetch('/api/auth/me', { credentials: 'include' })
            .then(res => {
                if (!res.ok) {
                    window.location.href = '/admin/login.html';
                    return;
                }
                return res.json();
            })
            .then(data => {
                if (!data || !data.role) {
                    window.location.href = '/admin/login.html';
                }
            })
            .catch(() => {
                window.location.href = '/admin/login.html';
            });
    },

    checkAdminAccess() {
        fetch('/api/admin/stats', { credentials: 'include' })
            .then(res => {
                if (res.status === 403) {
                    this.showToast('Access Denied: Admin privileges required', 'error');
                    setTimeout(() => window.location.href = '/admin/login.html', 2000);
                } else if (!res.ok) {
                    this.showToast('Session expired. Please login again.', 'error');
                    setTimeout(() => window.location.href = '/admin/login.html', 2000);
                }
            })
            .catch(() => {
                this.showToast('Connection error', 'error');
            });
    },

    setupNavigation() {
        document.querySelectorAll('.admin-nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const section = item.dataset.section;
                this.switchSection(section);
            });
        });
    },

    setupEventListeners() {
        // User search with debounce
        const userSearch = document.getElementById('user-search');
        if (userSearch) {
            userSearch.addEventListener('input', () => {
                clearTimeout(this.userSearchTimeout);
                this.userSearchTimeout = setTimeout(() => {
                    this.userPage = 1;
                    this.userSearch = userSearch.value;
                    this.loadUsers();
                }, 300);
            });
        }

        // User status filter
        const userStatusFilter = document.getElementById('user-status-filter');
        if (userStatusFilter) {
            userStatusFilter.addEventListener('change', () => {
                this.userStatusFilter = userStatusFilter.value;
                this.userPage = 1;
                this.loadUsers();
            });
        }

        // User role filter
        const userRoleFilter = document.getElementById('user-role-filter');
        if (userRoleFilter) {
            userRoleFilter.addEventListener('change', () => {
                this.userRoleFilter = userRoleFilter.value;
                this.userPage = 1;
                this.loadUsers();
            });
        }

        // Activity type filter
        const activityTypeFilter = document.getElementById('activity-type-filter');
        if (activityTypeFilter) {
            activityTypeFilter.addEventListener('change', () => {
                this.activityTypeFilter = activityTypeFilter.value;
                this.activityPage = 1;
                this.loadActivity();
            });
        }

        // Activity search
        const activitySearch = document.getElementById('activity-search');
        if (activitySearch) {
            activitySearch.addEventListener('input', () => {
                clearTimeout(this.userSearchTimeout);
                this.userSearchTimeout = setTimeout(() => {
                    this.activityPage = 1;
                    this.loadActivity();
                }, 300);
            });
        }
    },

    switchSection(section) {
        this.currentSection = section;
        this.currentUserId = null;

        // Update nav
        document.querySelectorAll('.admin-nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.section === section);
        });

        // Update sections
        document.querySelectorAll('.admin-section').forEach(sectionEl => {
            sectionEl.classList.remove('active');
        });
        document.getElementById(`section-${section}`).classList.add('active');

        // Update header
        const titles = {
            dashboard: { title: 'Dashboard', desc: 'Overview of your platform activity' },
            users: { title: 'User Management', desc: 'View and manage all registered users' },
            activity: { title: 'Activity Log', desc: 'Monitor user actions and system events' },
            interviews: { title: 'Interviews', desc: 'View and manage all interview sessions' },
            qa: { title: 'Questions & Answers', desc: 'Browse all questions, answers and AI feedback' },
            errors: { title: 'Error Log', desc: 'View all errors and failed activities' },
            system: { title: 'System Health', desc: 'Real-time server status and activity metrics' }
        };
        const header = titles[section] || { title: section, desc: '' };
        document.getElementById('admin-section-title').textContent = header.title;
        document.getElementById('admin-section-desc').textContent = header.desc;

        // Load section data
        if (section === 'dashboard') this.loadDashboard();
        else if (section === 'users') this.loadUsers();
        else if (section === 'activity') this.loadActivityTypes();
        else if (section === 'errors') this.loadErrors();
        else if (section === 'system') this.loadSystemHealth();
        else if (section === 'interviews') this.loadInterviews();
        else if (section === 'qa') this.loadQAList();
    },

    // ─────────────────────────────────────────────────────────────────────
    // DASHBOARD
    // ─────────────────────────────────────────────────────────────────────

    async loadDashboard() {
        try {
            const res = await fetch('/api/admin/stats', { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load stats');
            const data = await res.json();

            const stats = data.stats;
            this.updateStat('stat-total-users', stats.total_users);
            this.updateStat('stat-active-users', stats.active_users);
            this.updateStat('stat-new-today', stats.new_users_today);
            this.updateStat('stat-total-interviews', stats.total_interviews);
            this.updateStat('stat-failed-logins', stats.failed_logins_24h);
            this.updateStat('stat-completed', stats.completed_interviews);

            // Load charts
            this.loadRoleDistributionChart(data.role_distribution);
            this.loadTopRolesChart(data.top_roles);

            // Load recent activity
            this.renderRecentActivity(data.recent_activity);
        } catch (error) {
            console.error('Dashboard load error:', error);
            this.showToast('Failed to load dashboard data', 'error');
        }
    },

    updateStat(elementId, value) {
        const el = document.getElementById(elementId);
        if (el) {
            el.textContent = value.toLocaleString();
        }
    },

    loadRoleDistributionChart(data) {
        const canvas = document.getElementById('roleDistributionChart');
        if (!canvas) return;

        // Destroy existing chart if any
        if (this.roleChart) this.roleChart.destroy();

        const ctx = canvas.getContext('2d');
        const labels = Object.keys(data).length ? Object.keys(data) : ['No Data'];
        const values = Object.values(data).length ? Object.values(data) : [0];
        const colors = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6'];

        this.roleChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors.slice(0, labels.length),
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 16,
                            usePointStyle: true,
                            pointStyle: 'circle',
                            font: { size: 12 }
                        }
                    }
                },
                cutout: '70%'
            }
        });
    },

    loadTopRolesChart(data) {
        const canvas = document.getElementById('topRolesChart');
        if (!canvas) return;

        if (this.topRolesChart) this.topRolesChart.destroy();

        const ctx = canvas.getContext('2d');

        if (!data || data.length === 0) {
            data = [{ role: 'No Data', count: 0, avg_score: 0 }];
        }

        const labels = data.map(d => d.role);
        const values = data.map(d => d.count);
        const colors = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6'];

        this.topRolesChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Interviews',
                    data: values,
                    backgroundColor: colors.slice(0, labels.length),
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1,
                            font: { size: 11 }
                        }
                    },
                    x: {
                        ticks: {
                            font: { size: 11 }
                        }
                    }
                }
            }
        });
    },

    renderRecentActivity(activities) {
        const container = document.getElementById('recent-activity-list');
        if (!container) return;

        if (!activities || activities.length === 0) {
            container.innerHTML = '<div class="activity-empty">No recent activity</div>';
            return;
        }

        container.innerHTML = activities.map(activity => this.formatActivityItem(activity)).join('');
    },

    formatActivityItem(activity) {
        const iconMap = {
            'login': { icon: '🔑', type: 'success' },
            'logout': { icon: '🚪', type: 'info' },
            'registration': { icon: '📝', type: 'success' },
            'failed_login': { icon: '❌', type: 'failed' },
            'interview_submitted': { icon: '🎯', type: 'success' },
            'user_modified': { icon: '✏️', type: 'warning' },
            'profile_updated': { icon: '👤', type: 'info' },
            'password_reset': { icon: '🔒', type: 'warning' },
            'email_verified': { icon: '✅', type: 'success' },
            'email_verification_requested': { icon: '📧', type: 'info' },
        };

        const info = iconMap[activity.activity_type] || { icon: '📊', type: 'info' };
        const time = this.formatTime(activity.created_at);
        const user = activity.user_name || 'System';

        return `
            <div class="activity-item">
                <div class="activity-icon ${info.type}">${info.icon}</div>
                <div class="activity-content">
                    <div class="activity-description">${this.escapeHtml(activity.description || '')}</div>
                    <div class="activity-meta">
                        <span class="activity-time">${time}</span>
                        <span class="activity-user">${this.escapeHtml(user)}${activity.user_email ? ` <${this.escapeHtml(activity.user_email)}>` : ''}</span>
                        ${activity.status === 'failed' ? '<span style="color: #ef4444;">Failed</span>' : ''}
                    </div>
                </div>
            </div>
        `;
    },

    // SQLite writes CURRENT_TIMESTAMP / datetime('now') as a UTC "YYYY-MM-DD HH:MM:SS"
    // string with no zone marker. `new Date()` would read that as local time, which
    // shifted every relative timestamp by the UTC offset. Anchor it to UTC instead.
    parseDbTime(value) {
        if (value instanceof Date) return value;
        const s = String(value || '');
        if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(s)) {
            return new Date(s.replace(' ', 'T') + 'Z');
        }
        return new Date(s);
    },

    formatTime(dateStr) {
        if (!dateStr) return '';
        const date = this.parseDbTime(dateStr);
        if (isNaN(date.getTime())) return '';
        const now = new Date();
        const diff = now - date;
        const mins = Math.floor(diff / 60000);
        const hours = Math.floor(diff / 3600000);
        const days = Math.floor(diff / 86400000);

        if (mins < 1) return 'Just now';
        if (mins < 60) return `${mins}m ago`;
        if (hours < 24) return `${hours}h ago`;
        if (days < 7) return `${days}d ago`;
        return date.toLocaleDateString();
    },

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    // ─────────────────────────────────────────────────────────────────────
    // USERS
    // ─────────────────────────────────────────────────────────────────────

    async loadUsers() {
        const tbody = document.getElementById('users-table-body');
        const pagination = document.getElementById('users-pagination');
        if (!tbody) return;

        tbody.innerHTML = '<tr><td colspan="8" class="table-loading">Loading users...</td></tr>';

        try {
            const params = new URLSearchParams({
                page: this.userPage,
                per_page: 20
            });
            if (this.userSearch) params.set('search', this.userSearch);
            if (this.userStatusFilter) params.set('status', this.userStatusFilter);
            if (this.userRoleFilter && this.userRoleFilter !== 'all') params.set('role', this.userRoleFilter);

            const res = await fetch(`/api/admin/users?${params}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load users');
            const data = await res.json();

            if (data.users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="table-loading">No users found</td></tr>';
            } else {
                tbody.innerHTML = data.users.map(user => this.formatUserRow(user)).join('');
            }

            this.renderPagination(pagination, data.pagination, (page) => {
                this.userPage = page;
                this.loadUsers();
            });
        } catch (error) {
            console.error('Users load error:', error);
            tbody.innerHTML = '<tr><td colspan="8" class="table-loading">Failed to load users</td></tr>';
            this.showToast('Failed to load users', 'error');
        }
    },

    formatUserRow(user) {
        const initial = (user.name || '?')[0].toUpperCase();
        const roleClass = user.role === 'admin' ? 'admin' : 'user';
        const statusClass = user.status;

        return `
            <tr>
                <td>
                    <div class="user-cell">
                        <div class="user-avatar-sm">${initial}</div>
                        <div>
                            <div class="user-name">${this.escapeHtml(user.name)}</div>
                        </div>
                    </div>
                </td>
                <td class="user-email">${this.escapeHtml(user.email)}</td>
                <td><span class="role-badge ${roleClass}">${this.escapeHtml(user.role)}</span></td>
                <td><span class="status-badge ${statusClass}">${user.status}</span></td>
                <td style="white-space: nowrap; color: var(--admin-text-secondary);">${this.formatDate(user.created_at)}</td>
                <td style="white-space: nowrap; color: var(--admin-text-secondary);">${this.formatDate(user.last_login) || 'Never'}</td>
                <td>${user.interview_count}</td>
                <td>
                    <button class="action-btn view" onclick="admin.viewUserDetail(${user.id})">View</button>
                    ${user.interview_blocked ? `<button class="action-btn edit" onclick="admin.unblockUser(${user.id})" title="Cheat strikes: ${user.cheat_strikes}">Unblock</button>` : ''}
                    ${user.role !== 'admin' ? `<button class="action-btn edit" onclick="admin.toggleUserStatus(${user.id}, '${user.status}')">${user.status === 'active' ? 'Suspend' : 'Activate'}</button>` : ''}
                </td>
            </tr>
        `;
    },

    formatDate(dateStr) {
        if (!dateStr) return '';
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    },

    renderPagination(container, pagination, onPageChange) {
        if (!container) return;

        const { page, per_page, total_count, total_pages } = pagination;

        let html = `<span class="pagination-info">Showing ${(page - 1) * per_page + 1}-${Math.min(page * per_page, total_count)} of ${total_count.toLocaleString()}</span>`;

        html += `<button class="pagination-btn" onclick="admin.goToPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>← Prev</button>`;

        for (let i = Math.max(1, page - 2); i <= Math.min(total_pages, page + 2); i++) {
            html += `<button class="pagination-btn ${i === page ? 'active' : ''}" onclick="admin.goToPage(${i})">${i}</button>`;
        }

        html += `<button class="pagination-btn" onclick="admin.goToPage(${page + 1})" ${page >= total_pages ? 'disabled' : ''}>Next →</button>`;

        container.innerHTML = html;
    },

    goToPage(page) {
        if (this.currentSection === 'users') {
            this.userPage = page;
            this.loadUsers();
        } else if (this.currentSection === 'activity') {
            this.activityPage = page;
            this.loadActivity();
        }
    },

    async viewUserDetail(userId) {
        this.currentUserId = userId;
        const card = document.getElementById('user-detail-card');
        const body = document.getElementById('user-detail-body');
        if (!card || !body) return;

        card.style.display = 'block';
        body.innerHTML = '<div class="activity-loading">Loading user details...</div>';

        try {
            const res = await fetch(`/api/admin/users/${userId}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load user');
            const data = await res.json();

            body.innerHTML = this.renderUserDetail(data);
        } catch (error) {
            console.error('User detail error:', error);
            body.innerHTML = '<div class="activity-empty">Failed to load user details</div>';
            this.showToast('Failed to load user details', 'error');
        }
    },

    renderUserDetail(data) {
        const user = data.user;
        const initial = (user.name || '?')[0].toUpperCase();
        const statusClass = user.status;

        let interviewsHtml = '';
        if (data.interviews && data.interviews.length > 0) {
            interviewsHtml = data.interviews.map(i => `
                <div style="padding: 10px; background: var(--admin-bg); border-radius: var(--admin-radius); margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                        <span style="font-weight: 500;">${this.escapeHtml(i.role)}</span>
                        <span style="color: var(--admin-text-secondary); font-size: 0.85rem;">${this.formatDate(i.date)}</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 0.85rem; color: var(--admin-text-secondary);">Score:</span>
                        <span style="font-weight: 600; color: ${i.overall_score >= 70 ? '#10b981' : '#f59e0b'};">${i.overall_score.toFixed(1)}%</span>
                    </div>
                </div>
            `).join('');
        } else {
            interviewsHtml = '<p style="color: var(--admin-text-secondary); font-style: italic;">No interviews yet</p>';
        }

        let activityHtml = '';
        if (data.activity && data.activity.length > 0) {
            activityHtml = data.activity.slice(0, 10).map(a => `
                <div style="padding: 8px 0; border-bottom: 1px solid var(--admin-border); font-size: 0.85rem;">
                    <div style="display: flex; justify-content: space-between;">
                        <span>${this.escapeHtml(a.activity_type.replace(/_/g, ' '))}</span>
                        <span style="color: var(--admin-text-secondary);">${this.formatTime(a.created_at)}</span>
                    </div>
                    <div style="color: var(--admin-text-secondary); margin-top: 2px;">${this.escapeHtml(a.description || '')}</div>
                </div>
            `).join('');
        } else {
            activityHtml = '<p style="color: var(--admin-text-secondary); font-style: italic;">No activity recorded</p>';
        }

        return `
            <div class="user-detail-header">
                <div class="user-detail-avatar">${initial}</div>
                <div>
                    <div class="user-detail-name">${this.escapeHtml(user.name)}</div>
                    <div class="user-detail-email">${this.escapeHtml(user.email)}</div>
                </div>
            </div>
            <div class="user-detail-info-grid">
                <div class="user-detail-item">
                    <div class="user-detail-item-label">User ID</div>
                    <div class="user-detail-item-value">#${user.id}</div>
                </div>
                <div class="user-detail-item">
                    <div class="user-detail-item-label">Role</div>
                    <div class="user-detail-item-value"><span class="role-badge ${user.role === 'admin' ? 'admin' : 'user'}">${this.escapeHtml(user.role)}</span></div>
                </div>
                <div class="user-detail-item">
                    <div class="user-detail-item-label">Status</div>
                    <div class="user-detail-item-value"><span class="status-badge ${statusClass}">${user.status}</span></div>
                </div>
                <div class="user-detail-item">
                    <div class="user-detail-item-label">Joined</div>
                    <div class="user-detail-item-value">${this.formatDate(user.created_at)}</div>
                </div>
                <div class="user-detail-item">
                    <div class="user-detail-item-label">Last Login</div>
                    <div class="user-detail-item-value">${this.formatDate(user.last_login) || 'Never'}</div>
                </div>
                <div class="user-detail-item">
                    <div class="user-detail-item-label">Interviews</div>
                    <div class="user-detail-item-value">${data.interviews ? data.interviews.length : 0}</div>
                </div>
            </div>
            <div>
                <h4 style="margin: 0 0 12px; font-size: 1rem;">Recent Interviews</h4>
                ${interviewsHtml}
            </div>
            <div style="margin-top: 24px;">
                <h4 style="margin: 0 0 12px; font-size: 1rem;">Activity</h4>
                ${activityHtml}
            </div>
            <div class="user-detail-actions">
                ${user.role !== 'admin' ? `
                    <button class="btn btn-primary" onclick="admin.promoteToAdmin(${user.id})">Make Admin</button>
                    <button class="btn btn-outline" onclick="admin.suspendUser(${user.id})">${user.status === 'suspended' ? 'Reactivate' : 'Suspend'}</button>
                ` : `
                    <button class="btn btn-danger" onclick="admin.demoteFromAdmin(${user.id})" style="width: 100%;">Remove Admin Access</button>
                `}
                <button class="btn btn-outline" onclick="admin.closeUserDetail()" style="margin-left: auto;">Close</button>
            </div>
        `;
    },

    closeUserDetail() {
        this.currentUserId = null;
        const card = document.getElementById('user-detail-card');
        if (card) card.style.display = 'none';
    },

    async unblockUser(userId) {
        if (!confirm('Clear all anti-cheat violations and allow this user to take interviews again?')) return;

        try {
            const res = await fetch(`/api/admin/users/${userId}`, {
                method: 'PATCH',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ unblock: true })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.error || 'Failed to unblock user');
            }

            this.showToast('User unblocked — strikes reset to 0', 'success');
            this.loadUsers();
        } catch (error) {
            this.showToast(error.message, 'error');
        }
    },

    async toggleUserStatus(userId, currentStatus) {
        const newStatus = currentStatus === 'active' ? 'suspended' : 'active';
        const btnText = currentStatus === 'active' ? 'Suspend' : 'Activate';

        if (!confirm(`Are you sure you want to ${newStatus} this user?`)) return;

        try {
            const res = await fetch(`/api/admin/users/${userId}`, {
                method: 'PATCH',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_verified: newStatus === 'active' })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.error || 'Failed to update user');
            }

            this.showToast(`User ${newStatus} successfully`, 'success');
            this.loadUsers();
            this.loadDashboard();
        } catch (error) {
            this.showToast(error.message, 'error');
        }
    },

    async promoteToAdmin(userId) {
        if (!confirm('Are you sure you want to make this user an administrator? This grants full access to the admin panel.')) return;

        try {
            const res = await fetch(`/api/admin/users/${userId}`, {
                method: 'PATCH',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role: 'admin' })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.error || 'Failed to update user');
            }

            this.showToast('User promoted to admin', 'success');
            this.closeUserDetail();
            this.loadUsers();
            this.loadDashboard();
        } catch (error) {
            this.showToast(error.message, 'error');
        }
    },

    async demoteFromAdmin(userId) {
        if (!confirm('Are you sure you want to remove this user\'s admin access?')) return;

        try {
            const res = await fetch(`/api/admin/users/${userId}`, {
                method: 'PATCH',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role: 'user' })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.error || 'Failed to update user');
            }

            this.showToast('Admin access removed', 'success');
            this.closeUserDetail();
            this.loadUsers();
            this.loadDashboard();
        } catch (error) {
            this.showToast(error.message, 'error');
        }
    },

    async suspendUser(userId) {
        // Already handled by toggleUserStatus
        this.toggleUserStatus(userId, 'active');
    },

    // ─────────────────────────────────────────────────────────────────────
    // INTERVIEWS
    // ─────────────────────────────────────────────────────────────────────

    async loadInterviews() {
        const tbody = document.getElementById('interviews-table-body');
        const pagination = document.getElementById('interviews-pagination');
        if (!tbody) return;

        tbody.innerHTML = '<tr><td colspan="8" class="table-loading">Loading interviews...</td></tr>';

        try {
            const params = new URLSearchParams({
                page: this.userPage,
                per_page: 20
            });
            const search = document.getElementById('interview-search');
            if (search) params.set('search', search.value);
            const statusFilter = document.getElementById('interview-status-filter');
            if (statusFilter && statusFilter.value) params.set('status', statusFilter.value);

            const res = await fetch(`/api/admin/interviews?${params}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load interviews');
            const data = await res.json();

            if (data.interviews.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="table-loading">No interviews found</td></tr>';
            } else {
                tbody.innerHTML = data.interviews.map(interview => this.formatInterviewRow(interview)).join('');
            }

            this.renderPagination(pagination, data.pagination, (page) => {
                this.userPage = page;
                this.loadInterviews();
            });
        } catch (error) {
            console.error('Interviews load error:', error);
            tbody.innerHTML = '<tr><td colspan="8" class="table-loading">Failed to load interviews</td></tr>';
            this.showToast('Failed to load interviews', 'error');
        }
    },

    formatInterviewRow(interview) {
        const scoreClass = interview.overall_score === null ? 'na' :
            interview.overall_score >= 80 ? 'high' :
            interview.overall_score >= 70 ? 'medium' : 'low';

        const scoreDisplay = interview.overall_score !== null ? `${interview.overall_score}%` : '—';

        return `
            <tr>
                <td>#${interview.id}</td>
                <td>
                    <div class="user-cell">
                        <div class="user-avatar-sm">${(interview.user.name || '?')[0].toUpperCase()}</div>
                        <div>
                            <div class="user-name">${this.escapeHtml(interview.user.name)}</div>
                        </div>
                    </div>
                </td>
                <td>${this.escapeHtml(interview.role)}</td>
                <td><span class="interview-status-badge ${interview.status}">${interview.status.replace('_', ' ')}</span></td>
                <td><span class="score-badge ${scoreClass}">${scoreDisplay}</span></td>
                <td>${interview.answer_count}</td>
                <td style="white-space: nowrap; color: var(--admin-text-secondary);">${this.formatDate(interview.created_at)}</td>
                <td>
                    <button class="action-btn view" onclick="admin.viewInterviewDetail(${interview.id})">View</button>
                </td>
            </tr>
        `;
    },

    async viewInterviewDetail(interviewId) {
        const card = document.getElementById('interview-detail-card');
        const body = document.getElementById('interview-detail-body');
        if (!card || !body) return;

        card.style.display = 'block';
        body.innerHTML = '<div class="activity-loading">Loading interview details...</div>';

        try {
            const res = await fetch(`/api/admin/interviews/${interviewId}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load interview');
            const data = await res.json();

            body.innerHTML = this.renderInterviewDetail(data);
        } catch (error) {
            console.error('Interview detail error:', error);
            body.innerHTML = '<div class="activity-empty">Failed to load interview details</div>';
            this.showToast('Failed to load interview details', 'error');
        }
    },

    renderInterviewDetail(data) {
        const interview = data.interview;
        const scoreClass = interview.overall_score === null ? 'na' :
            interview.overall_score >= 80 ? 'high' :
            interview.overall_score >= 70 ? 'medium' : 'low';

        const answersHtml = data.answers.length > 0 ? data.answers.map(answer => this.renderAnswerBlock(answer)).join('') : '<div class="qa-empty">No answers recorded</div>';

        return `
            <div class="interview-detail-header">
                <div style="font-size: 2rem;">📋</div>
                <div>
                    <div style="font-size: 1.25rem; font-weight: 700;">Interview #${interview.id}</div>
                    <div style="color: var(--admin-text-secondary);">${this.escapeHtml(interview.role)}</div>
                </div>
            </div>

            <div class="interview-detail-info">
                <div class="interview-detail-stat">
                    <div class="interview-detail-stat-value">${interview.user.name}</div>
                    <div class="interview-detail-stat-label">User</div>
                </div>
                <div class="interview-detail-stat">
                    <div class="interview-detail-stat-value">${interview.user.email}</div>
                    <div class="interview-detail-stat-label">Email</div>
                </div>
                <div class="interview-detail-stat">
                    <div class="interview-detail-stat-value" style="color: ${interview.overall_score !== null ? '#6366f1' : '#94a3b8'};">${interview.overall_score !== null ? interview.overall_score + '%' : 'In Progress'}</div>
                    <div class="interview-detail-stat-label">Score</div>
                </div>
                <div class="interview-detail-stat">
                    <div class="interview-detail-stat-value">${interview.answer_count}</div>
                    <div class="interview-detail-stat-label">Answers</div>
                </div>
                <div class="interview-detail-stat">
                    <div class="interview-detail-stat-value">${this.formatDate(interview.created_at)}</div>
                    <div class="interview-detail-stat-label">Date</div>
                </div>
            </div>

            <div style="margin-bottom: 20px;">
                <h4 style="margin: 0 0 12px; font-size: 1rem;">Summary</h4>
                <p style="color: var(--admin-text-secondary); line-height: 1.6;">${interview.summary || 'No summary available'}</p>
            </div>

            <h4 style="margin: 0 0 16px; font-size: 1rem;">Answers (${data.answers.length})</h4>
            ${answersHtml}
        `;
    },

    renderAnswerBlock(answer) {
        const scoreClass = answer.score === null ? 'na' :
            answer.score >= 80 ? 'high' :
            answer.score >= 70 ? 'medium' : 'low';

        const feedbackItems = answer.feedback && (answer.feedback.strengths || answer.feedback.tips) ?
            [...(answer.feedback.strengths || []), ...(answer.feedback.tips || [])].map(f => `<div class="qa-feedback-item">${this.escapeHtml(f)}</div>`).join('') : '<div style="color: var(--admin-text-secondary); font-style: italic;">No feedback available</div>';

        return `
            <div class="qaanswer-block">
                <div class="qa-question-text">${this.escapeHtml(answer.question_text)}</div>
                <div class="qa-meta-row">
                    <span class="qa-meta-tag category">${this.escapeHtml(answer.category)}</span>
                    <span class="qa-meta-tag difficulty-${answer.difficulty.toLowerCase()}">${this.escapeHtml(answer.difficulty)}</span>
                </div>
                <div class="qa-transcript">
                    <strong>Your Answer:</strong>
                    <div style="margin-top: 4px;">${this.escapeHtml(answer.transcript || '(No answer provided)')}</div>
                </div>
                <div class="qa-score-display">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 20px; height: 20px;"><path d="M12 20h9M3 20v-8c0-2.2 1.8-4 4-4h10c2.2 0 4 1.8 4 4v8M3 12a9 9 0 0 1 18 0"/></svg>
                    Score: ${answer.score !== null ? answer.score + '%' : 'N/A'}
                </div>
                <div class="qa-feedback-section">
                    <div class="qa-feedback-title">AI Feedback</div>
                    ${feedbackItems}
                </div>
            </div>
        `;
    },

    // ─────────────────────────────────────────────────────────────────────
    // Q&A LIST
    // ─────────────────────────────────────────────────────────────────────

    async loadQAList() {
        const container = document.getElementById('qa-list');
        const pagination = document.getElementById('qa-pagination');
        if (!container) return;

        container.innerHTML = '<div class="activity-loading">Loading Q&A...</div>';

        try {
            const params = new URLSearchParams({
                page: this.userPage,
                per_page: 20
            });
            const search = document.getElementById('qa-search');
            if (search) params.set('search', search.value);
            const categoryFilter = document.getElementById('qa-category-filter');
            if (categoryFilter && categoryFilter.value !== 'all') params.set('category', categoryFilter.value);
            const difficultyFilter = document.getElementById('qa-difficulty-filter');
            if (difficultyFilter && difficultyFilter.value !== 'all') params.set('difficulty', difficultyFilter.value);

            const res = await fetch(`/api/admin/questions?${params}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load Q&A');
            const data = await res.json();

            if (data.qa_list.length === 0) {
                container.innerHTML = '<div class="qa-empty">No Q&A records found</div>';
            } else {
                container.innerHTML = data.qa_list.map(qa => this.formatQARow(qa)).join('');
            }

            this.renderPagination(pagination, data.pagination, (page) => {
                this.userPage = page;
                this.loadQAList();
            });
        } catch (error) {
            console.error('Q&A load error:', error);
            container.innerHTML = '<div class="qa-empty">Failed to load Q&A</div>';
            this.showToast('Failed to load Q&A', 'error');
        }
    },

    formatQARow(qa) {
        const scoreClass = qa.score === null ? 'na' :
            qa.score >= 80 ? 'high' :
            qa.score >= 70 ? 'medium' : 'low';

        const scoreDisplay = qa.score !== null ? qa.score + '%' : 'N/A';

        return `
            <div class="qa-item">
                <div class="qa-item-header">
                    <div class="qa-item-question">${this.escapeHtml(qa.question_text.substring(0, 100))}${qa.question_text.length > 100 ? '...' : ''}</div>
                    <div class="qa-item-score ${scoreClass}">${scoreDisplay}</div>
                </div>
                <div class="qa-item-meta">
                    <span class="qa-meta-tag category">${this.escapeHtml(qa.category)}</span>
                    <span class="qa-meta-tag difficulty-${qa.difficulty.toLowerCase()}">${this.escapeHtml(qa.difficulty)}</span>
                    ${qa.user ? `<span class="qa-item-user">By: ${this.escapeHtml(qa.user.name)}</span>` : '<span class="qa-item-user">Guest</span>'}
                    ${qa.interview ? `<span class="qa-item-interview">Interview #${qa.interview.id}</span>` : '<span class="qa-item-interview">No Interview</span>'}
                </div>
                <div class="qa-item-transcript">${this.escapeHtml(qa.transcript ? qa.transcript.substring(0, 150) + (qa.transcript.length > 150 ? '...' : '') : '(No answer)')}</div>
                ${qa.feedback && qa.feedback.strengths ? `<div class="qa-item-feedback-preview">Strengths: ${qa.feedback.strengths.slice(0, 2).join(', ')}${qa.feedback.strengths.length > 2 ? '...' : ''}</div>` : ''}
            </div>
        `;
    },

    closeInterviewDetail() {
        const card = document.getElementById('interview-detail-card');
        if (card) card.style.display = 'none';
    },

    async resetAllUsers() {
        if (!confirm('WARNING: This will DELETE ALL user accounts and their interviews. The admin account will be preserved. Are you sure?')) return;
        if (!confirm('This action cannot be undone. All user data will be permanently lost. Continue?')) return;

        try {
            const res = await fetch('/api/admin/reset-users', { method: 'POST', credentials: 'include' });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.error || 'Failed to reset users');
            }
            const data = await res.json();
            this.showToast(`Reset complete: ${data.deleted_users} users deleted, ${data.deleted_interviews} interviews removed`, 'success');
            this.loadDashboard();
            this.loadUsers();
        } catch (error) {
            this.showToast(error.message, 'error');
        }
    },

    async resetAllData() {
        if (!confirm('WARNING: This will DELETE EVERYTHING — all users, all interviews, all answers, all activity logs. Even the admin account will be deleted. Are you sure?')) return;
        if (!confirm('This will completely wipe the database. The admin account will be lost and you will need to create a new one. Continue?')) return;
        if (!confirm('FINAL WARNING: Click OK to permanently delete ALL data. This cannot be undone.')) return;

        try {
            const res = await fetch('/api/admin/reset-all', { method: 'POST', credentials: 'include' });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.error || 'Failed to reset all data');
            }
            this.showToast('All data has been reset. Redirecting to login...', 'success');
            setTimeout(() => { window.location.href = '/login.html'; }, 2000);
        } catch (error) {
            this.showToast(error.message, 'error');
        }
    },

    // ─────────────────────────────────────────────────────────────────────
    // ERRORS LOG
    // ─────────────────────────────────────────────────────────────────────

    async loadErrors() {
        const container = document.getElementById('errors-list');
        const pagination = document.getElementById('errors-pagination');
        const typeFilter = document.getElementById('error-type-filter');
        if (!container) return;

        container.innerHTML = '<div class="activity-loading">Loading errors...</div>';

        try {
            const params = new URLSearchParams({
                page: this.activityPage,
                per_page: 50
            });
            if (this.activityTypeFilter) params.set('type', this.activityTypeFilter);

            const res = await fetch(`/api/admin/errors?${params}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load errors');
            const data = await res.json();

            // Update error type filter options
            if (typeFilter && data.pagination.total_count > 0) {
                const types = new Set(data.errors.map(e => e.activity_type));
                types.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t;
                    opt.textContent = t.replace(/_/g, ' ');
                    typeFilter.appendChild(opt);
                });
            }

            // Update error count badge
            const badge = document.getElementById('error-count-badge');
            if (badge) badge.textContent = `${data.pagination.total_count} errors`;

            if (data.errors.length === 0) {
                container.innerHTML = '<div class="activity-empty">No errors found</div>';
            } else {
                container.innerHTML = data.errors.map(error => this.formatErrorItem(error)).join('');
            }

            this.renderPagination(pagination, data.pagination, (page) => {
                this.activityPage = page;
                this.loadErrors();
            });
        } catch (error) {
            console.error('Errors load error:', error);
            container.innerHTML = '<div class="activity-empty">Failed to load errors</div>';
            this.showToast('Failed to load errors', 'error');
        }
    },

    formatErrorItem(error) {
        const time = this.formatTime(error.created_at);
        const user = error.user_name || 'System';
        const typeDisplay = error.activity_type.replace(/_/g, ' ');

        return `
            <div class="error-item">
                <div class="error-icon">⚠️</div>
                <div class="error-content">
                    <div class="error-description">${this.escapeHtml(error.description || '(No description)')}</div>
                    <div class="error-meta">
                        <span class="error-type-badge">${this.escapeHtml(typeDisplay)}</span>
                        <span class="activity-time">${time}</span>
                        <span class="activity-user">${this.escapeHtml(user)}</span>
                        ${error.user_email ? `<span style="color: var(--admin-text-secondary);">${this.escapeHtml(error.user_email)}</span>` : ''}
                        ${error.ip_address ? `<span>IP: ${this.escapeHtml(error.ip_address)}</span>` : ''}
                    </div>
                    ${error.details ? `<div style="margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--admin-border); font-size: 0.8rem; color: var(--admin-text-secondary);">Details: ${this.escapeHtml(error.details)}</div>` : ''}
                </div>
            </div>
        `;
    },

    // ─────────────────────────────────────────────────────────────────────
    // SYSTEM HEALTH
    // ─────────────────────────────────────────────────────────────────────

    async loadSystemHealth() {
        try {
            const res = await fetch('/api/admin/system-health', { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load system health');
            const data = await res.json();

            const health = data.health;

            // Update stat cards
            this.updateStat('sys-uptime', health.server_uptime_formatted);
            this.updateStat('sys-success-rate', health.success_rate_today + '%');
            this.updateStat('sys-errors-24h', health.errors_24h);

            // Update health grid
            this.updateStat('health-new-users-today', health.new_users_today);
            this.updateStat('health-active-users', health.active_users_24h);
            this.updateStat('health-total-users', health.total_users);
            this.updateStat('health-interviews-today', health.interviews_today);
            this.updateStat('health-total-interviews', health.total_interviews);
            this.updateStat('health-completed', health.completed_interviews);
            this.updateStat('health-failed-logins', health.failed_logins_today);
            this.updateStat('health-failed-logins-total', health.failed_logins_total);
            this.updateStat('health-errors-total', health.errors_total);

            // Update error icon color based on error count
            const errorsIcon = document.getElementById('sys-errors-icon');
            if (errorsIcon) {
                if (health.errors_24h > 0) {
                    errorsIcon.className = 'stat-icon stat-icon-red';
                } else {
                    errorsIcon.className = 'stat-icon stat-icon-green';
                }
            }

            // Load daily trend chart
            this.loadDailyTrendChart(data.daily_stats);

            // Load recent errors
            this.renderRecentErrors(data.recent_errors);
        } catch (error) {
            console.error('System health load error:', error);
            this.showToast('Failed to load system health', 'error');
        }
    },

    loadDailyTrendChart(data) {
        const canvas = document.getElementById('dailyTrendChart');
        if (!canvas) return;

        if (this.dailyTrendChart) this.dailyTrendChart.destroy();

        const ctx = canvas.getContext('2d');

        if (!data || data.length === 0) {
            data = [{ date: 'No Data', new_users: 0, interviews: 0, failed_logins: 0 }];
        }

        const labels = data.map(d => {
            const date = new Date(d.date);
            return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        });
        const newUsers = data.map(d => d.new_users);
        const interviews = data.map(d => d.interviews);
        const failedLogins = data.map(d => d.failed_logins);

        this.dailyTrendChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'New Users',
                        data: newUsers,
                        backgroundColor: 'rgba(99, 102, 241, 0.7)',
                        borderRadius: 4
                    },
                    {
                        label: 'Interviews',
                        data: interviews,
                        backgroundColor: 'rgba(16, 185, 129, 0.7)',
                        borderRadius: 4
                    },
                    {
                        label: 'Failed Logins',
                        data: failedLogins,
                        backgroundColor: 'rgba(245, 158, 11, 0.7)',
                        borderRadius: 4
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 16,
                            usePointStyle: true,
                            pointStyle: 'circle',
                            font: { size: 12 }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1,
                            font: { size: 11 }
                        }
                    },
                    x: {
                        ticks: {
                            font: { size: 11 }
                        }
                    }
                }
            }
        });
    },

    renderRecentErrors(errors) {
        const container = document.getElementById('recent-errors-list');
        if (!container) return;

        if (!errors || errors.length === 0) {
            container.innerHTML = '<div class="activity-empty">No recent errors</div>';
            return;
        }

        container.innerHTML = errors.slice(0, 10).map(error => this.formatErrorItem(error)).join('');
    },

    // ─────────────────────────────────────────────────────────────────────
    // ACTIVITY LOG
    // ─────────────────────────────────────────────────────────────────────

    async loadActivityTypes() {
        try {
            const res = await fetch('/api/admin/activity/types', { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load activity types');
            const data = await res.json();
            this.activityTypes = data.types;

            const select = document.getElementById('activity-type-filter');
            if (select) {
                select.innerHTML = '<option value="">All Types</option>' +
                    data.types.map(t => `<option value="${t.type}">${t.type.replace(/_/g, ' ')} (${t.count})</option>`).join('');
            }

            this.loadActivity();
        } catch (error) {
            console.error('Activity types error:', error);
        }
    },

    async loadActivity() {
        const container = document.getElementById('activity-list');
        const pagination = document.getElementById('activity-pagination');
        if (!container) return;

        container.innerHTML = '<div class="activity-loading">Loading activity...</div>';

        try {
            const params = new URLSearchParams({
                page: this.activityPage,
                per_page: 50
            });
            if (this.activityTypeFilter) params.set('type', this.activityTypeFilter);

            const res = await fetch(`/api/admin/activity?${params}`, { credentials: 'include' });
            if (!res.ok) throw new Error('Failed to load activity');
            const data = await res.json();

            if (data.activities.length === 0) {
                container.innerHTML = '<div class="activity-empty">No activity found</div>';
            } else {
                container.innerHTML = data.activities.map(activity => this.formatActivityItem(activity)).join('');
            }

            this.renderPagination(pagination, data.pagination, (page) => {
                this.activityPage = page;
                this.loadActivity();
            });
        } catch (error) {
            console.error('Activity load error:', error);
            container.innerHTML = '<div class="activity-empty">Failed to load activity</div>';
            this.showToast('Failed to load activity', 'error');
        }
    },

    // ─────────────────────────────────────────────────────────────────────
    // AUTH
    // ─────────────────────────────────────────────────────────────────────

    logout() {
        if (!confirm('Are you sure you want to logout?')) return;

        fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
            .then(() => {
                localStorage.removeItem('user');
                sessionStorage.removeItem('redirect_admin');
                window.location.href = '/admin/login.html';
            })
            .catch(() => {
                localStorage.removeItem('user');
                sessionStorage.removeItem('redirect_admin');
                window.location.href = '/admin/login.html';
            });
    },

    // ─────────────────────────────────────────────────────────────────────
    // TOAST
    // ─────────────────────────────────────────────────────────────────────

    showToast(message, type = 'info') {
        const existing = document.querySelector('.admin-toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = `admin-toast ${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => admin.init());
