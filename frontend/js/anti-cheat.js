/**
 * Anti-Cheat Monitoring System
 *
 * Monitors interview sessions for misconduct:
 *  1. Tab switching / window blur       → counts a strike, shows a live warning
 *  2. Camera off / face missing         → counts a strike, shows a live warning
 *  3. Third strike                      → session permanently blocked
 *
 * Strikes are reported to the backend (`/api/anti-cheat/report`) so admins
 * can see them in the activity log, and the user's account can be blocked
 * after 3 strikes. A blocked account cannot start new interviews until an
 * admin unblocks it.
 */
const antiCheat = {
    // ── Configuration ──
    MAX_STRIKES: 3,
    // Grace period (ms) before "no face" counts a strike — avoids false
    // positives when the user leans away briefly.
    FACE_MISS_GRACE_MS: 6000,
    // How often (ms) to check the video frame for a face.
    FACE_CHECK_INTERVAL_MS: 1500,

    // ── Runtime state ──
    strikes: 0,
    monitoringActive: false,
    focusListeners: [],
    faceCheckTimer: null,
    faceMissSince: null,
    faceDetected: true,
    stream: null,
    blockCallback: null,
    warnCallback: null,

    startMonitoring(stream, blockCallback, warnCallback) {
        this.stopMonitoring(); // clean slate on restart
        this.monitoringActive = true;
        this.strikes = 0;
        this.stream = stream || null;
        this.blockCallback = typeof blockCallback === 'function' ? blockCallback : null;
        this.warnCallback = typeof warnCallback === 'function' ? warnCallback : null;

        // ── 1. Tab switching / window blur ──
        const handleVisibilityChange = () => {
            if (!this.monitoringActive) return;
            if (document.hidden) {
                this.registerStrike('tab_switch', 'User switched away from the interview tab.');
            }
        };
        const handleBlur = () => {
            if (!this.monitoringActive) return;
            // visibilitychange already covers tab switches; blur without hidden
            // means the user clicked another window — still a violation.
            if (!document.hidden) {
                this.registerStrike('window_blur', 'User clicked outside the interview window.');
            }
        };

        document.addEventListener('visibilitychange', handleVisibilityChange);
        window.addEventListener('blur', handleBlur);
        this.focusListeners = [
            { el: document, event: 'visibilitychange', handler: handleVisibilityChange },
            { el: window, event: 'blur', handler: handleBlur },
        ];

        // ── 2. Camera / face monitoring ──
        this.startFaceMonitoring();
    },

    // ─────────────────────────────────────────────────────────────────────
    // Strike handling
    // ─────────────────────────────────────────────────────────────────────
    registerStrike(type, description) {
        if (!this.monitoringActive) return;
        this.strikes++;
        const remaining = this.MAX_STRIKES - this.strikes;

        // Report to backend (fire-and-forget; admins see it in the activity log)
        this.reportToBackend(type, description, remaining);

        if (this.strikes >= this.MAX_STRIKES) {
            this.blockSession(type, description);
        } else {
            this.showWarning(type, remaining);
        }
    },

    reportToBackend(type, description, remaining) {
        try {
            fetch('/api/anti-cheat/report', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    violation_type: type,
                    description: description,
                    strike_number: this.strikes,
                }),
                keepalive: true, // survives tab switches
            }).catch(() => {});
        } catch (e) { /* never break the interview over logging */ }
    },

    blockSession(type, description) {
        this.monitoringActive = false;
        this.teardownListeners();
        this.stopFaceMonitoring();

        // Hide the warning overlay; the blocked screen takes over.
        this.hideWarning();

        if (this.blockCallback) this.blockCallback(type, description);
    },

    // ─────────────────────────────────────────────────────────────────────
    // Camera / face monitoring
    // ─────────────────────────────────────────────────────────────────────
    startFaceMonitoring() {
        if (!this.stream) return; // no camera → nothing to monitor
        const video = document.getElementById('webcam-feed');
        if (!video) return;

        // Lazy-load face detection models (uses the browser's built-in
        // FaceDetector when available; falls back to motion heuristics).
        this.faceCheckTimer = setInterval(() => this.checkFace(), this.FACE_CHECK_INTERVAL_MS);
    },

    async checkFace() {
        if (!this.monitoringActive || !this.stream) return;
        const video = document.getElementById('webcam-feed');
        if (!video || video.readyState < 2) return; // not enough frame data yet

        // If the user toggled the camera off manually, the video track is
        // disabled — treat that as camera-off violation (not face-miss).
        const track = this.stream.getVideoTracks()[0];
        if (track && !track.enabled) {
            this.handleFaceViolation('camera_off', 'User turned off their camera during the interview.');
            return;
        }

        let facePresent = true;
        try {
            if ('FaceDetector' in window) {
                // Native browser face detection (Chrome behind a flag, but
                // harmless to attempt everywhere).
                if (!this._detector) {
                    this._detector = new FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
                }
                const faces = await this._detector.detect(video);
                facePresent = faces.length > 0;
            } else {
                // Fallback: pixel-brightness-variance heuristic. A live webcam
                // frame pointed at a person has skin-tone variance; a covered
                // lens, an empty chair, or a static image does not.
                facePresent = this.heuristicFaceCheck(video);
            }
        } catch (e) {
            facePresent = true; // never punish on detector failure
        }

        if (!facePresent) {
            this.handleFaceViolation('face_missing', 'No face detected in the camera frame.');
        } else {
            this.faceMissSince = null;
            this.faceDetected = true;
        }
    },

    heuristicFaceCheck(video) {
        try {
            const canvas = this._canvas || (this._canvas = document.createElement('canvas'));
            const w = 64, h = 48;
            canvas.width = w; canvas.height = h;
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.drawImage(video, 0, 0, w, h);
            const data = ctx.getImageData(0, 0, w, h).data;

            // Count pixels that look like skin tone (wide RGB range).
            let skin = 0;
            for (let i = 0; i < data.length; i += 4) {
                const r = data[i], g = data[i + 1], b = data[i + 2];
                if (r > 95 && g > 40 && b > 20 && r > g && r > b &&
                    (r - Math.min(g, b)) > 15) skin++;
            }
            const skinRatio = skin / (w * h);
            // Less than ~2% skin-tone pixels → likely no face in frame.
            return skinRatio >= 0.02;
        } catch (e) {
            return true; // never punish on heuristic failure
        }
    },

    handleFaceViolation(type, description) {
        if (!this.faceMissSince) {
            this.faceMissSince = Date.now();
        } else if (Date.now() - this.faceMissSince >= this.FACE_MISS_GRACE_MS) {
            // Face has been missing past the grace period → strike.
            this.faceMissSince = null;
            this.registerStrike(type, description);
        }
    },

    stopFaceMonitoring() {
        if (this.faceCheckTimer) {
            clearInterval(this.faceCheckTimer);
            this.faceCheckTimer = null;
        }
        this.faceMissSince = null;
    },

    // ─────────────────────────────────────────────────────────────────────
    // UI
    // ─────────────────────────────────────────────────────────────────────
    showWarning(type, remaining) {
        const warningEl = document.getElementById('anti-cheat-warning');
        if (!warningEl) return;

        const messages = {
            tab_switch: 'Do not switch tabs during the interview!',
            window_blur: 'Stay on the interview window!',
            camera_off: 'Camera turned off — keep your camera on!',
            face_missing: 'We could not see your face. Look at the camera!',
        };
        const textEl = warningEl.querySelector('.ac-warning-text');
        const iconEl = warningEl.querySelector('.ac-warning-icon');

        if (textEl) {
            textEl.innerHTML =
                `<strong>${messages[type] || 'Interview violation detected!'}</strong>` +
                `<p>Warning ${this.strikes} of ${this.MAX_STRIKES} — ` +
                `${remaining} more ${remaining === 1 ? 'violation' : 'violations'} and this session will be permanently blocked.</p>`;
        }
        if (iconEl) iconEl.textContent = type === 'tab_switch' || type === 'window_blur' ? '🚫' : '📷';

        warningEl.style.display = 'flex';
        warningEl.classList.add('warning-flash');
        if (this.warnCallback) this.warnCallback(this.strikes, remaining);

        // Auto-hide after 5 seconds.
        clearTimeout(this._warnTimer);
        this._warnTimer = setTimeout(() => this.hideWarning(), 5000);
    },

    hideWarning() {
        const warningEl = document.getElementById('anti-cheat-warning');
        if (warningEl) {
            warningEl.style.display = 'none';
            warningEl.classList.remove('warning-flash');
        }
    },

    // ─────────────────────────────────────────────────────────────────────
    // Lifecycle
    // ─────────────────────────────────────────────────────────────────────
    teardownListeners() {
        this.focusListeners.forEach(({ el, event, handler }) => {
            el.removeEventListener(event, handler);
        });
        this.focusListeners = [];
    },

    stopMonitoring() {
        this.monitoringActive = false;
        this.teardownListeners();
        this.stopFaceMonitoring();
        clearTimeout(this._warnTimer);
        this.hideWarning();
        this._detector = null;
    },

    getIntegrityReport() {
        return {
            is_flagged: this.strikes > 0,
            tab_switches: this.strikes,
            warning_count: this.strikes,
            strikes: this.strikes,
        };
    },
};
