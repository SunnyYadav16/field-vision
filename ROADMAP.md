# FieldVision Future Roadmap

## 🚀 Phase 2: Enhanced Interactivity (High Impact)

### 1. Visual Hazard Overlay (AR-Lite)
**Concept:** Instead of just *saying* "Missing hard hat", the AI draws a bounding box around the person's head on the video feed.
**Implementation:**
- Add a "Scan Scene" button.
- Capture a high-res frame.
- Send to Gemini Pro Vision with a prompt asking for JSON bounding box coordinates `[ymin, xmin, ymax, xmax]`.
- Draw these boxes on a `<canvas>` overlay on the video feed.
**Why it's better:** Visual confirmation is faster than audio. Makes the "AI Vision" tangible.

### 2. Voice-Activated Work Orders
**Concept:** Hands-free maintenance logging.
**User says:** "FieldVision, create a work order for pump 3 - leaking oil."
**AI Action:**
- Parses intent.
- Calls a new tool `create_ticket(asset_id="pump-3", issue="leaking oil", priority="high")`.
- Integrates with mock Jira/SAP/Maximo backend.
**Why it's better:** Turns observation into *action*. Saves conflicting paperwork later.

---

## 🌐 Phase 3: The "Supervisor" Agent (Multi-Modal Orchestration)

### 3. Site-Wide Safety Dashboard
**Concept:** Aggregate data from multiple FieldVision users.
**Implementation:**
- A central dashboard that shows a map of the facility.
- Real-time "Safety Score" based on incoming events from all connected workers.
- **Supervisor Agent:** A new AI instance that reads the aggregated logs and suggests site-wide interventions (e.g., "3 workers reported slippery floors in Zone B - recommending immediate cleanup crew").
**Why it's better:** Moves from *individual* safety to *systemic* safety.

### 4. IoT Sensor Fusion
**Concept:** AI that sees *invisible* hazards.
**Implementation:**
- Connect to MQTT streams from gas detectors (H2S, CO) or temperature sensors.
- If a sensor spikes, the AI warns the worker *before* they enter the area.
- **Context:** "Warning: H2S levels high in Sector 4. Do not enter without breathing apparatus."
**Why it's better:** Combines physical sensor data with visual understanding.

---

## 🧠 Phase 4: Active Learning & Training

### 5. Post-Shift Safety Coaching
**Concept:** Personalized training based on actual behavior.
**AI Action:**
- "Hey Ankit, purely for coaching: I noticed you forgot your gloves twice today. Here's a 30-second clip showing why they prevent burns."
- Generates a custom quiz based on the day's manual lookup.
**Why it's better:** Turns mistakes into learning opportunities without being punitive.
