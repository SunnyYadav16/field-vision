/**
 * FieldVision Frontend Application
 * Handles camera/microphone capture, WebSocket communication, and UI updates
 */

class FieldVisionApp {
    constructor() {
        // Configuration - Optimized for lower bandwidth
        this.config = {
            wsUrl: `ws://${window.location.host}/ws`,
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

            // Output
            responseContainer: document.getElementById('responseContainer'),
            eventsContainer: document.getElementById('eventsContainer'),

            // Stats
            sessionDuration: document.getElementById('sessionDuration'),
            eventCount: document.getElementById('eventCount'),
            criticalCount: document.getElementById('criticalCount'),
            warningCount: document.getElementById('warningCount'),
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
    }

    // ==================== WebSocket ====================

    connectWebSocket() {
        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(this.config.wsUrl);

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

    async handleDisconnect() {
        // If session was active (unexpected disconnect), try to auto-reconnect
        if (this.state.sessionActive && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            this.updateStatus(`Connection lost. Reconnecting (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
            this.addResponse(`⚠️ Connection interrupted. Attempting to reconnect...`, 'system');

            try {
                // Wait before reconnecting
                await new Promise(resolve => setTimeout(resolve, 2000));

                // Reconnect WebSocket
                await this.connectWebSocket();

                // Restart the Gemini session
                this.sendMessage('start_session', {
                    system_instruction: null,
                    manual_context: null,
                });

                this.addResponse(`✅ Reconnected! You can continue your conversation.`, 'system');

            } catch (error) {
                console.error('Reconnect failed:', error);
                if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                    this.addResponse(`❌ Could not reconnect. Please refresh the page.`, 'system');
                    this.cleanupSession();
                }
            }
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

        // Create audio worklet for capturing audio
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);

        // Use ScriptProcessor for broader compatibility (worklet is preferred but more complex)
        const processor = this.audioContext.createScriptProcessor(4096, 1, 1);

        processor.onaudioprocess = (e) => {
            if (this.state.sessionActive && !this.state.muted) {
                const inputData = e.inputBuffer.getChannelData(0);
                const pcmData = this.float32ToPCM16(inputData);
                const base64 = this.arrayBufferToBase64(pcmData.buffer);
                this.sendMessage('audio_data', { data: base64 });
            }
        };

        source.connect(processor);
        processor.connect(this.audioContext.destination);

        this.audioWorklet = processor;
    }

    float32ToPCM16(float32Array) {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16Array;
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
        }
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
