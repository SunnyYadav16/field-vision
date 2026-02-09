"""
Report Generator Module
Generates consolidated PDF reports from session transcripts.
"""
import json
import logging
import datetime
from pathlib import Path
from typing import List, Dict, Any

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

logger = logging.getLogger(__name__)

class ReportGenerator:
    def __init__(self, log_dir: str = "logs", output_dir: str = "reports"):
        self.log_dir = Path(log_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
    def generate_consolidated_report(self, hours: int = 24) -> str:
        """
        Generate a PDF report summarizing activity over the last N hours.
        Returns the path to the generated PDF.
        """
        # 1. Load Data
        transcript_file = self.log_dir / "session_transcript.json"
        
        data = []
        if transcript_file.exists():
            try:
                with open(transcript_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception as e:
                logger.error(f"Failed to load transcript: {e}")
                # Continue with empty data instead of failing
        
        # 2. Filter by Time
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        relevant_events = []
        
        session_ids = set()
        safety_events = []
        transcript_turns = []
        
        for entry in data:
            ts_str = entry.get("timestamp")
            if not ts_str:
                continue
            try:
                # Handle ISO format with or without Z
                ts_str = ts_str.replace('Z', '+00:00')
                ts = datetime.datetime.fromisoformat(ts_str)
                # Ensure naive vs aware comparison works (convert to utc naive if needed or aware)
                if ts.tzinfo:
                    ts = ts.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                
                if ts >= cutoff:
                    relevant_events.append(entry)
                    session_ids.add(entry.get("session_id"))
                    
                    if entry.get("type") == "tool_call" and entry.get("content") == "log_safety_event":
                        safety_events.append(entry)
                    elif entry.get("type") in ["answer"]: # Only include answers which contain context
                        transcript_turns.append(entry)
            except Exception as e:
                continue
                
        # 3. Generate PDF
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"FieldVision_Report_{hours}h_{timestamp}.pdf"
        filepath = self.output_dir / filename
        
        # Ensure directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        doc = SimpleDocTemplate(str(filepath), pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        # Title
        title_style = styles["Title"]
        story.append(Paragraph(f"FieldVision Safety Report ({hours} Hours)", title_style))
        story.append(Paragraph(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
        story.append(Spacer(1, 0.25*inch))
        
        # Summary Stats
        stats_data = [
            ["Metric", "Value"],
            ["Total Sessions", str(len(session_ids))],
            ["Safety Events Logged", str(len(safety_events))],
            ["AI Interactions", str(len(transcript_turns))]
        ]
        
        t = Table(stats_data, colWidths=[3*inch, 2*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(t)
        story.append(Spacer(1, 0.25*inch))
        
        # Critical Events
        story.append(Paragraph("Safety Events Log", styles["Heading2"]))
        
        if not safety_events:
            story.append(Paragraph("No safety events recorded in this period.", styles["Normal"]))
        else:
            for event in safety_events:
                meta = event.get("metadata", {})
                severity = meta.get("severity", 1)
                etype = meta.get("event_type", "Unknown")
                desc = meta.get("description", "No description")
                ts = event.get("timestamp", "").split('T')[1][:8] if 'T' in event.get("timestamp", "") else ""
                
                # Check for evidence (Task 3 placeholder)
                evidence_text = ""
                if meta.get("evidence_path"):
                    evidence_text = "<br/><i>[Visual Evidence Attached]</i>"
                
                p_text = f"<b>[{ts}] {etype} (Severity: {severity})</b><br/>{desc}{evidence_text}"
                story.append(Paragraph(p_text, styles["Normal"]))
                story.append(Spacer(1, 0.1*inch))
        
        # Transcript Log
        story.append(Paragraph("Recent Interactions", styles["Heading2"]))
        last_10_turns = transcript_turns[-20:] if transcript_turns else []
        if not last_10_turns:
            story.append(Paragraph("No conversation activity.", styles["Normal"]))
        else:
            for turn in last_10_turns:
                speaker = turn.get("speaker")
                content = turn.get("content")
                ts = turn.get("timestamp", "").split('T')[1][:8] if 'T' in turn.get("timestamp", "") else ""
                
                color = "blue" if speaker == "USER" else "green"
                p_text = f"<font color='{color}'><b>[{ts}] {speaker}:</b></font> {content}"
                story.append(Paragraph(p_text, styles["Normal"]))
                story.append(Spacer(1, 0.05*inch))

        doc.build(story)
        return str(filepath)

report_generator = ReportGenerator()
