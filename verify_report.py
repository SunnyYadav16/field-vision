import urllib.request
import os

url = "http://localhost:8000/api/reports/consolidated?hours=24"
try:
    print(f"Requesting {url}...")
    with urllib.request.urlopen(url) as response:
        print(f"Response Code: {response.getcode()}")
        headers = response.info()
        print(f"Content-Type: {headers.get_content_type()}")
        
        content = response.read()
        print(f"Content Length: {len(content)} bytes")
        
        if response.getcode() == 200 and headers.get_content_type() == 'application/pdf' and len(content) > 1000:
            print("SUCCESS: Report generated successfully.")
            # Save for inspection if needed locally
            with open("test_report.pdf", "wb") as f:
                f.write(content)
        else:
            print("FAILURE: Invalid response.")
except Exception as e:
    print(f"ERROR: {e}")
