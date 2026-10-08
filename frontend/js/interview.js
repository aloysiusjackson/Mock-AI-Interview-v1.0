let videoStream = null;
let speechRecognizer = null;
let timerInterval = null;
let micAnalyzer = null;

const interview = {

    state: {
        role: '',
        questions: [],
        currentIndex: 0,
        answers: [],
        secondsElapsed: 0,
        isRecording: false,
        antiCheatFlags: null,
        micMuted: false,
        cameraOff: false,
        _aiState: 0,
        _aiSayId: 0,
    },

    async start(role) {
        if (window.proctor && proctor.shouldRunSetup()) {
            proctor.openSetup(role, () => this.startInternal(role));
            return;
        }
        return this.startInternal(role);
    },

    async startInternal(role) {
        this.state.role = role;
        this.state.currentIndex = 0;
        this.state.answers = [];
        this.state.secondsElapsed = 0;
        this.state.isRecording = false;
        this.state.antiCheatFlags = null;
        this.state.questions = [];
        this.state.resumeText = '';

        app.showLoader('Fetching relevant interview questions...');

        try {
            const data = await api.getQuestions(role);
            this.state.questions = data.questions;
        } catch (error) {
            console.error('Failed to load questions, using local seed fallback:', error);
            this.state.questions = this.getFallbackQuestions(role);
        }

        app.hideLoader();

        // Show Interview Room View
        app.switchView('interview-view');
        document.getElementById('interview-view').dataset.sessionActive = '1';
        if (window.proctor) proctor.beginSession();
        document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
        document.querySelectorAll('.nav-live-link').forEach(l => {
            l.parentElement.classList.add('active');
            l.classList.add('is-live');
            if (!l.querySelector('.live-dot')) {
                const dot = document.createElement('span');
                dot.className = 'live-dot';
                l.appendChild(dot);
            }
        });

        // Start anti-cheat monitoring
        antiCheat.startMonitoring(
            null,
            (type, description) => this.handleInterviewBlocked(type, description),
            (strikes, remaining) => console.warn(`[ANTI-CHEAT] Strike ${strikes} — ${remaining} remaining`)
        );

        // Update topbar context
        app.setInterviewTopbarContext(role);

        // Attempt to start real webcam, fallback to placeholder if fails
        this.startWebcam();

        // Setup Speech Recognition
        this.setupSpeechRecognition();

        // Initialize question tracking panel
        this.renderQuestionTracker();

        // Load the first question (starts the AI avatar animation)
        this.loadQuestion(0);

        // Setup transcript toggle
        this.setupTranscriptToggle();

        // Setup mic and camera toggle buttons
        this.setupControlButtons();
    },

    /** Setup mic/camera toggle buttons */
    setupControlButtons() {
        if (this._controlsBound) return;
        this._controlsBound = true;
        const micBtn = document.getElementById('call-mic-btn');
        const cameraBtn = document.getElementById('call-camera-btn');
        const micIndicator = document.getElementById('candidate-mic-indicator');

        // ── MIC TOGGLE ──
        if (micBtn) {
            this.state.micMuted = false;
            micBtn.addEventListener('click', () => {
                this.state.micMuted = !this.state.micMuted;
                micBtn.classList.toggle('muted', this.state.micMuted);
                micBtn.classList.toggle('active', !this.state.micMuted);
                if (micIndicator) micIndicator.style.color = this.state.micMuted ? '#ef4444' : '#34d399';
                if (this.state.micMuted) {
                    micBtn.querySelector('svg').innerHTML = '<line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"/><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .76-.13 1.49-.35 2.17"/><line x1="12" x2="12" y1="19" y2="22"/>';
                } else {
                    micBtn.querySelector('svg').innerHTML = '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>';
                }
                if (videoStream) {
                    videoStream.getAudioTracks().forEach(track => { track.enabled = !this.state.micMuted; });
                }
                console.log('Mic ' + (this.state.micMuted ? 'muted' : 'unmuted'));
            });
        }

        // ── CAMERA TOGGLE ──
        if (cameraBtn) {
            this.state.cameraOff = false;
            cameraBtn.addEventListener('click', () => {
                this.state.cameraOff = !this.state.cameraOff;
                cameraBtn.classList.toggle('muted', this.state.cameraOff);
                cameraBtn.classList.toggle('active', !this.state.cameraOff);

                const videoElement = document.getElementById('webcam-feed');
                const placeholder = document.getElementById('webcam-placeholder');

                if (this.state.cameraOff) {
                    if (videoStream) {
                        videoStream.getVideoTracks().forEach(track => { track.enabled = false; });
                    }
                    if (videoElement) videoElement.style.display = 'none';
                    if (placeholder) placeholder.style.display = 'flex';
                    cameraBtn.querySelector('svg').innerHTML = '<path d="M1 1l22 22"/><path d="M21 21H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h3m3-3h6l2 3h4a2 2 0 0 1 2 2v9.34"/>';
                    console.log('Camera turned OFF');
                } else {
                    const camPill = document.getElementById('proctor-status-camera');
                    if (camPill) {
                        camPill.dataset.state = this.state.cameraOff ? 'off' : 'on';
                        const ct = camPill.querySelector('.proctor-status-text');
                        if (ct) ct.textContent = this.state.cameraOff ? 'Off' : 'Active';
                    }
                    if (videoStream) {
                        videoStream.getVideoTracks().forEach(track => { track.enabled = true; });
                    }
                    if (videoElement) videoElement.style.display = 'block';
                    if (placeholder) placeholder.style.display = 'none';
                    cameraBtn.querySelector('svg').innerHTML = '<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>';
                    console.log('Camera turned ON');
                }
            });
        }
    },

    /** Setup the transcript panel toggle button */
    setupTranscriptToggle() {
        if (this._transcriptBound) return;
        this._transcriptBound = true;
        const transcriptBtn = document.getElementById('call-transcript-btn');
        const transcriptPanel = document.getElementById('call-transcript-panel');
        const closeTranscriptBtn = document.getElementById('close-transcript-panel');
        if (transcriptBtn && transcriptPanel) {
            transcriptBtn.addEventListener('click', () => {
                const isVisible = transcriptPanel.style.display !== 'none';
                transcriptPanel.style.display = isVisible ? 'none' : 'flex';
                transcriptBtn.classList.toggle('active', !isVisible);
            });
        }
        if (closeTranscriptBtn && transcriptPanel) {
            closeTranscriptBtn.addEventListener('click', () => {
                transcriptPanel.style.display = 'none';
                if (transcriptBtn) transcriptBtn.classList.remove('active');
            });
        }
    },

    /** Start an interview using questions generated from the candidate's resume */
    async startWithResumeQuestions(questions, role, resumeText) {
        if (window.proctor && proctor.shouldRunSetup()) {
            proctor.openSetup(role || 'Based on Resume', () => this.startWithResumeQuestionsInternal(questions, role, resumeText));
            return;
        }
        return this.startWithResumeQuestionsInternal(questions, role, resumeText);
    },

    async startWithResumeQuestionsInternal(questions, role, resumeText) {
        this.state.role = role || 'Based on Resume';
        this.state.currentIndex = 0;
        this.state.answers = [];
        this.state.secondsElapsed = 0;
        this.state.isRecording = false;
        this.state.antiCheatFlags = null;
        this.state.questions = questions || [];
        this.state.resumeText = resumeText || '';
        app.switchView('interview-view');
        document.getElementById('interview-view').dataset.sessionActive = '1';
        if (window.proctor) proctor.beginSession();
        document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
        document.querySelectorAll('.nav-live-link').forEach(l => {
            l.parentElement.classList.add('active');
            l.classList.add('is-live');
            if (!l.querySelector('.live-dot')) {
                const dot = document.createElement('span');
                dot.className = 'live-dot';
                l.appendChild(dot);
            }
        });
        antiCheat.startMonitoring(
            null,
            (type, description) => this.handleInterviewBlocked(type, description),
            (strikes, remaining) => console.warn(`[ANTI-CHEAT] Strike ${strikes} — ${remaining} remaining`)
        );
        app.setInterviewTopbarContext(this.state.role);
        this.startWebcam();
        this.setupSpeechRecognition();
        this.renderQuestionTracker();
        this.setupResumeQA(resumeText || '');
        this.setupTranscriptToggle();
        this.setupControlButtons();
        this.loadQuestion(0);
    },

    /** Start real webcam feed - fallback to placeholder on failure */
    async startWebcam() {
        const videoElement = document.getElementById('webcam-feed');
        const placeholder = document.getElementById('webcam-placeholder');
        if (window.proctor) {
            let existing = proctor.getMediaStream();
            if (!(existing && existing.getVideoTracks().some(t => t.readyState === 'live')) && proctor.mediaPending()) {
                existing = await proctor.awaitMedia(4000);
            }
            if (existing && existing.getVideoTracks().some(t => t.readyState === 'live')) {
                videoStream = existing;
                if (videoElement) { videoElement.srcObject = videoStream; videoElement.style.display = 'block'; }
                if (placeholder) placeholder.style.display = 'none';
                antiCheat.stream = videoStream;
                console.log('Reusing camera stream from the environment check');
                return;
            }
            if (proctor.cameraState === 'permission' || proctor.cameraState === 'unavailable') {
                console.warn('Camera not available from the environment check — using animated placeholder.');
                this.setupPlaceholderOnly();
                return;
            }
        }
        try {
            videoStream = await navigator.mediaDevices.getUserMedia({
                video: {
                    width: { ideal: 640 },
                    height: { ideal: 480 },
                    facingMode: 'user'
                },
                audio: false
            });
            videoElement.srcObject = videoStream;
            videoElement.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
            console.log('Real webcam feed started successfully');
            antiCheat.stream = videoStream;
            if (window.proctor) proctor.adoptStream(videoStream);
        } catch (error) {
            console.warn('Webcam not available, using animated placeholder:', error.message);
            this.setupPlaceholderOnly();
        }
    },

    /** Show the animated placeholder when webcam is unavailable */
    setupPlaceholderOnly() {
        const videoElement = document.getElementById('webcam-feed');
        const placeholder = document.getElementById('webcam-placeholder');
        if (videoElement) videoElement.style.display = 'none';
        if (placeholder) placeholder.style.display = 'flex';
        const avatar = document.getElementById('mock-avatar-interview');
        if (avatar && this.state.role) { avatar.textContent = this.state.role.charAt(0).toUpperCase(); }
        const interviewerName = document.getElementById('interviewer-name');
        if (interviewerName) { interviewerName.textContent = `AI ${this.state.role} Interviewer`; }
        const roleSub = document.getElementById('interviewer-role-sub');
        if (roleSub) roleSub.textContent = `${this.state.role} Panel`;
        const candidateInit = document.getElementById('candidate-initial');
        if (candidateInit) candidateInit.textContent = this.state.role.charAt(0).toUpperCase();
        console.log('Camera disabled. Using animated placeholder.');
    },

    /** Called by the anti-cheat monitor when 3 strikes are reached. */
    handleInterviewBlocked(type, description) {
        console.error('[ANTI-CHEAT] Session blocked:', type, description);
        this.stopRecording();
        this.stopWebcam();
        if (window.proctor) proctor.endSession();
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }
        if (speechRecognizer) {
            try { speechRecognizer.stop(); } catch (e) {}
            speechRecognizer = null;
        }
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
        this.clearLiveNavState();
        this.renderBlockedScreen(type);
    },
    renderBlockedScreen(type) {
        const view = document.getElementById('interview-view');
        if (!view) return;
        const reasons = {
            tab_switch: 'You left the interview tab too many times.',
            window_blur: 'You clicked outside the interview window too many times.',
            camera_off: 'You turned off your camera during the interview.',
            face_missing: 'You were not visible in the camera frame repeatedly.',
        };
        view.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:center; min-height:70vh; padding:2rem;">
                <div style="max-width:560px; width:100%; text-align:center; background:var(--glass-bg, rgba(20,20,30,.85));"
                    style="border:1px solid rgba(239,68,68,.4); border-radius:20px; padding:3rem 2.5rem;"
                    style="box-shadow:0 20px 60px rgba(239,68,68,.15);">
                    <div style="font-size:4rem; margin-bottom:1rem;">⛔</div>
                    <h2 style="color:#ef4444; margin-bottom:.75rem; font-size:1.6rem;">Interview Session Blocked</h2>
                    <p style="color:var(--text-secondary, #aaa); margin-bottom:1rem; line-height:1.6;">
                        ${reasons[type]}<br>
                        Your account has been flagged after <strong style="color:#ef4444;">3 violations</strong>
                        and you can no longer start interview sessions.
                    </p>
                    <p style="color:var(--text-secondary, #888); font-size:.9rem; margin-bottom:2rem;">
                        This block is permanent until an administrator reviews your account.
                    </p>
                    <button class="btn btn-primary" onclick="location.reload()"
                            style="padding:.8rem 2rem; border-radius:10px; cursor:pointer;">
                        Back to Dashboard
                    </button>
                </div>
            </div>
        `;
    },

    setupSpeechRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn('Speech Recognition not supported in this browser. Fallback input will be provided.');
            this.showSpeechError('Speech recognition is not supported in this browser. Please use Chrome, Edge, or Safari and type your answer in the text box below.');
            return;
        }

        speechRecognizer = new SpeechRecognition();
        speechRecognizer.continuous = true;
        speechRecognizer.interimResults = true;
        speechRecognizer.lang = 'en-US';
        speechRecognizer.maxAlternatives = 1;

        // ── Audio amplitude capture for the avatar ──
        // Renders the microphone (or TTS) stream through a tiny Web Audio
        // graph so the avatar mouth reacts to REAL voice amplitude.
        micAnalyzer = {
            stream: null,
            source: null,
            node: null,
            ctx: null,
            data: new Uint8Array(0),
            enabled: false,
            setSample: null,
            start(stream, callback) {
                if (this.enabled) return;
                this.stream = stream;
                this.setSample = callback;
                try {
                    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
                    this.source = this.ctx.createMediaStreamSource(stream);
                    this.node = this.ctx.createAnalyser();
                    this.node.fftSize = 256;
                    this.node.smoothingTimeConstant = 0.35;
                    this.source.connect(this.node);
                    this.enabled = true;
                    this._loop();
                } catch (e) {
                    console.warn('[interview] mic audio analyzer could not start:', e);
                }
            },
            _loop() {
                if (!this.enabled || !this.node) { setTimeout(() => this._loop(), 100); return; }
                const len = this.node.frequencyBinCount;
                if (!this.data || this.data.length !== len) this.data = new Uint8Array(len);
                this.node.getByteTimeDomainData(this.data);
                // Rough RMS energy for lip-sync.
                let sum = 0;
                for (let i = 0; i < this.data.length; i++) sum += this.data[i] * this.data[i];
                const rms = Math.sqrt(sum / this.data.length);
                const amp = Math.min(1, rms / 60);
                if (this.setSample) this.setSample(amp);
                setTimeout(() => this._loop(), 30);
            },
            stop() {
                this.enabled = false;
                this.setSample = null;
                try { if (this.source) this.source.disconnect(); } catch (e) {}
                try { if (this.ctx) this.ctx.close(); } catch (e) {}
            }
        };

        speechRecognizer.onresult = (event) => {
            let finalTranscript = '';
            let interimTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; ++i) {
                const result = event.results[i];
                if (result.isFinal) {
                    finalTranscript += result[0].transcript;
                } else {
                    interimTranscript += result[0].transcript;
                }
            }

            if (finalTranscript) {
                const box = document.getElementById('live-transcript-text');
                box.value += (box.value ? ' ' : '') + finalTranscript;
                box.scrollTop = box.scrollHeight;
            }

            if (interimTranscript) {
                const box = document.getElementById('live-transcript-text');
                box.placeholder = interimTranscript + '...';
            }
        };

        speechRecognizer.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            if (event.error === 'not-allowed') {
                this.showSpeechError('Microphone access was denied. Please allow microphone access in your browser settings, then refresh the page and try again. For now, you can type your answer in the text box.');
            } else if (event.error === 'no-speech') {
                console.log('No speech detected - keep talking or type your answer');
            } else if (event.error === 'audio-capture') {
                this.showSpeechError('No microphone found. Please connect a microphone or type your answer in the text box below.');
            } else if (event.error === 'service-not-allowed') {
                this.showSpeechError('Speech recognition service is not allowed on this page. Try using a different browser or type your answer.');
            }
        };

        speechRecognizer.onend = () => {
            if (this.state.isRecording) {
                try {
                    setTimeout(() => {
                        speechRecognizer.start();
                    }, 100);
                } catch(e){}
            }
        };
    },

    /** Show speech recognition error message to user */
    showSpeechError(message) {
        const errorEl = document.getElementById('speech-error-msg');
        if (errorEl) {
            const textEl = document.getElementById('speech-error-text');
            if (textEl) {
                textEl.textContent = message;
            }
            errorEl.style.display = 'flex';
        }
    },

    /** Request microphone permission explicitly before starting recording */
    async requestMicrophonePermission() {
        if (window.proctor && proctor.hasLiveAudio()) return true;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach(track => track.stop());
            return true;
        } catch (error) {
            console.error('Microphone permission denied:', error);
            this.showSpeechError('Microphone access is required for speech-to-text. Please allow microphone access in your browser settings, then refresh the page. You can still type your answer manually.');
            return false;
        }
    },

    /** Start capturing the candidate's microphone amplitude for the avatar. */
    startMicCapture() {
        // micAnalyzer only exists once setupSpeechRecognition() has built it.
        if (this._micCapture || !micAnalyzer) return;
        // Reuse the (captured) speech-recognition stream if available.
        let stream = null;
        try {
            if (speechRecognizer && speechRecognizer.stream) stream = speechRecognizer.stream;
        } catch (e) { stream = null; }
        this._micCapture = micAnalyzer;
        micAnalyzer.start(stream, (amp) => {
            // Bind the microphone amplitude to the room so the avatar mouth
            // reacts to the candidate's actual voice.
            this.state._aiVoiceSample = Math.max(0, Math.min(1, amp));
        });
    },

    /** Stop the candidate-microphone amplitude capture. */
    stopMicCapture() {
        if (this._micCapture) {
            this._micCapture.stop();
            this._micCapture = null;
        }
    },

    renderQuestionTracker() {
        const dotsContainer = document.getElementById('call-progress-dots');
        if (dotsContainer) {
            dotsContainer.innerHTML = '';
            this.state.questions.forEach((q, idx) => {
                const dot = document.createElement('div');
                dot.className = 'progress-dot';
                dot.id = `progress-dot-${idx}`;
                dot.title = `Q${idx + 1}: ${q.category}`;
                dotsContainer.appendChild(dot);
            });
        }
    },

    loadQuestion(index) {
        this.state.currentIndex = index;
        this.state.secondsElapsed = 0;
        this.state.isRecording = false;

        // Reset controls & transcript view
        document.getElementById('live-transcript-text').value = '';
        document.getElementById('record-btn').innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>
            Record Answer
        `;
        document.getElementById('record-btn').className = 'btn btn-primary';
        const recIndicator = document.getElementById('recording-indicator');
        if (recIndicator) recIndicator.style.display = 'none';
        document.getElementById('time-readout').textContent = '00:00';

        // Update progress dots
        this.state.questions.forEach((q, idx) => {
            const dot = document.getElementById(`progress-dot-${idx}`);
            if (dot) {
                if (idx === index) {
                    dot.className = 'progress-dot active';
                } else if (idx < index) {
                    dot.className = 'progress-dot completed';
                } else {
                    dot.className = 'progress-dot';
                }
            }
        });

        // Set Teleprompter question
        const currentQ = this.state.questions[index];
        document.getElementById('teleprompter-text').textContent = currentQ.question_text;

        // Update new call UI elements
        const callRoleLabel = document.getElementById('call-role-label');
        if (callRoleLabel) callRoleLabel.textContent = `${this.state.role} Interview`;

        const callQuestionCounter = document.getElementById('call-question-counter');
        if (callQuestionCounter) callQuestionCounter.textContent = `Question ${index + 1} of ${this.state.questions.length}`;

        const categoryBadge = document.getElementById('question-category-badge');
        if (categoryBadge) categoryBadge.textContent = currentQ.category || 'General';

        const diffEl = document.getElementById('question-difficulty');
        if (diffEl) {
            const diff = currentQ.difficulty || 'Medium';
            const dotColor = diff === 'Hard' ? '#ef4444' : diff === 'Easy' ? '#34d399' : '#fbbf24';
            diffEl.innerHTML = `<span class="difficulty-dot" style="background:${dotColor}"></span> ${diff}`;
        }

        const roleSub = document.getElementById('interviewer-role-sub');
        if (roleSub) roleSub.textContent = `${this.state.role} Panel`;

        const candidateInitial = document.getElementById('candidate-initial');
        if (candidateInitial && this.state.role) candidateInitial.textContent = this.state.role.charAt(0).toUpperCase();

        // Voice Readout (TTS) for the question is handled by say() below,
        // which plays the utterance with boundary/end handlers for lip-sync.

        // ---- AI interviewer avatar (optional; never blocks start) ----
        try {
            // Move the avatar into the state where it is about to speak, then
            // hand it the exact question the TTS will read. say() owns the
            // speaking indicator, so nothing else should toggle it here.
            this.aiAboutToSpeak();
            this.say(currentQ.question_text).catch(() => {
                // say() is a fire-and-forget presenter. If the avatar layer
                // throws, we never want it to stop the interview.
            });
        } catch (animationError) {
            console.warn('[interview] AI avatar failed (non-blocking, interview continues):', animationError);
            // Keep the interview active; restore the avatar to idle.
            this._stopAllAIAnimations();
            this.setStatusLabel('AI Interviewer');
        }
        // ---- end AI interviewer avatar ----

        // Update Action buttons: "Next Question" or "Finish Interview"
        const actionBtn = document.getElementById('next-q-btn');
        if (index === this.state.questions.length - 1) {
            actionBtn.innerHTML = `
                Submit Interview
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
            `;
            actionBtn.onclick = () => this.finishInterview();
        } else {
            actionBtn.innerHTML = `
                Next Question
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
            `;
            actionBtn.onclick = () => this.nextQuestion();
        }
    },




    /** ------------------------------------------------------------------
     *  PUBLIC API — called by the room lifecycle + TTS callback
     *  ------------------------------------------------------------------
     */

    /** Update the status label text shown under the AI interviewer avatar. */
    setStatusLabel(text) {
        const el = document.getElementById('ai-status-label');
        if (el) el.textContent = text;
    },

    /** Clear avatar indicators and return to idle visuals. */
    _stopAllAIAnimations() {
        const indicator = document.getElementById('ai-speaking-indicator');
        if (indicator) {
            indicator.classList.remove('active', 'listening');
            indicator.style.display = 'none';
        }
        const respeaker = document.getElementById('ai-respeaker');
        if (respeaker) {
            respeaker.classList.remove('listening');
            respeaker.style.display = 'none';
        }
    },

    /** Called right before the AI speaks — reset timers then enter THINKING. */
    aiAboutToSpeak() {
        this._stopAllAIAnimations();
        this.state._aiState = 1; // THINKING
        this.setStatusLabel('Thinking...');
    },

    /** Stop the AI interviewer avatar and all timers. */
    aiStop() {
        this._stopAllAIAnimations();
        this.state._aiState = 0; // IDLE
        this.setStatusLabel('AI Interviewer');
    },

    /** Candidate started speaking — switch to LISTENING mode. */
    candidateStartedSpeaking() {
        this._stopAllAIAnimations();
        this.state._aiState = 3; // LISTENING
        const respeaker = document.getElementById('ai-respeaker');
        if (respeaker) {
            respeaker.classList.add('listening');
            respeaker.style.display = 'block';
        }
        this.setStatusLabel('Listening...');
    },

    /** Candidate finished answering — switch to THINKING mode. */
    candidateFinishedAnswering() {
        this._stopAllAIAnimations();
        this.state._aiState = 1; // THINKING
        const respeaker = document.getElementById('ai-respeaker');
        if (respeaker) {
            respeaker.classList.remove('listening');
            respeaker.style.display = 'none';
        }
        this.setStatusLabel('Thinking...');
    },

    /** Ask the AI to say something: enter SPEAKING and play TTS. */
    async say(text) {
        if (this.state._aiState === 2) return;
        this.state._aiState = 2; // SPEAKING
        this._stopAllAIAnimations();
        this.setStatusLabel('Speaking...');

        const indicator = document.getElementById('ai-speaking-indicator');
        if (indicator) {
            indicator.classList.add('active');
            indicator.style.display = 'flex';
        }

        // Token for "latest call wins": bump first, then capture, so the finish
        // handler below can tell whether a newer say() has superseded it.
        this.state._aiSayId++;
        const sayId = this.state._aiSayId;

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.95;
        utterance.pitch = 1.0;
        const voices = window.speechSynthesis.getVoices();
        const preferredVoice = voices.find(v => v.lang.startsWith('en') && v.name.includes('Google'));
        if (preferredVoice) utterance.voice = preferredVoice;

        // Safety net: some browsers/voices never fire onend (no audio device,
        // no TTS voice installed, speech cancelled), which would leave the
        // label stuck on "Speaking..." forever. Fall back to an estimated
        // reading duration so the tile always returns to its idle state.
        const fallbackMs = Math.min(60000, 3000 + String(text || '').length * 120);
        let fallbackTimer = null;

        await new Promise((resolve) => {
            const finish = () => {
                if (fallbackTimer) clearTimeout(fallbackTimer);
                if (sayId === this.state._aiSayId) {
                    this.state._aiState = 0; // IDLE
                    this.setStatusLabel('AI Interviewer');
                    if (indicator) {
                        indicator.classList.remove('active');
                        indicator.style.display = 'none';
                    }
                    this._stopAllAIAnimations();
                }
                resolve();
            };
            fallbackTimer = setTimeout(finish, fallbackMs);
            utterance.onend = finish;
            utterance.onerror = finish;
            window.speechSynthesis.speak(utterance);
        });
    },



    // -- private animation helpers --

    getFallbackQuestions(role) {
        const allFallback = [
            { id: 1, role: "Software Engineer", category: "Behavioral", question_text: "Tell me about a time you had a technical disagreement with a team member. How did you resolve it?", difficulty: "Medium", optimal_keywords: "compromise, discussion, collaboration, perspective, consensus", expected_concepts: "Resolving conflict, communication, constructive debate, technical compromise" },
            { id: 2, role: "Software Engineer", category: "Technical", question_text: "Can you explain the difference between a Relational Database (SQL) and a Non-Relational Database (NoSQL)?", difficulty: "Medium", optimal_keywords: "schema, ACID, scale, horizontal, vertical, structured, key-value, document", expected_concepts: "Database design, trade-offs, scaling properties" },
            { id: 5, role: "Software Engineer", category: "Technical", question_text: "What is Big O notation, and why is it important in algorithm design?", difficulty: "Easy", optimal_keywords: "time complexity, space complexity, scale, input size", expected_concepts: "Algorithmic efficiency, execution speed, scalability" },
            { id: 6, role: "Product Manager", category: "Behavioral", question_text: "Tell me about a time when a product launch didn't go as planned. What did you learn?", difficulty: "Hard", optimal_keywords: "post-mortem, customer feedback, metric, root cause, adaptation", expected_concepts: "Resilience, post-launch feedback loop, metrics tracking" },
            { id: 7, role: "Product Manager", category: "Technical", question_text: "How do you decide what features to prioritize when building a product roadmap?", difficulty: "Medium", optimal_keywords: "RICE, MoSCoW, value, effort, metrics, stakeholders", expected_concepts: "Roadmapping, value/effort scoring, customer needs analysis" },
            { id: 11, role: "Data Analyst", category: "Technical", question_text: "What is the difference between inner join, left join, and outer join in SQL?", difficulty: "Easy", optimal_keywords: "join, merge, null, matching rows, left table, right table", expected_concepts: "Data cleaning, table relationships, aggregation" },
            { id: 12, role: "Data Analyst", category: "Technical", question_text: "Explain the difference between correlation and causation.", difficulty: "Medium", optimal_keywords: "correlation, causation, variable, confounding factor", expected_concepts: "Data literacy, analytical bias, scientific method" },
            { id: 15, role: "Sales Executive", category: "Behavioral", question_text: "Describe a time you exceeded your quarterly sales target. What specific strategies did you use?", difficulty: "Medium", optimal_keywords: "prospecting, pipeline, closing, negotiation, relationship", expected_concepts: "Sales methodology, target achievement, strategic planning" },
            { id: 16, role: "Sales Executive", category: "Situational", question_text: "How would you handle a situation where a long-time client is considering switching to a competitor?", difficulty: "Hard", optimal_keywords: "retention, value proposition, feedback, solution, loyalty", expected_concepts: "Customer retention, consultative selling, competitive differentiation" },
            { id: 17, role: "Sales Executive", category: "Behavioral", question_text: "Tell me about a time you failed to close an important deal. What you learn?", difficulty: "Medium", optimal_keywords: "analysis, reflection, objection, follow-up, qualification", expected_concepts: "Learning from failure, sales process refinement" },
            { id: 18, role: "Marketing Manager", category: "Technical", question_text: "What marketing KPIs do you track to measure campaign effectiveness?", difficulty: "Medium", optimal_keywords: "ROI, CAC, LTV, conversion rate, CTR, impressions", expected_concepts: "Marketing analytics, campaign optimization, KPI tracking" },
            { id: 19, role: "Marketing Manager", category: "Behavioral", question_text: "Describe a successful marketing campaign you led from concept to execution.", difficulty: "Medium", optimal_keywords: "strategy, creative, execution, metrics, target audience", expected_concepts: "Campaign lifecycle, creative strategy, results measurement" },
            { id: 21, role: "HR Manager", category: "Behavioral", question_text: "Tell me about a time you handled a sensitive employee relations issue. How did you maintain confidentiality?", difficulty: "Hard", optimal_keywords: "confidentiality, fairness, investigation, policy, communication", expected_concepts: "Employee relations, conflict resolution, compliance" },
            { id: 22, role: "HR Manager", category: "Technical", question_text: "What strategies do you use to improve employee retention and reduce turnover?", difficulty: "Medium", optimal_keywords: "retention, engagement, culture, feedback, development", expected_concepts: "Talent management, employee engagement, retention strategies" },
            { id: 24, role: "Financial Analyst", category: "Technical", question_text: "Walk me through how you would build a financial model to evaluate a potential investment.", difficulty: "Hard", optimal_keywords: "DCF, NPV, IRR, assumptions, revenue projections, costs", expected_concepts: "Financial modeling, valuation methods, forecasting" },
            { id: 25, role: "Financial Analyst", category: "Technical", question_text: "What are the key financial statements every analyst should understand?", difficulty: "Medium", optimal_keywords: "income statement, balance sheet, cash flow, revenue, expenses", expected_concepts: "Financial accounting, statement analysis, GAAP principles" },
            { id: 27, role: "Operations Manager", category: "Behavioral", question_text: "Tell me about a time you improved an inefficient process in your organization.", difficulty: "Medium", optimal_keywords: "process improvement, efficiency, cost reduction, automation", expected_concepts: "Process optimization, operational efficiency, measurable impact" },
            { id: 28, role: "Operations Manager", category: "Situational", question_text: "A key supplier has suddenly gone bankrupt, threatening your production timeline. How do you respond?", difficulty: "Hard", optimal_keywords: "contingency planning, supplier diversification, communication", expected_concepts: "Supply chain management, crisis response, problem-solving" },
            { id: 30, role: "Customer Support Lead", category: "Behavioral", question_text: "Describe a time you handled an extremely angry customer. How did you de-escalate the situation?", difficulty: "Medium", optimal_keywords: "empathy, active listening, de-escalation, solution, patience", expected_concepts: "Customer service excellence, conflict de-escalation" },
            { id: 31, role: "Customer Support Lead", category: "Situational", question_text: "Your team's customer satisfaction scores have dropped by 10 points. How do you fix it?", difficulty: "Hard", optimal_keywords: "data analysis, team feedback, training, quality assurance", expected_concepts: "Quality management, team development, root cause analysis" },
            { id: 33, role: "Healthcare Administrator", category: "Behavioral", question_text: "Describe a time you had to manage a crisis in a healthcare setting.", difficulty: "Hard", optimal_keywords: "crisis management, patient safety, staffing, resource allocation", expected_concepts: "Healthcare operations, crisis leadership, team coordination" },
            { id: 34, role: "Healthcare Administrator", category: "Technical", question_text: "How do you ensure compliance with healthcare regulations such as HIPAA?", difficulty: "Medium", optimal_keywords: "compliance, HIPAA, privacy, audit, training, protocols", expected_concepts: "Healthcare regulations, compliance management" },
            { id: 36, role: "Project Manager", category: "Behavioral", question_text: "Tell me about a project that was falling behind schedule. How did you get back on track?", difficulty: "Medium", optimal_keywords: "schedule, risk mitigation, resources, communication", expected_concepts: "Project recovery, stakeholder management, adaptive planning" },
            { id: 37, role: "Project Manager", category: "Technical", question_text: "What project management methodologies have you used? Compare Agile, Scrum, and Waterfall.", difficulty: "Medium", optimal_keywords: "Agile, Scrum, Waterfall, sprint, iteration, documentation", expected_concepts: "Project methodology, lifecycle comparison, process selection" },
            { id: 39, role: "Business Analyst", category: "Technical", question_text: "How do you gather and document requirements from stakeholders?", difficulty: "Medium", optimal_keywords: "interviews, surveys, workshops, documentation, BRD, user stories", expected_concepts: "Requirements gathering, stakeholder elicitation" },
            { id: 40, role: "Business Analyst", category: "Behavioral", question_text: "Describe a time when you identified a gap between business needs and the proposed solution.", difficulty: "Medium", optimal_keywords: "gap analysis, solution assessment, communication, alternatives", expected_concepts: "Business process analysis, solution evaluation" },
            { id: 42, role: "Teacher/Educator", category: "Behavioral", question_text: "Describe a time you had to adapt your teaching style to accommodate a student with different learning needs.", difficulty: "Medium", optimal_keywords: "adaptation, differentiation, inclusive, engagement, assessment", expected_concepts: "Differentiated instruction, inclusive education" },
            { id: 43, role: "Teacher/Educator", category: "Situational", question_text: "How would you handle a classroom where students are disengaged and not participating?", difficulty: "Medium", optimal_keywords: "engagement strategies, interactive learning, rapport, feedback", expected_concepts: "Classroom management, student engagement" },
            { id: 45, role: "Retail Manager", category: "Behavioral", question_text: "Describe a time you improved the customer experience in your store.", difficulty: "Medium", optimal_keywords: "customer experience, sales growth, loyalty, service, training", expected_concepts: "Retail operations, customer experience management" },
            { id: 46, role: "Retail Manager", category: "Situational", question_text: "Your store is consistently missing its monthly sales targets. Walk me through your plan to turn performance around.", difficulty: "Hard", optimal_keywords: "sales strategy, training, inventory, promotions, staffing", expected_concepts: "Retail turnaround, performance management" },
            { id: 48, role: "Legal Associate", category: "Behavioral", question_text: "Describe a time you had to manage multiple high-priority cases with conflicting deadlines.", difficulty: "Medium", optimal_keywords: "prioritization, deadlines, organization, case management", expected_concepts: "Legal workflow management, time management" },
            { id: 49, role: "Legal Associate", category: "Technical", question_text: "What steps do you take to ensure legal documents are accurate and compliant?", difficulty: "Medium", optimal_keywords: "document review, compliance, accuracy, research", expected_concepts: "Legal documentation, regulatory compliance" },
            { id: 51, role: "Graphic Designer", category: "Behavioral", question_text: "Tell me about a time a client rejected your design concept. How did you handle it?", difficulty: "Medium", optimal_keywords: "feedback, revision, communication, client management, compromise", expected_concepts: "Design process, client communication, creative problem-solving" },
            { id: 52, role: "Graphic Designer", category: "Technical", question_text: "Walk me through your design process from client brief to final deliverable.", difficulty: "Medium", optimal_keywords: "wireframe, mockup, prototyping, Figma, Adobe, iteration", expected_concepts: "Design thinking, tools proficiency, workflow structure" },
            { id: 54, role: "Content Writer", category: "Behavioral", question_text: "Describe a time your content strategy significantly increased audience engagement.", difficulty: "Medium", optimal_keywords: "strategy, engagement, analytics, audience, SEO, storytelling", expected_concepts: "Content marketing, audience development, strategic writing" },
            { id: 55, role: "Content Writer", category: "Technical", question_text: "How do you approach SEO keyword research while maintaining high-quality content?", difficulty: "Medium", optimal_keywords: "SEO, keywords, readability, search intent, headers, meta", expected_concepts: "SEO writing, content optimization, reader experience" }
        ];

        // Filter for the requested role
        const roleQuestions = allFallback.filter(q => q.role === role);

        // If we have enough questions for this role, return them
        if (roleQuestions.length >= 3) {
            return roleQuestions.slice(0, 3);
        }

        // If no specific role questions found, generate generic ones
        if (roleQuestions.length === 0) {
            return [
                {
                    id: -1,
                    role: role,
                    category: "Behavioral",
                    question_text: `Tell me about a time you demonstrated leadership skills in a ${role} context. What was the outcome?`,
                    difficulty: "Medium",
                    optimal_keywords: "leadership, team, outcome, initiative, responsibility",
                    expected_concepts: "Leadership experience, team collaboration, measurable results"
                },
                {
                    id: -2,
                    role: role,
                    category: "Technical",
                    question_text: `What are the most important skills and tools for a ${role} to master, and why?`,
                    difficulty: "Medium",
                    optimal_keywords: "skills, tools, proficiency, expertise, best practices",
                    expected_concepts: "Domain knowledge, tool proficiency, continuous learning"
                },
                {
                    id: -3,
                    role: role,
                    category: "Situational",
                    question_text: `As a ${role}, you are given a project with limited resources and a tight deadline. How do you prioritize?`,
                    difficulty: "Medium",
                    optimal_keywords: "prioritization, resource management, deadline, efficiency",
                    expected_concepts: "Resource allocation, priority setting, time management"
                }
            ];
        }

        return roleQuestions;
    },

    renderMockFeedback() {
        const answerResults = this.state.answers.map((ans, idx) => {
            const localScore = this.localScoreAnswer(ans.transcript, {
                optimal_keywords: ans.optimal_keywords,
                expected_concepts: ans.expected_concepts
            });

            const score = localScore.score;
            const clarity = Math.min(100, score + Math.floor(Math.random() * 10) - 5);
            const grammar = Math.min(100, score + Math.floor(Math.random() * 8) - 4);
            const relevance = Math.min(100, score + Math.floor(Math.random() * 12) - 6);
            const fillerCount = Math.max(0, Math.floor(Math.random() * 5));

            return {
                question_text: ans.question_text,
                category: ans.category,
                transcript: ans.transcript || 'No response recorded.',
                score: score,
                feedback: {
                    score: score,
                    clarity: Math.max(0, clarity),
                    grammar: Math.max(0, grammar),
                    relevance: Math.max(0, relevance),
                    filler_count: fillerCount,
                    strengths: score >= 70 ? ["Good structure in your response", "Relevant points covered"] : ["Attempted to address the question"],
                    weaknesses: score < 70 ? ["Answer could be more detailed", "Try using specific examples"] : ["Could include more metrics"],
                    tips: score < 50 ? ["Elaborate more — aim for at least 30-40 words", "Use the STAR method to structure your answer"] : ["Great effort! Try to include more concrete examples next time"]
                }
            };
        });

        const totalScore = answerResults.reduce((sum, a) => sum + a.score, 0);
        const overallScore = answerResults.length > 0 ? Math.round(totalScore / answerResults.length) : 0;

        let summary = '';
        if (overallScore >= 80) {
            summary = "Good execution. Your answers were relevant and well-structured. Continue practicing to refine your delivery and include more specific metrics.";
        } else if (overallScore >= 60) {
            summary = "Decent attempt. Your answers covered the basics but could benefit from more detail and structure. Focus on using the STAR method and providing concrete examples.";
        } else {
            summary = "Your responses need more development. Try to elaborate more in your answers, use specific examples from your experience, and structure your responses clearly.";
        }

        const mockResult = {
            id: Date.now(),
            role: this.state.role,
            overall_score: overallScore,
            summary: summary,
            date: new Date().toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' }),
            answers: answerResults
        };
        app.showFeedbackDetail(mockResult);
    },

    /** Setup Q&A functionality for resume-based interviews */
    setupResumeQA(resumeText) {
        this.state.resumeText = resumeText;

        const openQaBtn = document.getElementById('open-qa-btn');
        const qaSection = document.getElementById('resume-qa-section');
        const closeQaBtn = document.getElementById('close-qa-section');
        const askQaBtn = document.getElementById('ask-qa-btn');
        const qaInput = document.getElementById('qa-input');

        openQaBtn.style.display = 'block';

        openQaBtn.addEventListener('click', () => {
            qaSection.style.display = 'block';
        });

        closeQaBtn.addEventListener('click', () => {
            qaSection.style.display = 'none';
        });

        askQaBtn.addEventListener('click', () => {
            this.askResumeQuestion();
        });

        qaInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.askResumeQuestion();
            }
        });
    },

    /** Ask a question about the resume */
    async askResumeQuestion() {
        const qaInput = document.getElementById('qa-input');
        const qaChat = document.getElementById('resume-qa-chat');
        const question = qaInput.value.trim();
        if (!question) return;

        const userMsgDiv = document.createElement('div');
        userMsgDiv.className = 'resume-qa-message resume-qa-question';
        userMsgDiv.innerHTML = `<strong>You:</strong> ${question}`;
        qaChat.appendChild(userMsgDiv);
        qaChat.scrollTop = qaChat.scrollHeight;

        qaInput.value = '';

        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'resume-qa-message';
        loadingDiv.style.cssText = 'padding: 0.5rem; color: var(--text-muted); font-style: italic;';
        loadingDiv.textContent = 'AI is thinking...';
        qaChat.appendChild(loadingDiv);
        qaChat.scrollTop = qaChat.scrollHeight;

        try {
            const response = await api.askResumeQuestion(question, this.state.resumeText);

            loadingDiv.remove();

            const aiMsgDiv = document.createElement('div');
            aiMsgDiv.className = 'resume-qa-message resume-qa-answer';
            aiMsgDiv.innerHTML = `<strong>AI Interviewer:</strong> ${response.answer}`;
            qaChat.appendChild(aiMsgDiv);
            qaChat.scrollTop = qaChat.scrollHeight;

            if ('speechSynthesis' in window) {
                this.speakQAAnswer(response.answer);
            }
        } catch (error) {
            loadingDiv.remove();
            const errorMsgDiv = document.createElement('div');
            errorMsgDiv.className = 'resume-qa-message resume-qa-answer';
            errorMsgDiv.innerHTML = `<strong>AI Interviewer:</strong> Sorry, I couldn't process your question. Please try again.`;
            qaChat.appendChild(errorMsgDiv);
            qaChat.scrollTop = qaChat.scrollHeight;
        }
    },

    /** Speak the Q&A answer using text-to-speech */
    speakQAAnswer(text) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.95;
        utterance.pitch = 1.0;

        const voices = window.speechSynthesis.getVoices();
        const preferredVoice = voices.find(v => v.lang.startsWith('en') && v.name.includes('Google'));
        if (preferredVoice) utterance.voice = preferredVoice;

        window.speechSynthesis.speak(utterance);
    },

    /** Speak an interview question to the candidate via text-to-speech. */
    speakQuestion(text) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.95;
        utterance.pitch = 1.0;

        const voices = window.speechSynthesis.getVoices();
        const preferredVoice = voices.find(v => v.lang.startsWith('en') && v.name.includes('Google'));
        if (preferredVoice) utterance.voice = preferredVoice;

        window.speechSynthesis.speak(utterance);
    },

    /** Stop the current live recording and reset the recording UI controls. */
    stopRecording() {
        this.state.isRecording = false;

        const btn = document.getElementById('record-btn');
        if (btn) btn.classList.remove('recording');
        const label = document.getElementById('record-btn-label');
        if (label) label.textContent = 'Record';

        const recBadge = document.getElementById('call-recording-badge');
        if (recBadge) recBadge.style.display = 'none';

        const waveform = document.getElementById('candidate-waveform');
        if (waveform) waveform.style.display = 'none';

        this.stopTimer();

        if (speechRecognizer) {
            try { speechRecognizer.stop(); } catch (e) {}
        }

        // Stop the mic amplitude capture (so no more avatar amplitude).
        this.stopMicCapture();

        // Stop the AI interviewer avatar (if it is animating).
        this.aiStop();
    },

    /* ------------------------------------------------------------------
     *  SESSION CONTROL SURFACE
     *  The implementations live as module-scope functions (they all
     *  operate on this singleton). They are surfaced here as instance
     *  methods because the room is driven through `interview.<name>()` —
     *  from inline HTML handlers (onclick="interview.toggleRecording()")
     *  and from the app shell (app.js).
     *  ------------------------------------------------------------------ */

    saveCurrentResponse() { return saveCurrentResponse(); },

    nextQuestion() { return nextQuestion(); },

    finishInterview() { return finishInterview(); },

    startRecording() { return startRecording(); },

    toggleRecording() { return toggleRecording(); },

    autoGradeCurrentAnswer() { return autoGradeCurrentAnswer(); },

    startTimer() { return startTimer(); },

    stopTimer() { return stopTimer(); },

    stopWebcam() { return stopWebcam(); },

    localScoreAnswer(transcript, question) { return localScoreAnswer(transcript, question); },

    getScoreClass(score) { return getScoreClass(score); },

    /** Remove the "live interview" markers added when the room starts. */
    clearLiveNavState() {
        const view = document.getElementById('interview-view');
        if (view) delete view.dataset.sessionActive;
        document.querySelectorAll('.nav-live-link').forEach(l => {
            l.classList.remove('is-live');
            if (l.parentElement) l.parentElement.classList.remove('active');
            const dot = l.querySelector('.live-dot');
            if (dot) dot.remove();
        });
    }
};

