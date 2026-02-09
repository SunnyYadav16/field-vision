import asyncio
import websockets
import json
import requests
import time

WS_URL = "ws://localhost:8000/ws"
REPORT_URL = "http://localhost:8000/api/reports/consolidated?hours=24"

async def test_multi_question_with_turn_complete():
    print("=" * 60)
    print("FIELDVISION MULTI-QUESTION TEST (with Turn Completion)")
    print("Testing: Session Persistence + Audio + turn_complete Signal")
    print("=" * 60)
    
    questions = [
        "What safety equipment should I wear?",
        "Is there any hazard in the area?",
        "What is the evacuation route?",
        "Can you summarize the safety rules?"
    ]
    
    audio_responses_received = 0
    text_responses_received = 0
    turn_completes_received = 0
    audio_bytes_total = 0
    
    try:
        print(f"\n[1] Connecting to {WS_URL}...")
        async with websockets.connect(WS_URL) as websocket:
            print("    [OK] Connected successfully")
            
            # Start Session
            print("\n[2] Starting session...")
            await websocket.send(json.dumps({
                "type": "start_session",
                "payload": {}
            }))
            
            # Wait for SESSION_STARTED
            response = await websocket.recv()
            data = json.loads(response)
            if data["type"] == "session_started":
                session_id = data["payload"]["session_id"]
                print(f"    [OK] Session Started: {session_id[:8]}...")
            else:
                print(f"    [FAIL] Unexpected: {data}")
                return

            # Ask multiple questions - waiting for turn_complete before next question
            for i, question in enumerate(questions, 1):
                print(f"\n[3.{i}] Sending Question {i}: \"{question}\"")
                await websocket.send(json.dumps({
                    "type": "text_message",
                    "payload": {"text": question}
                }))
                
                # Wait for turn_complete (or timeout after 30 seconds)
                got_turn_complete = False
                got_text = False
                question_audio_chunks = 0
                start_time = time.time()
                
                while time.time() - start_time < 30 and not got_turn_complete:
                    try:
                        msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(msg)
                        msg_type = data["type"]
                        
                        if msg_type == "audio_response":
                            audio_responses_received += 1
                            question_audio_chunks += 1
                            audio_size = len(data["payload"].get("data", ""))
                            audio_bytes_total += audio_size
                            if question_audio_chunks == 1:
                                print(f"      [AUDIO] First chunk received ({audio_size} bytes)")
                        
                        elif msg_type == "text_response":
                            text_responses_received += 1
                            text = data["payload"]["text"]
                            preview = text[:60] + "..." if len(text) > 60 else text
                            print(f"      [TEXT] {preview}")
                            got_text = True
                            
                        elif msg_type == "turn_complete":
                            turn_completes_received += 1
                            print(f"      [TURN_COMPLETE] Gemini finished responding")
                            got_turn_complete = True
                            
                        elif msg_type == "tool_call":
                            func = data["payload"]["function"]
                            print(f"      [TOOL] {func}")
                            
                    except asyncio.TimeoutError:
                        continue
                
                if got_turn_complete:
                    print(f"      [OK] Q{i} complete (text: {got_text}, audio: {question_audio_chunks})")
                else:
                    print(f"      [TIMEOUT] Q{i} did not receive turn_complete in 30s")

            # End Session
            print(f"\n[4] Ending session...")
            await websocket.send(json.dumps({"type": "end_session", "payload": {}}))
            
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                data = json.loads(msg)
                if data["type"] == "session_ended":
                    print("    [OK] Session ended successfully")
            except asyncio.TimeoutError:
                print("    [WARN] Session end confirmation timeout")
                    
    except Exception as e:
        print(f"[FAIL] WebSocket Error: {e}")
        return

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Questions Asked:      {len(questions)}")
    print(f"Text Responses:       {text_responses_received}")
    print(f"Audio Chunks:         {audio_responses_received}")
    print(f"Turn Completes:       {turn_completes_received}")
    print(f"Total Audio Data:     {audio_bytes_total} bytes")
    
    # Verdict
    if turn_completes_received >= len(questions):
        print("\n[PASS] TURN COMPLETION: All questions completed properly")
    elif turn_completes_received > 0:
        print(f"\n[PARTIAL] TURN COMPLETION: {turn_completes_received}/{len(questions)}")
    else:
        print("\n[FAIL] TURN COMPLETION: No turn_complete signals received")
    
    if audio_responses_received > 0:
        print("[PASS] AUDIO: Audio responses received")
    else:
        print("[WARN] AUDIO: No audio responses detected")

    # Test Report Download
    print("\n[5] Testing Report Download...")
    try:
        r = requests.get(REPORT_URL)
        if r.status_code == 200 and 'application/pdf' in r.headers.get('content-type', ''):
            print(f"    [PASS] Report Generated: {len(r.content)} bytes")
            with open("turn_complete_report.pdf", "wb") as f:
                f.write(r.content)
            print("    Saved to turn_complete_report.pdf")
        else:
            print(f"    [FAIL] Report Failed: {r.status_code}")
    except Exception as e:
        print(f"    [FAIL] Report Error: {e}")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_multi_question_with_turn_complete())
