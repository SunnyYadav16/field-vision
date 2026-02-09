"""
FieldVision Report Generator Module
Generates consolidated PDF reports from session transcripts and work order compliance data.
"""

import json
import logging
import datetime
import io
from pathlib import Path
from typing import List, Dict, Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from app.work_orders import get_pending_orders, get_approved_orders, get_completed_orders

logger = logging.getLogger(__name__)

class ReportGenerator:
    def __init__(self, log_dir: str = "logs", output_dir: str = "reports"):
        self.log_dir = Path(log_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        
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
        filename = f"FieldVision_Session_Report_{hours}h_{timestamp}.pdf"
        filepath = self.output_dir / filename
        
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
                
                # Check for evidence
                evidence_text = ""
                if meta.get("evidence_path"):
                    evidence_text = "<br/><i>[Visual Evidence Attached]</i>"
                
                p_text = f"<b>[{ts}] {etype} (Severity: {severity})</b><br/>{desc}{evidence_text}"
                story.append(Paragraph(p_text, styles["Normal"]))
                story.append(Spacer(1, 0.1*inch))
        
        # Transcript Log
        story.append(Paragraph("Recent Interactions", styles["Heading2"]))
        last_turns = transcript_turns[-20:] if transcript_turns else []
        if not last_turns:
            story.append(Paragraph("No conversation activity.", styles["Normal"]))
        else:
            for turn in last_turns:
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


# --- Work Order Report Generation (Merged from Remote) ---

def filter_orders_by_date(orders: list, start_date: datetime.datetime, end_date: datetime.datetime) -> list:
    """Filter orders by created_at date within the given range."""
    filtered = []
    for order in orders:
        created_str = order.get("created_at", "")
        if created_str:
            try:
                # Parse ISO format date
                created = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                # Make comparison timezone-naive
                created_naive = created.replace(tzinfo=None)
                if start_date <= created_naive <= end_date:
                    filtered.append(order)
            except (ValueError, TypeError):
                continue
    return filtered


def format_date_display(dt: datetime.datetime) -> str:
    """Format datetime for display."""
    return dt.strftime("%B %d, %Y %I:%M %p")


def generate_work_orders_report(start_date: datetime.datetime, end_date: datetime.datetime) -> bytes:
    """Generate a PDF report of work orders within the date range."""
    
    # Get and filter orders
    pending = filter_orders_by_date(get_pending_orders(), start_date, end_date)
    approved = filter_orders_by_date(get_approved_orders(), start_date, end_date)
    completed = filter_orders_by_date(get_completed_orders(), start_date, end_date)
    
    # Create PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch
    )
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#F59E0B'),
        alignment=TA_CENTER,
        spaceAfter=12
    )
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.gray,
        alignment=TA_CENTER,
        spaceAfter=24
    )
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#1F2937'),
        spaceAfter=8,
        spaceBefore=16
    )
    
    # Build document elements
    elements = []
    
    # Header
    elements.append(Paragraph("⚙️ FieldVision Compliance Report", title_style))
    elements.append(Paragraph(
        f"Work Orders: {format_date_display(start_date)} - {format_date_display(end_date)}",
        subtitle_style
    ))
    elements.append(Paragraph(
        f"Generated: {format_date_display(datetime.datetime.now())}",
        subtitle_style
    ))
    
    # Summary Stats
    summary_data = [
        ['Status', 'Count'],
        ['Pending Approval', str(len(pending))],
        ['Approved / In Progress', str(len(approved))],
        ['Completed', str(len(completed))],
        ['Total', str(len(pending) + len(approved) + len(completed))]
    ]
    summary_table = Table(summary_data, colWidths=[3*inch, 1.5*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F2937')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#374151')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.gray),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 20))
    
    # Helper function for order tables
    def create_order_table(orders: list, section_name: str, status_color: colors.Color):
        elements.append(Paragraph(f"{section_name} ({len(orders)})", section_style))
        
        if not orders:
            elements.append(Paragraph("No orders in this category.", styles['Normal']))
            return
        
        table_data = [['Order ID', 'Equipment', 'Priority', 'Requested By', 'Created']]
        for order in orders:
            table_data.append([
                order.get('order_id', 'N/A'),
                order.get('equipment', 'N/A')[:25],
                order.get('priority', 'N/A').upper(),
                order.get('requested_by', {}).get('name', 'Unknown'),
                order.get('created_at', '')[:10]
            ])
        
        col_widths = [1.6*inch, 2*inch, 0.8*inch, 1.3*inch, 1*inch]
        table = Table(table_data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), status_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (2, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9FAFB')]),
        ]))
        elements.append(table)
    
    # Add sections
    create_order_table(pending, "🟡 Pending Approval", colors.HexColor('#D97706'))
    create_order_table(approved, "🔵 Approved / In Progress", colors.HexColor('#2563EB'))
    create_order_table(completed, "🟢 Completed", colors.HexColor('#059669'))
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF bytes
    buffer.seek(0)
    return buffer.read()