// Expose the interview instance on the global window object so the app shell
// (app.js) can drive it: interview.toggleRecording(), etc.
window.interview = interview;

// ── Top-level functions (kept outside the object for HTML wiring) ──

async function toggleRecording() {
    if (interview.state.isRecording) {
        interview.stopRecording();
        interview.autoGradeCurrentAnswer();
    } else {
        const micGranted = await interview.requestMicrophonePermission();
        if (micGranted) {
            interview.startRecording();
        } else {
            interview.showSpeechError('Microphone permission is required for speech-to-text. After allowing access in browser settings, click "Record Answer" again. You can also type your answer manually.');
        }
    }
}

async function startRecording() {
    interview.state.isRecording = true;

    // The candidate has started speaking through the microphone, so the
    // AI interviewer avatar switches to LISTENING posture.
    interview.candidateStartedSpeaking();

    const btn = document.getElementById('record-btn');
    btn.classList.add('recording');
    const label = document.getElementById('record-btn-label');
    if (label) label.textContent = 'Stop';

    const recBadge = document.getElementById('call-recording-badge');
    if (recBadge) recBadge.style.display = 'flex';

    const waveform = document.getElementById('candidate-waveform');
    if (waveform) waveform.style.display = 'flex';

    const errorEl = document.getElementById('speech-error-msg');
    if (errorEl) errorEl.style.display = 'none';

    interview.startTimer();

    // Start the microphone amp capture so the AI interviewer avatar can
    // react to the candidate's voice.
    interview.startMicCapture();

    if (speechRecognizer) {
        try {
            speechRecognizer.start();
            console.log('Speech recognition started successfully');
        } catch(e) {
            console.error("SpeechRecognition failed to start:", e);
            interview.showSpeechError('Failed to start speech recognition. Try typing your answer in the text box instead.');
        }
    } else {
        console.warn('Speech recognition not available - user must type manually');
    }
}

