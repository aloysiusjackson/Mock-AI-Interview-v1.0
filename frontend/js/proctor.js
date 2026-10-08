/**
 * Assessment Proctoring Layer
 *
 * Adds professional online-assessment behaviour on top of the existing
 * interview room — without changing any of the current UI, styles or flows:
 *
 *  1. Interview Environment Check — a pre-session screen (Camera, Microphone,
 *     Fullscreen, Screen Sharing) that opens when the user starts an
 *     interview. The session itself begins only after "Start Interview".
 *  2. True browser fullscreen (Fullscreen API) with exit detection and a
 *     "Return to Fullscreen" recovery banner.
 *  3. Screen sharing via getDisplayMedia(), including detection of the
 *     browser's native "Stop sharing" control and a resume-sharing banner.
 *  4. Live status indicators (Fullscreen / Screen Sharing / Camera /
 *     Microphone) inside the existing call UI.
 *
 * Safety rules followed here:
 *  - The camera/microphone stream is captured ONCE on the check screen and is
 *    handed to the interview room (`interview.startWebcam` reuses it), so a
 *    session never opens a duplicate camera or mic stream.
 *  - Nothing is recorded, hidden, or bypassed. Every warning is visible,
 *    dismissible and non-blocking, and capture only starts from an explicit
 *    user action (button click / browser permission prompt).
 */

