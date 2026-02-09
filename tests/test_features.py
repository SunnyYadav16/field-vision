import pytest
import os
import json
import shutil
import asyncio
from datetime import datetime, timedelta
from app.conversation_logger import ConversationLogger
from app.report_generator import ReportGenerator
from main import app
from fastapi.testclient import TestClient

# Use separate test directories to avoid messing with real logs
TEST_LOG_DIR = "test_logs"
TEST_REPORT_DIR = "test_reports"

# Fixtures
@pytest.fixture
def setup_dirs():
    if os.path.exists(TEST_LOG_DIR):
        shutil.rmtree(TEST_LOG_DIR)
    if os.path.exists(TEST_REPORT_DIR):
        shutil.rmtree(TEST_REPORT_DIR)
    os.makedirs(TEST_LOG_DIR, exist_ok=True)
    os.makedirs(TEST_REPORT_DIR, exist_ok=True)
    yield
    # Cleanup logs, keep reports maybe?
    if os.path.exists(TEST_LOG_DIR):
        shutil.rmtree(TEST_LOG_DIR)
    if os.path.exists(TEST_REPORT_DIR):
        shutil.rmtree(TEST_REPORT_DIR)

@pytest.mark.asyncio
async def test_logger_functionality(setup_dirs):
    """Verify conversation logging works efficiently"""
    logger = ConversationLogger(log_dir=TEST_LOG_DIR)
    
    test_data = {
        "speaker": "AI",
        "type": "answer",
        "content": "This is a test response",
        "timestamp": datetime.utcnow().isoformat()
    }
    await logger.log_interaction("test-daily-session", test_data)
    
    log_file = os.path.join(TEST_LOG_DIR, "session_transcript.json")
    assert os.path.exists(log_file)
    
    with open(log_file, "r") as f:
        logs = json.load(f)
    assert len(logs) == 1
    assert logs[0]["content"] == "This is a test response"


def test_report_generation(setup_dirs):
    """Verify PDF report generation logic"""
    # 1. Seed logs
    log_file = os.path.join(TEST_LOG_DIR, "session_transcript.json")
    seed_data = [
        {
            "session_id": "seed-1", 
            "timestamp": datetime.utcnow().isoformat(),
            "speaker": "USER", 
            "type": "question", 
            "content": "Is the system safe?"
        },
        {
            "session_id": "seed-1", 
            "timestamp": datetime.utcnow().isoformat(),
            "speaker": "AI", 
            "type": "answer", 
            "content": "Yes, systems are nominal."
        }
    ]
    with open(log_file, "w") as f:
        json.dump(seed_data, f)
        
    # 2. Generate Report
    generator = ReportGenerator(log_dir=TEST_LOG_DIR, output_dir=TEST_REPORT_DIR)
    pdf_path = generator.generate_consolidated_report(hours=24)
    
    assert pdf_path
    assert os.path.exists(pdf_path)
    assert pdf_path.endswith(".pdf")
    assert os.path.getsize(pdf_path) > 1000

def test_api_health():
    """Verify Health Endpoint"""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_api_report():
    """Verify Consolidated Report Endpoint"""
    # This uses the default logs directory which we seeded manually with 'demo-session-123' earlier
    client = TestClient(app)
    response = client.get("/api/reports/consolidated?hours=24")
    
    # Check assertions
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert len(response.content) > 1000