function stopRecording() {
    interview.stopRecording();
    interview.stopMicCapture();
}

async function autoGradeCurrentAnswer() {
    // The candidate has finished their answer. The interviewer avatar
    // switches to THINKING while the answer is processed.
    interview.candidateFinishedAnswering();

    const transcript = document.getElementById('live-transcript-text').value.trim();
    if (!transcript) return;

    const currentQ = interview.state.questions[interview.state.currentIndex];

    const feedbackEl = document.createElement('div');
    feedbackEl.className = 'auto-grade-feedback';
    feedbackEl.id = 'auto-grade-indicator';
    feedbackEl.innerHTML = `
        <div class="grade-loading">
            <div class="grade-spinner"></div>
            <span>Analyzing your response...</span>
        </div>
    `;

    const transcriptBody = document.querySelector('.transcript-panel-body');
    if (transcriptBody) transcriptBody.appendChild(feedbackEl);
    else {
        const panel = document.getElementById('call-transcript-panel');
        if (panel) panel.appendChild(feedbackEl);
    }

    try {
        const gradeResult = await api.autoGrade({
            transcript: transcript,
            question_text: currentQ.question_text,
            category: currentQ.category,
            optimal_keywords: currentQ.optimal_keywords || '',
            expected_concepts: currentQ.expected_concepts || ''
        });
        if (!gradeResult || gradeResult.error) {
            throw new Error(gradeResult?.error || 'Auto-grade failed');
        }

        feedbackEl.innerHTML = `
            <div class="grade-result">
                <div class="grade-header">
                    <span class="grade-label">Your Response Score</span>
                    <span class="grade-score ${interview.getScoreClass(gradeResult.score)}">${gradeResult.score}%</span>
                </div>
                <div class="grade-subscores">
                    <div class="grade-sub">
                        <span>Clarity</span>
                        <span>${gradeResult.clarity}%</span>
                    </div>
                    <div class="grade-sub">
                        <span>Relevance</span>
                        <span>${gradeResult.relevance}%</span>
                    </div>
                </div>
                <div class="grade-tips" style="margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid var(--border-glass);">
                    <span style="font-size: 0.85rem;">${gradeResult.strengths && gradeResult.strengths.length > 0 ? '✅ ' + gradeResult.strengths[0] : '💡 Good start! Keep practicing.'}</span>
                </div>
            </div>
        `;

        setTimeout(() => {
            if (feedbackEl) feedbackEl.remove();
        }, 10000);

    } catch (error) {
        console.log('Auto-grade via backend failed, using local scoring:', error);

        const localScore = interview.localScoreAnswer(transcript, currentQ);

        feedbackEl.innerHTML = `
            <div class="grade-result">
                <div class="grade-header">
                    <span class="grade-label">Quick Assessment</span>
                    <span class="grade-score ${interview.getScoreClass(localScore.score)}">${localScore.score}%</span>
                </div>
                <div class="grade-tips" style="margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid var(--border-glass);">
                    <span style="font-size: 0.85rem;">✅ ${localScore.tip}</span>
                </div>
            </div>
        `;

        setTimeout(() => {
            if (feedbackEl) feedbackEl.remove();
        }, 8000);
    }
}

