/**
 * FieldVision Frontend Application
 * Handles camera/microphone capture, WebSocket communication, and UI updates
 */

class FieldVisionApp {
    constructor() {
        // Configuration - Optimized for lower bandwidth
        this.config = {
            wsUrl: `ws://${window.location.host}/ws?token=${localStorage.getItem('fv_token') || ''}`,
            frameRate: 1,           // 1 FPS for video (can reduce to 0.5 for slower connections)
            jpegQuality: 0.6,       // Reduced from 0.85 for faster transfer
            audioSampleRate: 16000,
            audioBitDepth: 16,
            maxVideoWidth: 640,     // Max resolution constraints
            maxVideoHeight: 480,
        };

        // State
        this.state = {
            connected: false,
            sessionActive: false,
            sessionId: null,
            sessionStartTime: null,
            muted: false,
            videoEnabled: true,
            eventCount: 0,
            criticalCount: 0,
            warningCount: 0,
        };

        // WebSocket
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;

        // Media
        this.mediaStream = null;
        this.audioContext = null;
        this.audioWorklet = null;
        this.videoCapture = null;
        this.frameInterval = null;

        // Audio playback
        this.audioQueue = [];
        this.isPlayingAudio = false;

        // DOM Elements
        this.elements = {};

        // Initialize
        this.init();
    }

    init() {
        this.cacheElements();
        this.bindEvents();
        this.startDurationTimer();
    }

    cacheElements() {
        this.elements = {
            // Status
            connectionStatus: document.getElementById('connectionStatus'),
            statusIndicator: document.getElementById('statusIndicator'),
            statusText: document.getElementById('statusText'),
            recordingBadge: document.getElementById('recordingBadge'),

            // Video
            videoPreview: document.getElementById('videoPreview'),
            videoPlaceholder: document.getElementById('videoPlaceholder'),
            captureCanvas: document.getElementById('captureCanvas'),

            // Controls
            startSessionBtn: document.getElementById('startSessionBtn'),
            endSessionBtn: document.getElementById('endSessionBtn'),
            toggleMuteBtn: document.getElementById('toggleMuteBtn'),
            toggleVideoBtn: document.getElementById('toggleVideoBtn'),
            micOnIcon: document.getElementById('micOnIcon'),
            micOffIcon: document.getElementById('micOffIcon'),
            videoOnIcon: document.getElementById('videoOnIcon'),
            videoOffIcon: document.getElementById('videoOffIcon'),
            textInput: document.getElementById('textInput'),
            sendTextBtn: document.getElementById('sendTextBtn'),
            newTopicBtn: document.getElementById('newTopicBtn'),

            // Output
            responseContainer: document.getElementById('responseContainer'),
            eventsContainer: document.getElementById('eventsContainer'),

            // Stats
            sessionDuration: document.getElementById('sessionDuration'),
            eventCount: document.getElementById('eventCount'),
            criticalCount: document.getElementById('criticalCount'),
            warningCount: document.getElementById('warningCount'),

            // Reports
            reportsBtn: document.getElementById('reportsBtn'),
            reportsModal: document.getElementById('reportsModal'),
            closeReportsBtn: document.getElementById('closeReportsBtn'),
            closeReportsBackdrop: document.getElementById('closeReportsBackdrop'),
            refreshReportsBtn: document.getElementById('refreshReportsBtn'),
            reportsList: document.getElementById('reportsList'),


        };
    }

