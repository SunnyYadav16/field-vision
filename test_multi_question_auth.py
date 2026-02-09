import asyncio
import websockets
import json
import requests
import time

LOGIN_URL = "http://localhost:8000/api/login"
WS_URL = "ws://localhost:8000/ws"

async def test_multi_question_authenticated():
    print("=" * 60)
    print("FIELDVISION AUTHENTICATED MULTI-QUESTION TEST")
    print("Testing: Auth + History Persistence + turn_complete")
    print("=" * 60)
    
    # login
    print("\n[0] Authenticating as sup_007...")
    try:
        r = requests.post(LOGIN_URL, json={
            "user_id": "sup_007",
            "password": "super789"
        })
        if r.status_code == 200:
            token = r.json()["token"]
            print(f"    [OK] Login successful, token: {token[:15]}...")
        else:
            print(f"    [FAIL] Login failed: {r.status_code} - {r.text}")
            return
    except Exception as e:
        print(f"    [FAIL] Auth Error: {e}")
        return

    questions = [
        "What safety equipment should I wear?",
        "Is there any hazard in the area?", # Follow up 1
        "What is the evacuation route?",    # Follow up 2
        "Can you summarize our conversation?" # Testing history
    ]
    
    auth_ws_url = f"{WS_URL}?token={token}"
    
    turn_completes_received = 0
    text_responses = []
    
    try:
        print(f"\n[1] Connecting to {WS_URL} with token...")
        async with websockets.connect(auth_ws_url) as websocket:
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

            # Ask multiple questions
            for i, question in enumerate(questions, 1):
                print(f"\n[3.{i}] Sending Question {i}: \"{question}\"")
                await websocket.send(json.dumps({
                    "type": "text_message",
                    "payload": {"text": question}
                }))
                
                got_turn_complete = False
                current_response_text = ""
                start_time = time.time()
                
                # Wait for turn_complete (30s max per turn)
                while time.time() - start_time < 30 and not got_turn_complete:
                    try:
                        msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(msg)
                        msg_type = data["type"]
                        
                        if msg_type == "text_response":
                            text = data["payload"]["text"]
                            current_response_text += text
                            preview = text[:60] + "..." if len(text) > 60 else text
                            print(f"      [TEXT] {preview}")
                            
                        elif msg_type == "turn_complete":
                            turn_completes_received += 1
                            print(f"      [TURN_COMPLETE] Gemini finished responding")
                            got_turn_complete = True
                            text_responses.append(current_response_text)
                            
                    except asyncio.TimeoutError:
                        continue
                
                if not got_turn_complete:
                    print(f"      [TIMEOUT] Q{i} did not receive turn_complete")

            # End Session
            print(f"\n[4] Ending session...")
            await websocket.send(json.dumps({"type": "end_session", "payload": {}}))
            
    except Exception as e:
        print(f"[FAIL] WebSocket Error: {e}")
        return

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Questions Asked:      {len(questions)}")
    print(f"Turn Completes:       {turn_completes_received}")
    
    # Verify History (Quick check if last response summarizes previous ones)
    if len(text_responses) == 4:
        last_response = text_responses[3].lower()
        if any(word in last_response for word in ["wear", "hazard", "evacuation", "safety"]):
             print("\n[PASS] HISTORY: AI correctly referenced previous conversation.")
        else:
             print("\n[WARN] HISTORY: AI response did not clearly reference history.")
    
    if turn_completes_received >= len(questions):
        print("[PASS] SIGNALING: All turn_complete events received.")
    else:
        print(f"[FAIL] SIGNALING: Only {turn_completes_received}/{len(questions)} turn_completes.")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_multi_question_authenticated())
