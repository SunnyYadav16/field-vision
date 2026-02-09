# FieldVision Phase 2 - Advanced Safety Monitor

## 1. Transcript & Contextual Reporting
**Goal**: Provide a complete record of the session, ensuring reports include the full "to and fro" conversation.
**Technical Approach**:
- **Interaction Logging**: Implement `log_interaction` tool to capture Q&A pairs (User query + AI response).
- **Report Enhancement**: Update `reporting.py` to render a "Conversation Log" section alongside safety events.
- **Status**: *Partially Implemented (Tool defined, Report section added).*

## 2. Contextual Monitoring (Visual Evidence)
**Goal**: Prove compliance violations (e.g., "Hard Hat Zone" entry) with visual proof in the report.
**Technical Approach**:
- **Frame Buffering**: `GeminiLiveService` handles the video stream. We will cache the most recent valid JPEG frame.
- **Evidence Capture**: When `log_safety_event` is called by the AI, the service will save the cached frame to disk (`static/evidence/{session_id}_{timestamp}.jpg`).
- **Report Integration**: The HTML report will include `<img src="...">` tags next to critical safety events.

## 3. Workflow Automation (RBAC & Badge Access)
**Goal**: Ensure only authorized personnel can execute high-priority actions like creating work orders ("pump 3 - oil leak").
**Strategy (Prototype)**:
- **Visual Verification**: Use Gemini's multimodal vision to "scan" for a badge.
- **Protocol**:
    1. User says: "Create high priority ticket..."
    2. AI System Instruction: "Check video feed for a visible ID badge. If not found, deny request."
    3. If found, call `create_ticket` tool.
- **Role-Based Action**: The `create_ticket` tool will have a mock check for "Manager" vs "Technician" roles based on the badge data (simulated).

## 4. Consolidated Reporting
**Goal**: A unified view of site activity over time (6/12/24 hours).
**Technical Approach**:
- **New Endpoint**: `GET /api/reports/site-summary?hours=24`.
- **Aggregation**: `AuditLogger` will filter historical logs by time window.
- **Output**: A single PDF-ready HTML table summarizing all sessions, total hazards, and critical interventions.

## 5. Natural Audio & "Human" Interaction
**Goal**: Audio should be strictly professional and human-like, removing "AI" preambles.
**Technical Approach**:
- **System Instruction Tuning**: Explicit negative constraints: "Do not say 'As an AI'. Do not say 'I understand'. Speak efficiently like a senior field colleague."
- **Multi-Turn Stability**: Ensure the session loop is robust against silence or rapid-fire questions (Already improved in Phase 1).