function localScoreAnswer(transcript, question) {
    const wordCount = transcript.split(/\s+/).length;

    let score = 40;
    if (wordCount >= 15) score = 55;
    if (wordCount >= 25) score = 65;
    if (wordCount >= 35) score = 72;
    if (wordCount >= 50) score = 78;
    if (wordCount >= 70) score = 85;

    if (question.optimal_keywords) {
        const keywords = question.optimal_keywords.split(',').map(k => k.trim().toLowerCase());
        const transcriptLower = transcript.toLowerCase();
        const matches = keywords.filter(k => transcriptLower.includes(k));
        score += Math.min(10, matches.length * 2);
    }

    if (wordCount < 10) score = Math.max(25, score - 5);
    score = Math.min(92, score);

    let tip = 'Good response! You addressed the key points.';
    if (wordCount < 20) tip = 'Nice start! Try adding more details to elaborate on your answer.';
    if (wordCount < 10) tip = 'Try to speak for a bit longer to give more context to your answer.';
    if (score >= 75) tip = 'Excellent response! You covered the key points well.';

    return { score, tip };
}

function getScoreClass(score) {
    if (score >= 80) return 'high';
    if (score >= 65) return 'mid';
    return 'low';
}

function startTimer() {
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        interview.state.secondsElapsed++;
        const mins = Math.floor(interview.state.secondsElapsed / 60).toString().padStart(2, '0');
        const secs = (interview.state.secondsElapsed % 60).toString().padStart(2, '0');
        document.getElementById('time-readout').textContent = `${mins}:${secs}`;
        const largeTimer = document.getElementById('call-duration-large');
        if (largeTimer) largeTimer.textContent = `${mins}:${secs}`;
    }, 1000);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function saveCurrentResponse() {
    const text = document.getElementById('live-transcript-text').value.trim();
    const currentQ = interview.state.questions[interview.state.currentIndex];

    interview.state.answers.push({
        question_id: currentQ.id,
        question_text: currentQ.question_text,
        category: currentQ.category,
        optimal_keywords: currentQ.optimal_keywords || '',
        expected_concepts: currentQ.expected_concepts || '',
        transcript: text
    });
}

