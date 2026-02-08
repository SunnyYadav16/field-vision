# FieldVision 🏭👁️

**AI-Powered Industrial Safety Assistant**

FieldVision is a real-time AI copilot for industrial maintenance technicians. It uses Google's Gemini Live API to provide hands-free voice interaction, continuous visual safety monitoring, and automated compliance logging.

![FieldVision Demo](docs/demo.gif)

## ✨ Features

- **🎥 Real-Time Video Analysis** - Continuous monitoring for safety hazards, PPE compliance, and procedure verification
- **🎤 Hands-Free Voice Interface** - Full two-way audio conversation using Gemini Live API
- **� Automated Reporting** - Generates PDF-ready HTML reports with AI executive summaries
- **🔄 Session Resumption** - "New Topic" feature allows seamless context switching
- **📚 Technical Manual Integration** - Grounded Q&A using cached maintenance documentation

## 🏗️ Architecture

```
┌─────────────────┐     WebSocket      ┌─────────────────┐     gRPC/HTTPS     ┌─────────────────┐
│                 │ ◄─────────────────► │                 │ ◄─────────────────► │                 │
│     Browser     │   Audio + Video     │  FastAPI Server │   Bidirectional     │  Gemini Live    │
│   (Camera/Mic)  │                     │   (Python)      │     Streaming       │      API        │
│                 │ ◄─────────────────► │                 │ ◄─────────────────► │                 │
└─────────────────┘   AI Responses      └─────────────────┘   Audio + Tooling   └─────────────────┘
                                                │
                                                ▼
                                        ┌─────────────────┐
                                        │  audit_log.json │
                                        │  (Compliance)   │
                                        └─────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Google Cloud account with Gemini API access
- Webcam and microphone

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/field-vision.git
cd field-vision

# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\activate

# Activate (Unix/macOS)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. **Get your Gemini API Key** from [Google AI Studio](https://aistudio.google.com/apikey)

2. **Create `.env` file**:
```bash
cp .env.example .env
```

3. **Edit `.env`** with your API key:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```

### Running the Application

```bash
# Start the server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser to: **http://localhost:8000**

## 📁 Project Structure

```
field-vision/
├── app/
│   ├── __init__.py          # Package exports
│   ├── config.py             # Pydantic settings
│   ├── audit.py              # Safety event logging
│   ├── gemini_service.py     # Gemini Live API client
│   └── websocket_handler.py  # WebSocket connection manager
├── static/
│   ├── index.html            # Main UI
│   └── app.js                # Frontend application
├── logs/
│   └── audit_log.json        # Safety event audit trail
├── main.py                   # FastAPI application
├── requirements.txt          # Python dependencies
├── .env.example              # Environment template
└── README.md
```

## 🔧 API Reference

### WebSocket Messages

#### Client → Server

| Type | Payload | Description |
|------|---------|-------------|
| `start_session` | `{ manual_context?: string }` | Start a new AI session |
| `end_session` | `{}` | End current session |
| `audio_data` | `{ data: base64 }` | PCM16 audio at 16kHz |
| `video_frame` | `{ data: base64 }` | JPEG image frame |
| `text_message` | `{ text: string }` | Text input |

#### Server → Client

| Type | Payload | Description |
|------|---------|-------------|
| `session_started` | `{ session_id: string }` | Session confirmation |
| `audio_response` | `{ data: base64 }` | PCM16 audio at 24kHz |
| `text_response` | `{ text: string }` | Text response |
| `tool_call` | `{ function: string, arguments: object }` | Safety event logged |
| `error` | `{ error: string }` | Error message |

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Main application UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/session/{id}/summary` | Session audit summary |
| `GET` | `/api/session/{id}/events` | Session event list |
| `GET` | `/api/audit/logs` | List all historical sessions |
| `GET` | `/api/reports/{session_id}` | Generate HTML Audit Report |

## 🛡️ Safety Event Types

| Event Type | Description | Severity Range |
|------------|-------------|----------------|
| `missing_ppe` | PPE not detected (gloves, glasses, etc.) | 3-5 |
| `hazard_detected` | General safety hazard identified | 2-5 |
| `unsafe_position` | Body in dangerous position | 4-5 |
| `procedure_violation` | Incorrect procedure step | 3-4 |
| `equipment_issue` | Equipment problem detected | 2-5 |
| `environment_hazard` | Spill, obstruction, etc. | 2-5 |
| `step_verified` | Procedure step confirmed correct | 1 |
| `safety_check_passed` | Safety inspection passed | 1 |

## ⚙️ Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google Gemini API key |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `SESSION_TTL_SECONDS` | `3600` | Session timeout |
| `FRAME_RATE` | `1` | Video capture FPS |
| `JPEG_QUALITY` | `85` | Image compression quality |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## 🔒 Safety Governance

FieldVision adheres to responsible AI principles:

1. **Transparency** - All observations are logged and auditable
2. **AI Disclosure** - Responses explicitly labeled as AI-generated
3. **Advisory Only** - No direct machine control; humans perform all actions
4. **Human-in-the-Loop** - Safety sign-offs require human approval
5. **Accountability** - Each session has a clearly defined owner

## 📈 Future Roadmap

- [ ] AR glasses integration for hands-free HUD
- [ ] PDF compliance report generation
- [ ] Multi-step LOTO sequence verification
- [ ] IoT sensor integration
- [ ] Cloud Run deployment for fleet scaling
- [ ] Firestore for persistent audit storage

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**⚠️ Disclaimer**: FieldVision is an advisory system only. It does NOT control industrial equipment and should NOT be used as a primary safety mechanism. Always follow your organization's safety protocols and procedures.
