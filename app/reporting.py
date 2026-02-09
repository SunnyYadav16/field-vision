import json
from pathlib import Path
from datetime import datetime
import structlog
from google import genai  # Use new SDK
from app.audit import AuditLogger, SafetyEvent
from app.config import get_settings

logger = structlog.get_logger(__name__)

class AuditReporter:
    def __init__(self, audit_logger: AuditLogger):
        self.audit_logger = audit_logger
        self.settings = get_settings()
        try:
            # Initialize new SDK client
            self.client = genai.Client(api_key=self.settings.gemini_api_key)
        except Exception as e:
            logger.error("genai_client_init_error", error=str(e))
            self.client = None

    async def _generate_ai_summary(self, events: list[SafetyEvent]) -> str:
        """Generate a natural language summary of the session using Gemini"""
        if not self.client or not events:
            return "Summary unavailable."
            
        # Format events for the prompt
        event_log = "\n".join([
            f"[{e.timestamp}] {e.event_type} (Severity {e.severity}): {e.description}"
            for e in events
        ])
        
        prompt = f"""
        Analyze the following safety event log from an industrial worksite session.
        Write a concise executive summary (3-4 sentences) highlighting key risks, 
        compliance issues (PPE), and overall safety status. Use technical, professional tone.
        
        Log:
        {event_log}
        """
        
        try:
            # Use async generate_content from new SDK
            response = await self.client.aio.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error("ai_summary_failed", error=str(e))
            return "AI summary generation failed."

    async def generate_session_report(self, session_id: str) -> str:
        """
        Generate a comprehensive HTML report for a session.
        Includes summary stats, event timeline, and consolidated recommendations.
        """
        try:
            events = self.audit_logger.get_session_events(session_id)
            
            # Load Transcript
            transcript = []
            transcript_file = Path("logs/session_transcript.json")
            if transcript_file.exists():
                try:
                    with open(transcript_file, "r", encoding="utf-8") as f:
                        all_logs = json.load(f)
                        transcript = [log for log in all_logs if log.get("session_id") == session_id]
                except Exception as e:
                    logger.error("transcript_load_error", error=str(e))

            if not events:
                return "<html><body><h1>Session not found or empty</h1></body></html>"

            try:
                summary = await self.audit_logger.get_session_summary(session_id)
            except Exception as e:
                logger.error("session_summary_failed", error=str(e))
                summary = {
                    "total_events": len(events),
                    "critical_events": "N/A",
                    "high_severity_events": "N/A"
                }
            
            # Generate AI Summary
            try:
                ai_summary_text = await self._generate_ai_summary(events)
            except Exception:
                ai_summary_text = "Summary unavailable."

            # Basic HTML template
            html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Safety Report - {session_id}</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <style>
                    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
                    body {{ font-family: 'Inter', sans-serif; }}
                    .severity-1 {{ background-color: #3b82f6; color: white; }}
                    .severity-2 {{ background-color: #22c55e; color: white; }}
                    .severity-3 {{ background-color: #eab308; color: black; }}
                    .severity-4 {{ background-color: #f97316; color: white; }}
                    .severity-5 {{ background-color: #ef4444; color: white; }}
                </style>
            </head>
            <body class="bg-gray-50 text-gray-900 min-h-screen p-8 print:p-0">
                <div class="max-w-4xl mx-auto bg-white shadow-lg rounded-xl overflow-hidden print:shadow-none">
                    <!-- Header -->
                    <div class="bg-slate-900 text-white p-8">
                        <div class="flex justify-between items-start">
                            <div>
                                <h1 class="text-3xl font-bold mb-2">FieldVision Safety Report</h1>
                                <p class="text-slate-400">Session ID: <span class="font-mono">{session_id}</span></p>
                            </div>
                            <div class="text-right">
                                 <p class="text-lg font-semibold">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                                 <p class="text-sm text-slate-400">Generated Report</p>
                            </div>
                        </div>
                    </div>

                    <!-- Executive Summary -->
                    <div class="bg-blue-50 p-8 border-b border-blue-100">
                        <h2 class="text-xl font-bold mb-3 text-blue-900">Executive Summary</h2>
                        <p class="text-blue-800 leading-relaxed">{ai_summary_text}</p>
                    </div>
                    
                    <!-- Stats -->
                    <div class="grid grid-cols-4 border-b border-gray-200">
                        <div class="p-6 text-center border-r border-gray-200">
                            <span class="block text-sm text-gray-500 uppercase tracking-wide">Total Events</span>
                            <span class="block text-3xl font-bold mt-1">{summary['total_events']}</span>
                        </div>
                        <div class="p-6 text-center border-r border-gray-200">
                            <span class="block text-sm text-gray-500 uppercase tracking-wide">Critical</span>
                            <span class="block text-3xl font-bold mt-1 text-red-600">{summary['critical_events']}</span>
                        </div>
                        <div class="p-6 text-center border-r border-gray-200">
                            <span class="block text-sm text-gray-500 uppercase tracking-wide">High Severity</span>
                            <span class="block text-3xl font-bold mt-1 text-orange-600">{summary['high_severity_events']}</span>
                        </div>
                        <div class="p-6 text-center">
                            <span class="block text-sm text-gray-500 uppercase tracking-wide">Duration</span>
                            <span class="block text-3xl font-bold mt-1 text-gray-700">{self._calculate_duration(events)}</span>
                        </div>
                    </div>
                    
                    <!-- Event Timeline -->
                    <div class="p-8">
                        <h2 class="text-xl font-bold mb-6 flex items-center gap-2">
                            <svg class="w-6 h-6 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                 <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            Event Timeline
                        </h2>
                        
                        <div class="space-y-4">
            """
            
            severity_labels = {1: "INFO", 2: "SUCCESS", 3: "WARNING", 4: "HIGH", 5: "CRITICAL"}

            for event in events:
                try:
                    if not event.timestamp:
                        continue
                        
                    # Safe timestamp parsing
                    try:
                        ts = datetime.fromisoformat(event.timestamp)
                    except ValueError:
                        ts = datetime.now()
                        
                    time_str = ts.strftime('%H:%M:%S')
                    
                    # Safe severity handling
                    sev = event.severity if 1 <= event.severity <= 5 else 1
                    severity_class = f"severity-{sev}"
                    severity_label = severity_labels.get(sev, "UNKNOWN")
                    
                    safe_desc = event.description or "No description provided."
                    
                    evidence_html = ""
                    if event.metadata and event.metadata.get("evidence_url"):
                        evidence_url = event.metadata.get("evidence_url")
                        evidence_html = f"""
                        <div class="mt-3">
                            <img src="{evidence_url}" class="rounded-lg border border-gray-300 max-w-full h-auto shadow-sm" alt="Safety Evidence">
                            <p class="text-xs text-gray-400 mt-1 italic">Visual Evidence Captured</p>
                        </div>
                        """
                
                    html += f"""
                                <div class="flex gap-4 p-4 rounded-lg bg-gray-50 border border-gray-200">
                                    <div class="w-20 pt-1 text-sm font-mono text-gray-500">{time_str}</div>
                                    <div class="flex-1">
                                        <div class="flex items-center gap-2 mb-1">
                                            <span class="px-2 py-0.5 rounded text-xs font-bold {severity_class}">{severity_label}</span>
                                            <span class="font-semibold text-gray-800 uppercase tracking-wide text-xs">{event.event_type.replace('_', ' ')}</span>
                                        </div>
                                        <p class="text-gray-700">{safe_desc}</p>
                                        {evidence_html}
                                    </div>
                                </div>
                    """
                except Exception as e:
                    logger.error("report_render_event_error", error=str(e), event_id=str(event.timestamp))
                    continue
                
            html += """
                        </div>
                    </div>
                    
                    <!-- Conversation Transcript -->
                    <div class="p-8 bg-slate-50 border-t border-gray-200">
                        <h2 class="text-xl font-bold mb-6 flex items-center gap-2">
                            <svg class="w-6 h-6 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"></path>
                            </svg>
                            Conversation Transcript
                        </h2>
                        
                        <div class="space-y-4">
            """
            
            for log in transcript:
                speaker = log.get("speaker", "UNKNOWN")
                content = log.get("content", "")
                ts = log.get("timestamp", "")
                try:
                    time_only = datetime.fromisoformat(ts).strftime('%H:%M:%S')
                except:
                    time_only = "--:--:--"
                
                bg_color = "bg-blue-100 border-blue-200" if speaker == "USER" else "bg-white border-gray-200"
                align = "ml-auto text-right" if speaker == "USER" else "mr-auto"
                
                html += f"""
                    <div class="flex flex-col {align} max-w-[80%]">
                        <div class="flex items-center gap-2 mb-1 {align}">
                            <span class="text-[10px] font-bold text-gray-400 uppercase tracking-wider">{speaker} @ {time_only}</span>
                        </div>
                        <div class="p-3 rounded-lg border {bg_color} text-sm shadow-sm">{content}</div>
                    </div>
                """

            html += """
                        </div>
                    </div>
                    
                    <!-- Footer -->
                    <div class="bg-gray-50 p-8 border-t border-gray-200 text-center text-sm text-gray-500">
                        <p>FieldVision AI Safety Monitor • Automated Report</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            return html
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("report_generation_critical_failure", error=str(e), traceback=tb)
            return f"""
            <html>
                <body style="font-family: monospace; padding: 20px; background: #fff0f0; color: #cc0000;">
                    <h1>Report Generation Failed</h1>
                    <p>Internal Error Details:</p>
                    <pre style="background: #fff; padding: 15px; border: 1px solid #cc0000; overflow: auto;">{tb}</pre>
                </body>
            </html>
            """

    def _calculate_duration(self, events: list[SafetyEvent]) -> str:
        if not events:
            return "0s"
        try:
            start = datetime.fromisoformat(events[0].timestamp)
            end = datetime.fromisoformat(events[-1].timestamp)
            diff = end - start
            seconds = int(diff.total_seconds())
            if seconds < 0: seconds = 0
            
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        except Exception:
            return "Unknown"
