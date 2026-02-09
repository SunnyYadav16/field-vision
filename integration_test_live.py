import asyncio
import websockets
import json
import requests
import time

WS_URL = "ws://localhost:8000/ws"
REPORT_URL = "http://localhost:8000/api/reports/consolidated?hours=24"

async def test_live_session():
    print(f"Connecting to {WS_URL}...")
    try:
        async with websockets.connect(WS_URL) as websocket:
            print("Connected.")
            
            # Start Session
            start_payload = {
                "type": "start_session",
                "payload": {}
            }
            await websocket.send(json.dumps(start_payload))
            print("Sent start_session.")
            
            session_id = None
            
            # Wait for SESSION_STARTED
            response = await websocket.recv()
            data = json.loads(response)
            if data["type"] == "session_started":
                session_id = data["payload"]["session_id"]
                print(f"Session Started: {session_id}")
            else:
                print(f"Unexpected response: {data}")
                return

            # Turn 1: Question 1
            question1 = "Is the area safe?"
            print(f"Sending Question 1: {question1}")
            await websocket.send(json.dumps({
                "type": "text_message",
                "payload": {"text": question1}
            }))
            
            # Wait for response (might get session_started ack or audio/text)
            # We expect TEXT_RESPONSE eventually
            got_response = False
            start_wait = time.time()
            while not got_response and time.time() - start_wait < 10:
                msg = await websocket.recv()
                data = json.loads(msg)
                print(f"Received: {data['type']}")
                if data["type"] == "text_response":
                    print(f"AI Answer 1: {data['payload']['text']}")
                    got_response = True
            
            if not got_response:
                print("Failed to get response for Question 1")
                return

            # Pause to simulate thinking/reading
            await asyncio.sleep(2)

            # Turn 2: Question 2 (Persistence Test)
            question2 = "What should I scan?"
            print(f"Sending Question 2: {question2}")
            await websocket.send(json.dumps({
                "type": "text_message",
                "payload": {"text": question2}
            }))
            
            got_response2 = False
            start_wait = time.time()
            while not got_response2 and time.time() - start_wait < 10:
                msg = await websocket.recv()
                data = json.loads(msg)
                print(f"Received: {data['type']}")
                if data["type"] == "text_response":
                    print(f"AI Answer 2: {data['payload']['text']}")
                    got_response2 = True

            if not got_response2:
                print("Failed to get response for Question 2 (Persistence Failed?)")
                return
            else:
                print("Persistence Verified: Received second answer.")

            # End Session
            print("Ending Session...")
            await websocket.send(json.dumps({"type": "end_session", "payload": {}}))
            
            # Wait for summary
            summary_received = False
            while not summary_received and time.time() - start_wait < 5:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    data = json.loads(msg)
                    if data["type"] == "session_ended":
                        print("Session Ended confirmed.")
                        summary_received = True
                except asyncio.TimeoutError:
                    break
                    
    except Exception as e:
        print(f"WebSocket Error: {e}")
        return

    # Phase 2: Report Download
    print("\nTesting Report Download...")
    try:
        r = requests.get(REPORT_URL)
        if r.status_code == 200 and r.headers['content-type'] == 'application/pdf':
            print(f"Report Generated Successfully! Size: {len(r.content)} bytes")
            with open("live_test_report.pdf", "wb") as f:
                f.write(r.content)
            print("Saved to live_test_report.pdf")
        else:
            print(f"Report Failed: {r.status_code}")
    except Exception as e:
        print(f"Report Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_live_session())
