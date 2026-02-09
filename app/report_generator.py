"""
FieldVision PDF Report Generator
Generates compliance reports for work orders using ReportLab
"""

import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from app.work_orders import get_pending_orders, get_approved_orders, get_completed_orders


def filter_orders_by_date(orders: list, start_date: datetime, end_date: datetime) -> list:
    """Filter orders by created_at date within the given range."""
    filtered = []
    for order in orders:
        created_str = order.get("created_at", "")
        if created_str:
            try:
                # Parse ISO format date
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                # Make comparison timezone-naive
                created_naive = created.replace(tzinfo=None)
                if start_date <= created_naive <= end_date:
                    filtered.append(order)
            except (ValueError, TypeError):
                continue
    return filtered


def format_date_display(dt: datetime) -> str:
    """Format datetime for display."""
    return dt.strftime("%B %d, %Y %I:%M %p")


def generate_work_orders_report(start_date: datetime, end_date: datetime) -> bytes:
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
        f"Generated: {format_date_display(datetime.now())}",
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