const proctor = {
    // ── Configuration ──
    // Grace period (ms) before a deliberately-turned-off camera/mic raises a
    // soft warning — long enough not to nag while someone is thinking.
    SOFT_WARN_GRACE_MS: 8000,

    // ── Runtime state ──
    setupOpen: false,
    sessionActive: false,
    mediaStream: null,        // combined camera + mic stream from the check screen
    cameraStream: null,       // fallback when only the camera could be granted
    micStream: null,          // fallback when only the microphone could be granted
    screenStream: null,       // active getDisplayMedia stream
    cameraState: 'unknown',   // unknown | ready | permission | unavailable
    micState: 'unknown',      // unknown | ready | permission | unavailable
    pendingStart: null,       // interview start callback waiting on the check screen
    _pollTimer: null,
    _controlsBound: false,
    _stoppingScreen: false,   // internal stop → suppress the "sharing stopped" warning
    _warningKind: null,
    _softWarn: { camera: { since: 0, warned: false }, mic: { since: 0, warned: false } },

    // ─────────────────────────────────────────────────────────────────────
    // Capability detection
    // ─────────────────────────────────────────────────────────────────────
    supportsFullscreen() {
        return !!(document.fullscreenEnabled && (
            document.documentElement.requestFullscreen ||
            document.documentElement.webkitRequestFullscreen
        ));
    },

    supportsScreenShare() {
        return !!(window.isSecureContext !== false &&
            navigator.mediaDevices &&
            typeof navigator.mediaDevices.getDisplayMedia === 'function');
    },

    isFullscreen() {
        return !!(document.fullscreenElement || document.webkitFullscreenElement);
    },

    /** Never let a hanging browser promise stall the interview flow. */
    raceTimeout(promise, ms) {
        return Promise.race([
            Promise.resolve(promise).catch(() => {}),
            new Promise(resolve => setTimeout(resolve, ms))
        ]);
    },

    // ─────────────────────────────────────────────────────────────────────
    // Fullscreen
    // ─────────────────────────────────────────────────────────────────────
    async enterFullscreen() {
        if (this.isFullscreen()) return true;
        if (!this.supportsFullscreen()) {
            this.warn('unavailable',
                'Fullscreen is not available in this browser. Please keep this tab focused while you interview.');
            return false;
        }

        // Fullscreen the interview room itself, so the browser chrome is all
        // that remains — no CSS fake fullscreen is used anywhere.
        const target = document.getElementById('interview-view') || document.documentElement;
        try {
            const request = target.requestFullscreen
                ? target.requestFullscreen({ navigationUI: 'hide' })
                : (typeof target.webkitRequestFullscreen === 'function'
                    ? target.webkitRequestFullscreen()
                    : Promise.resolve());
            // Some embedded webviews leave the request promise pending forever —
            // never let fullscreen block the interview from starting.
            await this.raceTimeout(request, 2500);
            const entered = this.isFullscreen();
            if (!entered) {
                this.warn('fullscreen',
                    'Fullscreen could not be enabled in this browser. Please keep this tab focused while you interview.');
            }
            return entered;
        } catch (error) {
            // The request needs a user gesture and can be blocked by policy —
            // explain it instead of crashing.
            this.warn('fullscreen',
                'Fullscreen could not be enabled. Press "Return to Fullscreen" to try again.');
            return false;
        }
    },

    async exitFullscreen() {
        try {
            if (document.fullscreenElement && document.exitFullscreen) {
                await this.raceTimeout(document.exitFullscreen(), 1500);
            } else if (document.webkitFullscreenElement && document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            }
        } catch (error) { /* nothing to do */ }
    },

    handleFullscreenChange() {
        if (this.isFullscreen()) {
            // Back in fullscreen — clear a pending fullscreen warning.
            if (this._warningKind === 'fullscreen') this.hideWarning();
        } else if (this.sessionActive && !this._setupVisible()) {
            this.warn('fullscreen',
                'You have exited fullscreen mode. Please return to fullscreen to continue your interview.',
                'Return to Fullscreen',
                () => this.enterFullscreen().then(ok => { if (ok) this.hideWarning(); }));
        }
        this.refreshStatuses();
    },

    // ─────────────────────────────────────────────────────────────────────
    // Screen sharing
    // ─────────────────────────────────────────────────────────────────────
    async startScreenShare() {
        if (!this.supportsScreenShare()) {
            this.reportScreenProblem(
                'Screen sharing is not supported in this browser. Please continue on desktop Chrome, Edge or Firefox.');
            return false;
        }

        try {
            // The browser picker is the ONLY way a stream starts — no bypassing.
            const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
            this.registerScreenStream(stream);
            return true;
        } catch (error) {
            const cancelled = error && (error.name === 'NotAllowedError' || error.name === 'AbortError');
            this.reportScreenProblem(cancelled
                ? 'Screen sharing was cancelled. Press "Share Screen" whenever you are ready to resume.'
                : 'Screen sharing could not be started. Please try again, or continue without sharing.');
            return false;
        }
    },

    /**
     * Attach monitoring to a display-capture stream. Kept separate from
     * startScreenShare() so the "stopped" detector is testable on its own.
     */
    registerScreenStream(stream) {
        this.screenStream = stream;
        const track = stream.getVideoTracks()[0];
        if (track) {
            // Fires when the user stops sharing from the browser's own
            // "Stop sharing" control, or when the shared surface is closed.
            track.addEventListener('ended', () => this.handleScreenEnded());
        }
        this.refreshStatuses();
        this.refreshSetupScreen();
    },

    handleScreenEnded() {
        this.screenStream = null;
        if (this.sessionActive && !this._stoppingScreen) {
            this.warn('screen',
                'Screen sharing has been stopped. Please resume screen sharing to continue.',
                'Share Screen',
                () => this.startScreenShare().then(ok => { if (ok) this.hideWarning(); }));
        }
        this.refreshStatuses();
        this.refreshSetupScreen();
    },

    stopScreenShare() {
        this._stoppingScreen = true;
        if (this.screenStream) {
            this.screenStream.getTracks().forEach(track => track.stop());
            this.screenStream = null;
        }
        this._stoppingScreen = false;
        this.refreshStatuses();
        this.refreshSetupScreen();
    },

    screenStreamReady() {
        if (!this.screenStream) return false;
        return this.screenStream.getVideoTracks().some(track => track.readyState === 'live');
    },

    reportScreenProblem(message) {
        // Before the session this belongs on the check screen; during the
        // session it deserves the clearly visible banner.
        if (this.setupOpen) this.setupHint(message, true);
        else this.warn('screen', message);
    },

    // ─────────────────────────────────────────────────────────────────────
    // Camera + microphone (a single stream, requested once)
    // ─────────────────────────────────────────────────────────────────────
    videoTrack() {
        const stream = this.mediaStream || this.cameraStream;
        if (!stream) return null;
        return stream.getVideoTracks()[0] || null;
    },

    audioTrack() {
        const stream = this.mediaStream || this.micStream;
        if (!stream) return null;
        return stream.getAudioTracks()[0] || null;
    },

    /** The stream the interview room should reuse (never a duplicate). */
    getMediaStream() {
        return this.mediaStream || this.cameraStream || null;
    },

    hasLiveAudio() {
        const track = this.audioTrack();
        return !!(track && track.readyState === 'live');
    },

    requestMedia() {
        // Remember the in-flight acquisition so the interview room can wait for
        // it instead of opening a second camera stream.
        this._mediaPromise = this._requestMediaFlow();
        return this._mediaPromise;
    },

    mediaPending() {
        return !!this._mediaPromise;
    },

    /** Wait (bounded) for the check-screen acquisition to finish. */
    async awaitMedia(maxMs) {
        if (this._mediaPromise) await this.raceTimeout(this._mediaPromise, maxMs);
        return this.getMediaStream();
    },

    /** Adopt a stream the interview room acquired itself (timing fallback). */
    adoptStream(stream) {
        if (!stream) return;
        if (this.mediaStream && this.mediaStream !== stream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
        }
        this.mediaStream = stream;
        if (stream.getVideoTracks().length) this.cameraState = 'ready';
        if (stream.getAudioTracks().length) this.micState = 'ready';
        this.watchMediaTracks(stream);
        this.refreshStatuses();
        this.refreshSetupScreen();
    },

    async _requestMediaFlow() {
        if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
            this.cameraState = 'unavailable';
            this.micState = 'unavailable';
            this.refreshSetupScreen();
            return;
        }

        const cameraLive = !!(this.videoTrack() && this.videoTrack().readyState === 'live');
        const micLive = this.hasLiveAudio();
        if (cameraLive && micLive) {
            this.cameraState = 'ready';
            this.micState = 'ready';
            this.refreshSetupScreen();
            return;
        }

        // First attempt: one combined prompt for camera + mic.
        if (!cameraLive && !micLive) {
            try {
                const combined = await navigator.mediaDevices.getUserMedia({
                    video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
                    audio: true
                });
                this.mediaStream = combined;
                this.watchMediaTracks(combined);
                this.cameraState = 'ready';
                this.micState = 'ready';
                this.refreshSetupScreen();
                this.refreshStatuses();
                return;
            } catch (error) {
                // Fall through: try each device separately so every check row
                // shows a truthful status (e.g. camera present, mic missing).
            }
        }

        if (!cameraLive) await this.requestCameraOnly();
        if (!micLive) await this.requestMicOnly();

        this.refreshSetupScreen();
        this.refreshStatuses();
    },

    async requestCameraOnly() {
        if (this.videoTrack() && this.videoTrack().readyState === 'live') return;
        try {
            this.cameraStream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
            });
            this.watchMediaTracks(this.cameraStream);
            this.cameraState = 'ready';
        } catch (error) {
            this.cameraState = this.classifyMediaError(error);
        }
    },

    async requestMicOnly() {
        if (this.hasLiveAudio()) return;
        try {
            this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.watchMediaTracks(this.micStream);
            this.micState = 'ready';
        } catch (error) {
            this.micState = this.classifyMediaError(error);
        }
    },

    classifyMediaError(error) {
        const name = error && error.name;
        if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
            return 'unavailable';
        }
        return 'permission'; // NotAllowedError / SecurityError / NotReadableError
    },

    /**
     * Watch a stream's tracks so an unexpected stop is noticed immediately.
     * Note: interview.stopWebcam() calls track.stop(), which does NOT fire
     * "ended", so a normal session end never raises a false warning.
     */
    watchMediaTracks(stream) {
        stream.getTracks().forEach(track => {
            track.addEventListener('ended', () => {
                if (track.kind === 'video') this.cameraState = 'unavailable';
                if (track.kind === 'audio') this.micState = 'unavailable';
                if (this.sessionActive) this.raiseTrackWarning(track.kind);
                this.refreshStatuses();
                this.refreshSetupScreen();
            });
        });
    },

    raiseTrackWarning(kind) {
        const isCamera = kind === 'video';
        const monitor = isCamera ? this._softWarn.camera : this._softWarn.mic;
        monitor.warned = true; // the poll must not repeat this one
        this.warn(isCamera ? 'camera' : 'mic',
            isCamera
                ? 'Your camera stopped unexpectedly. Press "Resume Camera" to continue.'
                : 'Your microphone stopped unexpectedly. Press "Resume Microphone" to continue.',
            isCamera ? 'Resume Camera' : 'Resume Microphone',
            () => (isCamera ? this.resumeCamera() : this.resumeMicrophone()));
    },

    /** Re-enable the existing track/UI (button click) or re-acquire if it died. */
    resumeCamera() {
        const btn = document.getElementById('call-camera-btn');
        if (btn && typeof interview !== 'undefined' && interview.state.cameraOff) {
            btn.click(); // reuse the room's own toggle so its icon stays in sync
            return;
        }
        this.retryMedia('video');
    },

    resumeMicrophone() {
        const btn = document.getElementById('call-mic-btn');
        if (btn && typeof interview !== 'undefined' && interview.state.micMuted) {
            btn.click(); // reuse the room's own toggle so its icon stays in sync
            return;
        }
        this.retryMedia('audio');
    },

    async retryMedia(kind) {
        if (kind === 'video') {
            this.mediaStream = null;
            this.cameraStream = null;
            await this.requestCameraOnly();

            const stream = this.getMediaStream();
            const video = document.getElementById('webcam-feed');
            if (stream && video) {
                // Keep the interview room (and anti-cheat) pointed at the same stream.
                videoStream = stream;
                video.srcObject = stream;
                video.style.display = 'block';
                const placeholder = document.getElementById('webcam-placeholder');
                if (placeholder) placeholder.style.display = 'none';
                if (typeof antiCheat !== 'undefined') antiCheat.stream = stream;
            }
        } else {
            this.mediaStream = null;
            this.micStream = null;
            await this.requestMicOnly();
        }

        this._softWarn[kind === 'video' ? 'camera' : 'mic'].warned = false;
        this.hideWarning();
        this.refreshStatuses();
        this.refreshSetupScreen();
    },

    /** Called from the 1s poll: offer a soft warning for a camera/mic left off. */
    monitorTrackState() {
        if (!this.sessionActive) return;
        const cams = typeof interview !== 'undefined' ? interview.state : {};

        // Only nudge when a capture exists and is off/muted — a session that
        // intentionally continues without a camera/mic is the user's choice.
        const cameraTrack = this.videoTrack();
        const cameraInactive = !!cameraTrack && (!!cams.cameraOff || !(cameraTrack.readyState === 'live' && cameraTrack.enabled));
        this.softWarnOne('camera', cameraInactive,
            'Camera is off — the interviewer cannot see you. Press "Resume Camera" to turn it back on.');

        const micTrack = this.audioTrack();
        const micInactive = !!micTrack && (!!cams.micMuted || !(micTrack.readyState === 'live' && micTrack.enabled));
        this.softWarnOne('mic', micInactive,
            'Microphone is muted — your answers cannot be heard. Press "Resume Microphone" to unmute.');
    },

    softWarnOne(kind, inactive, message) {
        const monitor = this._softWarn[kind];
        if (!inactive) {
            monitor.since = 0;
            monitor.warned = false;
            return;
        }
        if (monitor.warned) return;
        if (!monitor.since) {
            monitor.since = Date.now();
            return;
        }
        if (Date.now() - monitor.since >= this.SOFT_WARN_GRACE_MS) {
            monitor.warned = true;
            this.warn(kind, message, kind === 'camera' ? 'Resume Camera' : 'Resume Microphone',
                () => (kind === 'camera' ? this.resumeCamera() : this.resumeMicrophone()));
        }
    },

    // ─────────────────────────────────────────────────────────────────────
    // Session lifecycle
    // ─────────────────────────────────────────────────────────────────────
    shouldRunSetup() {
        return !this.sessionActive;
    },

    beginSession() {
        this.sessionActive = true;
        this.bindControls();
        this.startPoll();
        this.refreshStatuses();
    },

    endSession() {
        this.sessionActive = false;
        this.stopScreenShare();
        if (this.isFullscreen()) this.exitFullscreen();
        this.hideWarning();
        this.releaseMedia();
        this.stopPollIfIdle();
        this.refreshStatuses();
    },

    releaseMedia() {
        [this.mediaStream, this.cameraStream, this.micStream].forEach(stream => {
            if (stream) stream.getTracks().forEach(track => track.stop());
        });
        this.mediaStream = this.cameraStream = this.micStream = null;
        this._mediaPromise = null;
        this.cameraState = 'unknown';
        this.micState = 'unknown';
        this._softWarn = { camera: { since: 0, warned: false }, mic: { since: 0, warned: false } };
    },

    // ─────────────────────────────────────────────────────────────────────
    // Environment-check screen
    // ─────────────────────────────────────────────────────────────────────
    _setupVisible() {
        const modal = document.getElementById('assessment-setup-modal');
        return !!(modal && modal.style.display === 'flex');
    },

    openSetup(roleLabel, onProceed) {
        const modal = document.getElementById('assessment-setup-modal');
        if (!modal) {
            // Markup missing → never block the interview.
            if (typeof onProceed === 'function') onProceed();
            return;
        }

        this.pendingStart = typeof onProceed === 'function' ? onProceed : null;
        this.setupOpen = true;

        // Every session gets a fresh environment check.
        this.stopScreenShare();
        this._softWarn = { camera: { since: 0, warned: false }, mic: { since: 0, warned: false } };

        const roleEl = document.getElementById('assessment-setup-role');
        if (roleEl) roleEl.textContent = roleLabel || 'your interview';

        modal.style.display = 'flex';
        modal.style.alignItems = 'center';
        modal.style.justifyContent = 'center';
        this.setupHint('');
        this.refreshSetupScreen();
        this.startPoll();

        // This IS the environment check, so ask for camera + mic right away.
        this.requestMedia();
    },

    closeSetup(cancelled) {
        const modal = document.getElementById('assessment-setup-modal');
        if (modal) modal.style.display = 'none';
        this.setupOpen = false;

        if (cancelled) {
            this.pendingStart = null;
            if (!this.sessionActive) {
                this.stopScreenShare();
                this.releaseMedia();          // give the camera light back if they bail out
                if (this.isFullscreen()) this.exitFullscreen();
            }
        }
        this.stopPollIfIdle();
    },

    async proceedFromSetup() {
        const start = this.pendingStart;
        this.pendingStart = null;
        this.closeSetup(false);

        // Fullscreen must be entered from this click — browsers require a user
        // gesture, so it can never be forced automatically.
        if (this.supportsFullscreen() && !this.isFullscreen()) {
            await this.enterFullscreen();
        }

        if (typeof start === 'function') start();
    },

    refreshSetupScreen() {
        if (!this.setupOpen) return;
        this.renderSetupItem('camera', this.cameraState, 'camera');
        this.renderSetupItem('microphone', this.micState, 'mic');
        this.renderSetupItem('fullscreen', this.supportsFullscreen() ? 'ready' : 'unavailable', 'fullscreen');
        this.renderSetupItem('screenshare',
            this.screenStreamReady() ? 'sharing' : (this.supportsScreenShare() ? 'ready' : 'unavailable'),
            'screen');
    },

    renderSetupItem(key, state, kind) {
        const statusEl = document.getElementById('assessment-status-' + key);
        const actionEl = document.getElementById('assessment-action-' + key);
        const noteEl = document.getElementById('assessment-note-' + key);
        if (!statusEl) return;

        const states = {
            ready: { text: 'Ready', className: 'is-ready' },
            permission: { text: 'Permission Required', className: 'is-warn' },
            unavailable: { text: 'Not Available', className: 'is-bad' },
            sharing: { text: 'Sharing', className: 'is-ready' },
            unknown: { text: 'Not Checked', className: 'is-warn' }
        };
        const info = states[state] || states.unknown;
        statusEl.textContent = info.text;
        statusEl.className = 'assessment-check-status ' + info.className;

        if (actionEl) {
            if (kind === 'camera' || kind === 'mic') {
                actionEl.style.display = state === 'ready' ? 'none' : 'inline-flex';
                actionEl.textContent = 'Enable';
            } else if (kind === 'screen') {
                if (state === 'sharing') {
                    actionEl.style.display = 'inline-flex';
                    actionEl.textContent = 'Stop Sharing';
                } else if (state === 'ready' || state === 'permission') {
                    actionEl.style.display = 'inline-flex';
                    actionEl.textContent = 'Share Screen';
                } else {
                    actionEl.style.display = 'none';
                }
            } else {
                actionEl.style.display = 'none';
            }
        }

        if (noteEl) {
            const notes = {
                camera: {
                    ready: 'Camera access granted — your feed appears in the interview room.',
                    permission: 'Allow camera access so your interviewer can see you.',
                    unavailable: 'No usable camera was found on this device.'
                },
                microphone: {
                    ready: 'Microphone access granted — speech-to-text is ready.',
                    permission: 'Allow microphone access so your answers can be transcribed.',
                    unavailable: 'No usable microphone was found on this device.'
                },
                fullscreen: {
                    ready: 'Enters automatically when you press Start Interview.',
                    unavailable: 'This browser cannot lock fullscreen — keep this tab focused.'
                },
                screenshare: {
                    sharing: 'Your screen is being shared. You can stop at any time.',
                    ready: 'Share your screen so the session can be monitored (optional, recommended).',
                    unavailable: 'Screen sharing is not supported in this browser.'
                }
            };
            noteEl.textContent = (notes[kind] && notes[kind][state]) || '';
        }
    },

    setupHint(message, isWarning) {
        const el = document.getElementById('assessment-setup-hint');
        if (!el) return;
        el.textContent = message || 'Fix anything marked above, then start when ready.';
        el.classList.toggle('is-warning', !!isWarning);
    },

    // ─────────────────────────────────────────────────────────────────────
    // Status indicators (interview room)
    // ─────────────────────────────────────────────────────────────────────
    refreshStatuses() {
        const fullscreenOn = this.isFullscreen();
        const screenOn = this.screenStreamReady();
        const cameraTrack = this.videoTrack();
        const micTrack = this.audioTrack();
        const cameraOn = !!(cameraTrack && cameraTrack.readyState === 'live' && cameraTrack.enabled);
        const micOn = !!(micTrack && micTrack.readyState === 'live' && micTrack.enabled);

        this.setStatusPill('fullscreen',
            fullscreenOn ? 'on' : (this.supportsFullscreen() ? 'off' : 'bad'),
            fullscreenOn ? 'Active' : (this.supportsFullscreen() ? 'Off' : 'Unavailable'));

        this.setStatusPill('screen',
            screenOn ? 'on' : (this.supportsScreenShare() ? 'off' : 'bad'),
            screenOn ? 'Active' : (this.supportsScreenShare() ? 'Off' : 'Unavailable'));

        let cameraState, cameraText;
        if (cameraOn) { cameraState = 'on'; cameraText = 'Active'; }
        else if (cameraTrack) { cameraState = 'warn'; cameraText = 'Off'; }
        else if (this.cameraState === 'unavailable') { cameraState = 'bad'; cameraText = 'Unavailable'; }
        else { cameraState = 'off'; cameraText = 'Off'; }
        this.setStatusPill('camera', cameraState, cameraText);

        let micState, micText;
        if (micOn) { micState = 'on'; micText = 'Active'; }
        else if (micTrack) { micState = 'warn'; micText = 'Muted'; }
        else if (this.micState === 'unavailable') { micState = 'bad'; micText = 'Unavailable'; }
        else { micState = 'off'; micText = 'Off'; }
        this.setStatusPill('mic', micState, micText);

        const fullscreenBtn = document.getElementById('call-fullscreen-btn');
        if (fullscreenBtn) fullscreenBtn.classList.toggle('active', fullscreenOn);

        const shareBtn = document.getElementById('call-screenshare-btn');
        if (shareBtn) {
            shareBtn.classList.toggle('active', screenOn);
            shareBtn.title = screenOn ? 'Stop Screen Sharing' : 'Share Screen';
            const label = document.getElementById('call-screenshare-label');
            if (label) label.textContent = screenOn ? 'Stop' : 'Share';
        }
    },

    setStatusPill(key, state, text) {
        const el = document.getElementById('proctor-status-' + key);
        if (!el) return;
        el.dataset.state = state;
        const textEl = el.querySelector('.proctor-status-text');
        if (textEl) textEl.textContent = text;
    },

    startPoll() {
        if (this._pollTimer) return;
        this._pollTimer = setInterval(() => {
            this.refreshStatuses();
            this.refreshSetupScreen();
            this.monitorTrackState();
        }, 1000);
    },

    stopPollIfIdle() {
        if (this._pollTimer && !this.setupOpen && !this.sessionActive) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    // ─────────────────────────────────────────────────────────────────────
    // Warning banner (non-blocking)
    // ─────────────────────────────────────────────────────────────────────
    warn(kind, text, actionLabel, actionHandler) {
        const el = document.getElementById('proctor-warning');
        if (!el) {
            // No banner markup → degrade to the app's existing toast.
            if (typeof app !== 'undefined' && app.showNavToast) app.showNavToast(text);
            return;
        }

        this._warningKind = kind;
        const icons = { fullscreen: '🖥️', screen: '📡', camera: '📷', mic: '🎤' };
        const iconEl = document.getElementById('proctor-warning-icon');
        if (iconEl) iconEl.textContent = icons[kind] || '⚠️';

        const textEl = document.getElementById('proctor-warning-text');
        if (textEl) textEl.textContent = text;

        const btn = document.getElementById('proctor-warning-action');
        if (btn) {
            if (actionLabel && typeof actionHandler === 'function') {
                btn.style.display = 'inline-flex';
                btn.textContent = actionLabel;
                btn.onclick = () => { this.hideWarning(); actionHandler(); };
            } else {
                btn.style.display = 'none';
                btn.onclick = null;
            }
        }

        el.style.display = 'flex';
        el.classList.remove('proctor-warning-flash');
        void el.offsetWidth; // restart the slide-in animation
        el.classList.add('proctor-warning-flash');
    },

    hideWarning() {
        const el = document.getElementById('proctor-warning');
        if (el) el.style.display = 'none';
        this._warningKind = null;
    },

    // ─────────────────────────────────────────────────────────────────────
    // Wiring
    // ─────────────────────────────────────────────────────────────────────
    bindControls() {
        if (this._controlsBound) return;
        this._controlsBound = true;

        // ── Environment-check screen ──
        const startBtn = document.getElementById('assessment-start-btn');
        if (startBtn) startBtn.addEventListener('click', () => this.proceedFromSetup());

        const closeBtn = document.getElementById('assessment-setup-close');
        if (closeBtn) closeBtn.addEventListener('click', () => this.closeSetup(true));

        const modal = document.getElementById('assessment-setup-modal');
        if (modal) {
            modal.addEventListener('click', (event) => {
                if (event.target === modal) this.closeSetup(true);
            });
        }

        const cameraAction = document.getElementById('assessment-action-camera');
        if (cameraAction) cameraAction.addEventListener('click', () => this.requestMedia());

        const micAction = document.getElementById('assessment-action-microphone');
        if (micAction) micAction.addEventListener('click', () => this.requestMedia());

        const screenAction = document.getElementById('assessment-action-screenshare');
        if (screenAction) {
            screenAction.addEventListener('click', () => {
                if (this.screenStream) this.stopScreenShare();
                else this.startScreenShare();
            });
        }

        // ── Interview room controls ──
        const fullscreenBtn = document.getElementById('call-fullscreen-btn');
        if (fullscreenBtn) {
            fullscreenBtn.addEventListener('click', () => {
                if (!this.isFullscreen()) this.enterFullscreen();
            });
        }

        const shareBtn = document.getElementById('call-screenshare-btn');
        if (shareBtn) {
            shareBtn.addEventListener('click', () => {
                if (this.screenStream) this.stopScreenShare();
                else this.startScreenShare();
            });
        }

        // ── Warning banner ──
        const dismissBtn = document.getElementById('proctor-warning-dismiss');
        if (dismissBtn) dismissBtn.addEventListener('click', () => this.hideWarning());

        // ── Fullscreen exit detection ──
        document.addEventListener('fullscreenchange', () => this.handleFullscreenChange());
        document.addEventListener('webkitfullscreenchange', () => this.handleFullscreenChange());
    },

    init() {
        this.bindControls();
        this.refreshStatuses();
    }
};

// Expose for the interview room and the app shell (guard-friendly + handy in
// the browser console: `proctor.refreshStatuses()`).
window.proctor = proctor;

document.addEventListener('DOMContentLoaded', () => proctor.init());