function nextQuestion() {
    interview.saveCurrentResponse();
    interview.stopRecording();
    interview.loadQuestion(interview.state.currentIndex + 1);
}

async function finishInterview() {
    interview.saveCurrentResponse();
    interview.stopRecording();
    interview.stopWebcam();
    if (window.proctor) proctor.endSession();

    const integrityReport = antiCheat.getIntegrityReport();
    antiCheat.stopMonitoring();
    interview.clearLiveNavState();

    app.resetTopbarContext();

    if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
    }

    app.showLoader('Analyzing transcript answers, calculating clarity subscores, and compiling actionable tips...');

    try {
        const result = await api.submitInterview(interview.state.role, interview.state.answers);

        if (integrityReport.is_flagged) {
            result.integrity_note = `⚠️ This session had ${integrityReport.tab_switches} tab switch(es) detected. Scores may be marked for integrity review.`;
        }

        app.hideLoader();
        app.showFeedbackDetail(result);
    } catch (error) {
        console.error('Failed to submit interview details:', error);
        app.hideLoader();
        interview.renderMockFeedback();
    }
}

/** Stop the local camera track. Kept safe: the original definition was
 *  referenced but never shipped, so without this the room would throw
 *  when leaving the interview view. */
function stopWebcam() {
    if (!videoStream) return;
    try {
        videoStream.getTracks().forEach(track => track.stop());
    } catch (e) {}
    videoStream = null;

    const videoElement = document.getElementById('webcam-feed');
    const placeholder = document.getElementById('webcam-placeholder');
    const avatar = document.getElementById('mock-avatar-interview');
    if (videoElement) videoElement.style.display = 'none';
    if (placeholder) placeholder.style.display = 'flex';
    if (avatar && interview.state.role) { avatar.textContent = interview.state.role.charAt(0).toUpperCase(); }
    if (window.proctor) proctor.endSession();
}

