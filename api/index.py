from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from datetime import datetime
from pathlib import Path
import gspread
from openai import OpenAI
import traceback

# Response format schema for structured output
RESPONSE_FORMAT = {
    'type': 'json',
    'name': 'baby_activity',
    'description': 'A baby care activity record',
    'schema': {
        'type': 'object',
        'properties': {
            'type': {'type': 'string', 'description': 'Activity type (e.g., 분유, 모유, 수면, 기저귀)'},
            'amount': {'type': 'string', 'description': 'Amount or duration'},
            'memo': {'type': 'string', 'description': 'Additional notes'}
        },
        'required': ['type', 'amount']
    }
}


class handler(BaseHTTPRequestHandler):
    # Class attributes to store credentials (set from main)
    openai_key = None
    google_creds_json = None

    def _send_error_response(self, status_code, message):
        """Helper to send error responses"""
        self.send_response(status_code)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(message.encode('utf-8'))

    def do_POST(self):
        try:
            openai_key = os.environ.get('OPENAI_API_KEY')
            google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')

            # Get content length safely
            content_length = self.headers.get('content-length')
            if not content_length:
                self._send_error_response(400, "Missing content-length header")
                return

            # Parse request data
            length = int(content_length)
            raw_data = self.rfile.read(length)
            request_data = json.loads(raw_data)
            user_input = request_data.get('text', '').strip()

            if not user_input:
                self._send_error_response(400, "Missing 'text' field in request")
                return

            # Initialize OpenAI client
            client = OpenAI(api_key=openai_key, base_url="https://ai-gateway.vercel.sh/v1")

            # Get structured response from LLM
            completion = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Parse baby care activity into structured data"},
                    {"role": "user", "content": user_input}
                ],
                stream=False,
                response_format=RESPONSE_FORMAT
            )

            content = completion.choices[0].message.content
            # Debug: log the raw response
            print(f"LLM Response: {content}")

            ai_data = json.loads(content)

            # Handle structured output that returns the schema wrapper
            if 'properties' in ai_data and isinstance(ai_data.get('properties'), dict):
                # Extract actual values from the properties
                props = ai_data['properties']
                ai_data = {
                    'type': props.get('type', ''),
                    'amount': props.get('amount', ''),
                    'memo': props.get('memo', '')
                }

            # Initialize Google Sheets
            creds_dict = json.loads(google_creds_json)
            gc = gspread.service_account_from_dict(creds_dict)
            sh = gc.open("jihoo").sheet1

            # Append to sheet: time, type, value, memo
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sh.append_row([
                current_time,
                ai_data.get('type', ''),
                ai_data.get('amount', ''),
                ai_data.get('memo', '')
            ])

            # Send success response
            response_msg = f"네, {ai_data.get('type')} {ai_data.get('amount')} 기록했습니다."
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(response_msg.encode('utf-8'))

        except json.JSONDecodeError as e:
            self._send_error_response(400, f"Invalid JSON in request: {e}")
        except gspread.SpreadsheetNotFound:
            self._send_error_response(500, "Spreadsheet 'jihoo' not found or access denied")
        except Exception as e:
            self._send_error_response(500, f"Server error: {e}")
            traceback.print_exc()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("BabyLog Server is Running!".encode('utf-8'))


def test_connections(openai_key, google_creds_json):
    """Test OpenAI and Google Sheets connections"""
    print("=== Testing Connections ===\n")

    # Test OpenAI
    print("1. Testing OpenAI connection...")
    try:
        client = OpenAI(api_key=openai_key, base_url="https://ai-gateway.vercel.sh/v1")
        completion = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Parse baby care activity into structured data"},
                {"role": "user", "content": "아기가 분유 170ml 먹었어"}
            ],
            stream=False,
            response_format=RESPONSE_FORMAT
        )
        ai_data = json.loads(completion.choices[0].message.content)
        print(f"   ✓ OpenAI connection successful!")
        print(f"   Response: {ai_data}\n")
    except Exception as e:
        print(f"   ✗ OpenAI connection failed: {e}\n")
        traceback.print_exc()
        return False

    # Test Google Sheets
    print("2. Testing Google Sheets connection...")
    try:
        creds_dict = json.loads(google_creds_json)
        service_email = creds_dict.get('client_email')
        print(f"   Service Account Email: {service_email}")

        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open("jihoo")
        print(f"   ✓ Google Sheets connection successful!")
        print(f"   Spreadsheet: {sh.title}\n")
    except gspread.SpreadsheetNotFound:
        print(f"   ✗ Spreadsheet 'jihoo' not found!")
        print(f"   Make sure:")
        print(f"   1. The spreadsheet name is exactly 'jihoo'")
        print(f"   2. The spreadsheet is shared with: {service_email}")
        print(f"   3. The service account has Editor permission\n")
        return False
    except Exception as e:
        print(f"   ✗ Google Sheets connection failed: {e}\n")
        traceback.print_exc()
        return False

    # Test writing to Google Sheets
    print("3. Testing write to Google Sheets...")
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        test_row = [current_time, "테스트", "100", "자동 테스트 기록"]
        sh.sheet1.append_row(test_row)
        print(f"   ✓ Write successful!")
        print(f"   Added row: {test_row}\n")
    except Exception as e:
        print(f"   ✗ Write failed: {e}\n")
        traceback.print_exc()
        return False

    print("=== All tests passed! ===")
    return True


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    # Try to get credentials from environment variables first
    openai_key = os.environ.get('OPENAI_API_KEY')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')

    # If not found, prompt user for input
    if not openai_key:
        print("OPENAI_API_KEY not found in environment variables.")
        openai_key = input("Enter your OpenAI API Key: ").strip()

    if not google_creds_json:
        # Automatically find JSON file in project root
        project_root = Path(__file__).parent.parent
        json_files = list(project_root.glob("*.json"))

        if json_files:
            google_creds_path = json_files[0]
            print(f"Found Google credentials file: {google_creds_path.name}")
            try:
                with open(google_creds_path, 'r', encoding='utf-8') as f:
                    google_creds_json = f.read()
            except Exception as e:
                print(f"Error reading file: {e}")
                exit(1)
        else:
            print("No JSON file found in project root.")
            exit(1)

    print(f"\nOpenAI API Key: {'*' * 20}{openai_key[-4:] if openai_key else 'Not set'}")
    print(f"Google Credentials: {'Loaded' if google_creds_json else 'Not set'}\n")

    # Set credentials as class attributes for the handler
    handler.openai_key = openai_key
    handler.google_creds_json = google_creds_json

    # Run tests
    if test_connections(openai_key, google_creds_json):
        print("\nServer is ready! You can now deploy or run the server manually.")