    bindEvents() {
        this.elements.startSessionBtn.addEventListener('click', () => this.startSession());
        this.elements.endSessionBtn.addEventListener('click', () => this.endSession());
        this.elements.toggleMuteBtn.addEventListener('click', () => this.toggleMute());
        this.elements.toggleVideoBtn.addEventListener('click', () => this.toggleVideo());
        this.elements.sendTextBtn.addEventListener('click', () => this.sendText());
        this.elements.textInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.sendText();
        });

        if (this.elements.newTopicBtn) {
            this.elements.newTopicBtn.addEventListener('click', () => this.resetContext());
        }

        // Reports
        if (this.elements.reportsBtn) {
            this.elements.reportsBtn.addEventListener('click', () => this.toggleReports(true));
            this.elements.closeReportsBtn.addEventListener('click', () => this.toggleReports(false));
            this.elements.closeReportsBackdrop.addEventListener('click', () => this.toggleReports(false));
            this.elements.refreshReportsBtn.addEventListener('click', () => this.fetchReports());
        }


    }

    // ==================== WebSocket ====================

    connectWebSocket() {
        this.shouldReconnect = true;
        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(`ws://${window.location.host}/ws?token=${localStorage.getItem('fv_token') || ''}`);

            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.reconnectAttempts = 0;
                this.updateConnectionStatus(true);
                resolve();
            };

            this.ws.onclose = (event) => {
                console.log(`WebSocket disconnected. Code: ${event.code}, Reason: ${event.reason}, Clean: ${event.wasClean}`);
                this.updateConnectionStatus(false);

                // standard close is 1000, going away is 1001
                // irregular codes often mean timeout/network error
                if (event.code !== 1000) {
                    this.handleDisconnect();
                } else if (this.state.sessionActive) {
                    // Even if clean close, if session was active, something is wrong
                    this.handleDisconnect();
                }
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                reject(error);
            };

            this.ws.onmessage = (event) => {
                this.handleMessage(JSON.parse(event.data));
            };
        });
    }

    handleDisconnect() {
        if (!this.shouldReconnect) return;

        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;

            // If intentional reset, show friendly message
            if (this.isResetting) {
                this.updateStatus('Starting new topic...');
                this.isResetting = false;
            } else {
                this.updateStatus(`Connection lost. Reconnecting... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
                this.addResponse(`⚠️ Connection lost. attempting to reconnect...`, 'system');
            }

            // Exponential backoff or immediate retry
            let delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 5000);

            if (this.isResetting) {
                delay = 100; // Fast retry for manual reset
                this.isResetting = false;
            }

            setTimeout(() => this.reconnectSession(), delay);
        } else {
            this.updateStatus('Connection failed. Please refresh.');
            this.state.sessionActive = false;
            this.addResponse(`❌ Connection failed permanently. Please refresh the page.`, 'system');
            this.cleanupSession();
        }
    }

    sendMessage(type, payload = {}) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type, payload }));
        }
    }

    handleMessage(message) {
        const handlers = {
            'session_started': (payload) => this.onSessionStarted(payload),
            'session_ended': (payload) => this.onSessionEnded(payload),
            'audio_response': (payload) => this.onAudioResponse(payload),
            'text_response': (payload) => this.onTextResponse(payload),
            'tool_call': (payload) => this.onToolCall(payload),
            'turn_complete': (payload) => this.onTurnComplete(payload),
            'error': (payload) => this.onError(payload),
            'status': (payload) => this.onStatus(payload),
        };

        const handler = handlers[message.type];
        if (handler) {
            handler(message.payload);
        } else {
            console.warn('Unknown message type:', message.type);
        }
    }

    async reconnectSession() {
        try {
            console.log("Attempting to reconnect session...");
            await this.connectWebSocket();

            // Ensure media is ready (should reuse existing)
            await this.setupMedia();

            // Resume session
            this.sendMessage('start_session', {
                system_instruction: null,
                manual_context: null,
            });

            console.log("Reconnect handshake sent.");

        } catch (error) {
            console.error("Reconnect attempt failed:", error);
            // Trigger next retry cycle
            this.handleDisconnect();
        }
    }

    // ==================== Session Management ====================

    async startSession() {
        try {
            this.updateStatus('Connecting...');

            // Connect WebSocket
            await this.connectWebSocket();

            // Request media access
            await this.setupMedia();

            // Send start session message
            this.sendMessage('start_session', {
                system_instruction: null,  // Use default
                manual_context: null,       // Optional: add technical manual here
            });

        } catch (error) {
            console.error('Failed to start session:', error);
            this.showError('Failed to start session: ' + error.message);
        }
    }

    onSessionStarted(payload) {
        this.state.sessionActive = true;
        this.state.sessionId = payload.session_id;
        this.state.sessionStartTime = Date.now();

        // Update UI
        this.elements.startSessionBtn.classList.add('hidden');
        this.elements.endSessionBtn.classList.remove('hidden');

        if (this.elements.newTopicBtn) {
            this.elements.newTopicBtn.classList.remove('hidden');
            this.elements.newTopicBtn.disabled = false;
        }

        this.elements.recordingBadge.classList.remove('hidden');
        this.elements.textInput.disabled = false;
        this.elements.sendTextBtn.disabled = false;

        // Start video capture
        this.startVideoCapture();

        // Clear previous content
        this.elements.responseContainer.innerHTML = '';
        this.elements.eventsContainer.innerHTML = '';
        this.resetStats();

        this.addResponse('FieldVision AI connected. I\'m monitoring your work area for safety hazards. How can I assist you?', 'ai');
        this.updateStatus('Monitoring');
    }

    async endSession() {
        this.shouldReconnect = false;
        // Send end message to server (best effort)
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.sendMessage('end_session', {});
        }

        // Immediately clean up local state - don't wait for server response
        this.cleanupSession();
    }

    cleanupSession() {
        // Update state
        this.state.sessionActive = false;
        this.state.sessionId = null;

        // Stop media
        this.stopVideoCapture();
        this.stopMedia();

        // Update UI
        this.elements.startSessionBtn.classList.remove('hidden');
        this.elements.endSessionBtn.classList.add('hidden');
        if (this.elements.newTopicBtn) this.elements.newTopicBtn.classList.add('hidden');

        this.elements.recordingBadge.classList.add('hidden');
        this.elements.textInput.disabled = true;
        this.elements.sendTextBtn.disabled = true;

        // Close WebSocket
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        this.updateStatus('Session ended');
        this.addResponse('Session ended.', 'system');
    }

    onSessionEnded(payload) {
        // Server confirmed session end - show summary if available
        if (payload.summary) {
            this.addResponse(`Events logged: ${payload.summary.total_events}`, 'system');
        }

        // Ensure cleanup happened (in case this arrives before user clicks end)
        if (this.state.sessionActive) {
            this.cleanupSession();
        }
    }

    // ==================== Media Setup ====================

    async setupMedia() {
        // Reuse existing stream if active
        if (this.mediaStream && this.mediaStream.active) {
            console.log("Reusing existing media stream");
            return;
        }

        try {
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                video: {
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                    facingMode: 'environment'
                },
                audio: {
                    sampleRate: this.config.audioSampleRate,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            });

            // Setup video preview
            this.elements.videoPreview.srcObject = this.mediaStream;
            this.elements.videoPlaceholder.classList.add('hidden');

            // Setup audio processing
            await this.setupAudioProcessing();

        } catch (error) {
            throw new Error('Camera/microphone access denied: ' + error.message);
        }
    }

    async setupAudioProcessing() {
        this.audioContext = new AudioContext({ sampleRate: this.config.audioSampleRate });

        try {
            await this.audioContext.audioWorklet.addModule('/static/pcm-processor.js');
        } catch (e) {
            console.error('Failed to load audio worklet:', e);
            throw e;
        }

        const source = this.audioContext.createMediaStreamSource(this.mediaStream);
        const processor = new AudioWorkletNode(this.audioContext, 'pcm-processor');

        processor.port.onmessage = (event) => {
            if (this.state.sessionActive && !this.state.muted) {
                // pcmData is already Int16Array buffer from worklet
                const base64 = this.arrayBufferToBase64(event.data.pcmData);
                this.sendMessage('audio_data', { data: base64 });
            }
        };

        source.connect(processor);
        // source.connect(this.audioContext.destination); // Monitor audio locally? No, echo loop.
        processor.connect(this.audioContext.destination); // Keep graph alive

        this.audioWorklet = processor;
    }

    stopMedia() {
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }

        if (this.audioWorklet) {
            this.audioWorklet.disconnect();
            this.audioWorklet = null;
        }

        // Clean up UI
        this.elements.videoPreview.srcObject = null;
        this.elements.videoPlaceholder.classList.remove('hidden');
    }

    // ==================== Video Capture ====================

    startVideoCapture() {
        // Capture frames at configured frame rate
        this.frameInterval = setInterval(() => {
            this.captureAndSendFrame();
        }, 1000 / this.config.frameRate);
    }

    stopVideoCapture() {
        if (this.frameInterval) {
            clearInterval(this.frameInterval);
            this.frameInterval = null;
        }
    }

    captureAndSendFrame() {
        if (!this.state.sessionActive || !this.state.videoEnabled) return;

        const video = this.elements.videoPreview;
        const canvas = this.elements.captureCanvas;

        if (video.readyState < 2) return;  // Not ready

        // Scale down if needed for optimization
        let width = video.videoWidth;
        let height = video.videoHeight;

        if (width > this.config.maxVideoWidth) {
            const scale = this.config.maxVideoWidth / width;
            width = this.config.maxVideoWidth;
            height = Math.floor(height * scale);
        }

        if (height > this.config.maxVideoHeight) {
            const scale = this.config.maxVideoHeight / height;
            height = this.config.maxVideoHeight;
            width = Math.floor(width * scale);
        }

        canvas.width = width;
        canvas.height = height;

        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, width, height);

        canvas.toBlob((blob) => {
            if (blob && blob.size < 512 * 1024) {  // Skip if > 512KB
                const reader = new FileReader();
                reader.onload = () => {
                    const base64 = reader.result.split(',')[1];
                    this.sendMessage('video_frame', { data: base64 });
                };
                reader.readAsDataURL(blob);
            }
        }, 'image/jpeg', this.config.jpegQuality);
    }

    // ==================== Audio Playback ====================

    async onAudioResponse(payload) {
        const audioData = this.base64ToArrayBuffer(payload.data);
        this.audioQueue.push(audioData);

        if (!this.isPlayingAudio) {
            this.playNextAudio();
        }
    }

    async playNextAudio() {
        if (this.audioQueue.length === 0) {
            this.isPlayingAudio = false;
            return;
        }

        this.isPlayingAudio = true;
        const audioData = this.audioQueue.shift();

        try {
            // Create audio context for playback at 24kHz
            const playbackContext = new AudioContext({ sampleRate: 24000 });

            // Convert PCM16 to Float32
            const int16Array = new Int16Array(audioData);
            const float32Array = new Float32Array(int16Array.length);

            for (let i = 0; i < int16Array.length; i++) {
                float32Array[i] = int16Array[i] / 32768.0;
            }

            // Create audio buffer
            const audioBuffer = playbackContext.createBuffer(1, float32Array.length, 24000);
            audioBuffer.getChannelData(0).set(float32Array);

            // Play
            const source = playbackContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(playbackContext.destination);
            source.onended = () => {
                playbackContext.close();
                this.playNextAudio();
            };
            source.start();

        } catch (error) {
            console.error('Audio playback error:', error);
            this.playNextAudio();
        }
    }

    // ==================== Response Handling ====================

    onTextResponse(payload) {
        this.addResponse(payload.text, 'ai');
    }

    onToolCall(payload) {
        // Handle safety event tool calls
        if (payload.function === 'log_safety_event') {
            this.addSafetyEvent(payload.arguments);
        } else if (payload.function === 'create_work_order') {
            this.addResponse(`🛠️ **Maintenance Request**: Work order initiated for **${payload.arguments.equipment_id}** (${payload.arguments.priority} priority). Pending badge verification...`, 'system');
        } else if (payload.function === 'verify_badge') {
            this.addResponse(`🆔 **Badge Scan**: Verifying ID for **${payload.arguments.employee_name}**...`, 'system');
        }
    }

    onTurnComplete(payload) {
        console.log('Turn complete:', payload);
        this.updateStatus('Ready');

        // Re-enable input for next turn
        this.elements.textInput.disabled = false;
        this.elements.sendTextBtn.disabled = false;
        this.elements.textInput.placeholder = "Type a question...";
        this.elements.textInput.focus();
    }

    onError(payload) {
        const errorMsg = payload.error || 'Unknown error';

        // Check if this is a timeout/keepalive error that we can auto-recover from
        if (errorMsg.includes('keepalive') || errorMsg.includes('timeout') || errorMsg.includes('1011')) {
            console.log('Session timeout detected, attempting auto-reconnect...');
            // Trigger reconnect flow instead of just showing error
            this.handleDisconnect();
        } else {
            // For other errors, show to user
            this.showError(errorMsg);
        }
    }

    onStatus(payload) {
        this.updateStatus(payload.message);
    }

    // ==================== UI Updates ====================

    addResponse(text, source = 'ai') {
        const container = this.elements.responseContainer;

        // Remove placeholder if present
        const placeholder = container.querySelector('.italic');
        if (placeholder) placeholder.remove();

        const div = document.createElement('div');
        div.className = `p-3 rounded-lg fade-in ${source === 'ai' ? 'bg-accent-primary/10' : 'bg-industrial-700'}`;
        div.innerHTML = `
            <p class="text-xs text-gray-500 mb-1">${source === 'ai' ? '🤖 AI' : '📡 System'}</p>
            <p>${this.escapeHtml(text)}</p>
        `;

        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    }

    addSafetyEvent(args) {
        const container = this.elements.eventsContainer;

        // Remove placeholder if present
        const placeholder = container.querySelector('.italic');
        if (placeholder) placeholder.remove();

        const severity = args.severity || 1;
        const type = args.event_type || 'unknown';
        const description = args.description || '';

        const div = document.createElement('div');
        div.className = `p-3 rounded-lg fade-in bg-industrial-700 border-l-4 ${this.getSeverityBorderClass(severity)}`;
        div.innerHTML = `
            <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-medium uppercase tracking-wider ${this.getSeverityTextClass(severity)}">${type.replace(/_/g, ' ')}</span>
                <span class="px-2 py-0.5 rounded text-xs severity-${severity}">${this.getSeverityLabel(severity)}</span>
            </div>
            <p class="text-sm text-gray-300">${this.escapeHtml(description)}</p>
        `;

        container.insertBefore(div, container.firstChild);

        // Update stats
        this.state.eventCount++;
        this.elements.eventCount.textContent = this.state.eventCount;

        if (severity >= 5) {
            this.state.criticalCount++;
            this.elements.criticalCount.textContent = this.state.criticalCount;
        } else if (severity >= 3) {
            this.state.warningCount++;
            this.elements.warningCount.textContent = this.state.warningCount;
        }
    }

    getSeverityBorderClass(severity) {
        const classes = {
            1: 'border-blue-500',
            2: 'border-green-500',
            3: 'border-yellow-500',
            4: 'border-orange-500',
            5: 'border-red-500',
        };
        return classes[severity] || classes[1];
    }

    getSeverityTextClass(severity) {
        const classes = {
            1: 'text-blue-400',
            2: 'text-green-400',
            3: 'text-yellow-400',
            4: 'text-orange-400',
            5: 'text-red-400',
        };
        return classes[severity] || classes[1];
    }

    getSeverityLabel(severity) {
        const labels = { 1: 'INFO', 2: 'LOW', 3: 'MEDIUM', 4: 'HIGH', 5: 'CRITICAL' };
        return labels[severity] || 'INFO';
    }

    updateConnectionStatus(connected) {
        this.state.connected = connected;

        this.elements.statusIndicator.className = `w-2 h-2 rounded-full ${connected ? 'bg-green-500 status-glow text-green-500' : 'bg-gray-500'}`;
        this.elements.statusText.textContent = connected ? 'Connected' : 'Disconnected';
        this.elements.statusText.className = `text-sm ${connected ? 'text-green-400' : 'text-gray-400'}`;
    }

    updateStatus(text) {
        this.elements.statusText.textContent = text;
    }

    showError(message) {
        console.error('Error:', message);
        this.addResponse(`Error: ${message}`, 'system');
    }

    resetStats() {
        this.state.eventCount = 0;
        this.state.criticalCount = 0;
        this.state.warningCount = 0;

        this.elements.eventCount.textContent = '0';
        this.elements.criticalCount.textContent = '0';
        this.elements.warningCount.textContent = '0';
    }

    // ==================== Controls ====================

    toggleMute() {
        this.state.muted = !this.state.muted;

        this.elements.micOnIcon.classList.toggle('hidden', this.state.muted);
        this.elements.micOffIcon.classList.toggle('hidden', !this.state.muted);

        // Mute audio track
        if (this.mediaStream) {
            const audioTrack = this.mediaStream.getAudioTracks()[0];
            if (audioTrack) {
                audioTrack.enabled = !this.state.muted;
            }
        }
    }

    toggleVideo() {
        this.state.videoEnabled = !this.state.videoEnabled;

        this.elements.videoOnIcon.classList.toggle('hidden', !this.state.videoEnabled);
        this.elements.videoOffIcon.classList.toggle('hidden', this.state.videoEnabled);

        // Disable video track
        if (this.mediaStream) {
            const videoTrack = this.mediaStream.getVideoTracks()[0];
            if (videoTrack) {
                videoTrack.enabled = this.state.videoEnabled;
            }
        }
    }

    sendText() {
        const text = this.elements.textInput.value.trim();
        if (!text || !this.state.sessionActive) return;

        // Disable input until turn is complete
        this.elements.textInput.disabled = true;
        this.elements.sendTextBtn.disabled = true;
        this.elements.textInput.placeholder = "AI is thinking...";

        this.sendMessage('text_message', { text });
        this.addResponse(text, 'user');
        this.elements.textInput.value = '';
    }

    // ==================== Timer ====================

    startDurationTimer() {
        setInterval(() => {
            if (this.state.sessionActive && this.state.sessionStartTime) {
                const elapsed = Math.floor((Date.now() - this.state.sessionStartTime) / 1000);
                const minutes = Math.floor(elapsed / 60).toString().padStart(2, '0');
                const seconds = (elapsed % 60).toString().padStart(2, '0');
                this.elements.sessionDuration.textContent = `${minutes}:${seconds}`;
            }
        }, 1000);
    }

    // ==================== Utilities ====================

    // ==================== Context Management ====================

    resetContext() {
        if (!this.state.sessionActive) return;

        this.isResetting = true;
        this.reconnectAttempts = 0; // Reset attempts for fresh start

        this.updateStatus('Resetting context...');
        this.addResponse('--- Starting New Topic ---', 'system');

        // Disable controls temporarily
        this.elements.textInput.disabled = true;
        this.elements.sendTextBtn.disabled = true;
        if (this.elements.newTopicBtn) this.elements.newTopicBtn.disabled = true;

        // Force close to trigger auto-reconnect logic
        if (this.ws) {
            this.ws.close(4000, "Reset Context");
        }

        // Safety timeout to re-enable UI and force reload if reset fails
        setTimeout(() => {
            if (this.elements.textInput.disabled && !this.state.sessionActive) {
                this.updateStatus('Reset timed out. Reloading...');
                console.warn("Session reset timed out. Forcing reload.");
                window.location.reload();
            }
        }, 5000);
    }

    // ==================== Reports UI ====================

    toggleReports(show) {
        if (!this.elements.reportsModal) return;

        if (show) {
            this.elements.reportsModal.classList.remove('hidden');
            this.fetchReports();
        } else {
            this.elements.reportsModal.classList.add('hidden');
        }
    }

    async fetchReports() {
        const container = this.elements.reportsList;
        if (!container) return;

        container.innerHTML = '<p class="text-center text-gray-500 py-8">Loading reports...</p>';

        try {
            const response = await fetch('/api/audit/logs');
            const data = await response.json();

            if (!data.sessions || data.sessions.length === 0) {
                container.innerHTML = '<p class="text-center text-gray-500 py-8 italic">No audit logs found.</p>';
                return;
            }

            container.innerHTML = '';

            data.sessions.forEach(session => {
                const date = new Date(session.start_time).toLocaleString();
                let duration = 'Unknown';

                if (session.end_time) {
                    const diff = new Date(session.end_time) - new Date(session.start_time);
                    if (!isNaN(diff)) {
                        const mins = Math.floor(diff / 60000);
                        const secs = Math.round((diff % 60000) / 1000);
                        duration = `${mins}m ${secs}s`;
                    }
                }

                const div = document.createElement('div');
                div.className = 'bg-industrial-800 p-4 rounded-xl border border-white/5 hover:border-white/20 transition-colors mb-4';
                div.innerHTML = `
                    <div class="flex justify-between items-start mb-3">
                        <div>
                            <span class="text-xs text-gray-500 font-bold uppercase">Session ID</span>
                            <p class="font-mono text-xs text-accent-primary truncate w-64" title="${session.session_id}">${session.session_id}</p>
                        </div>
                        <div class="text-right">
                             <span class="text-xs text-gray-500 font-bold uppercase">Date</span>
                             <p class="text-xs text-gray-300 font-medium">${date}</p>
                        </div>
                    </div>
                    <div class="grid grid-cols-4 gap-3 mt-3">
                         <div class="bg-white/5 p-2 rounded-lg text-center">
                             <span class="block text-xs text-gray-500 uppercase">Duration</span>
                             <span class="text-sm font-bold text-gray-300 mono">${duration}</span>
                         </div>
                         <div class="bg-white/5 p-2 rounded-lg text-center">
                             <span class="block text-xs text-gray-500 uppercase">Events</span>
                             <span class="text-lg font-bold text-white mono">${session.event_count}</span>
                         </div>
                         <div class="bg-white/5 p-2 rounded-lg text-center">
                             <span class="block text-xs text-gray-500 uppercase">Critical</span>
                             <span class="text-lg font-bold text-safety-red mono">${session.critical_events}</span>
                         </div>
                         <div class="bg-white/5 p-2 rounded-lg flex items-center justify-center hover:bg-white/10 transition-colors cursor-pointer" onclick="window.open('/api/reports/${session.session_id}', '_blank')">
                             <div class="text-center">
                                 <svg class="w-5 h-5 text-accent-primary mx-auto mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                     <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                                 </svg>
                                 <span class="text-xs text-accent-primary font-medium">Report</span>
                             </div>
                         </div>
                    </div>
                `;
                container.appendChild(div);
            });

        } catch (error) {
            console.error('Failed to fetch reports:', error);
            container.innerHTML = `<p class="text-center text-safety-red py-8">Failed to load reports: ${error.message}</p>`;
        }
    }



    arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    base64ToArrayBuffer(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    window.app = new FieldVisionApp();
});
