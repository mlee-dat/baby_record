from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from datetime import datetime
from pathlib import Path
import gspread
from openai import OpenAI
import traceback

# Response format for intent classification
INTENT_FORMAT = {
    'type': 'json',
    'name': 'intent_classification',
    'description': 'Classify user intent as record or query',
    'schema': {
        'type': 'object',
        'properties': {
            'intent': {'type': 'string', 'enum': ['record', 'query'], 'description': 'Whether to record or query data'},
            'activity_type': {'type': 'string', 'description': 'Type of activity (분유, 모유, 수면, 기저귀, etc.)'},
            'amount': {'type': 'string', 'description': 'Amount or duration (for record)'},
            'memo': {'type': 'string', 'description': 'Additional notes'}
        },
        'required': ['intent']
    }
}


class handler(BaseHTTPRequestHandler):

    def _send_error_response(self, status_code, message):
        """Helper to send error responses"""
        self.send_response(status_code)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(message.encode('utf-8'))

    def _send_response(self, message):
        """Helper to send success responses"""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(message.encode('utf-8'))

    def classify_intent(self, client, user_input):
        """Classify user intent using LLM"""
        completion = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """사용자의 입력을 분석하여 의도를 분류하세요.

'record': 새로운 활동을 기록하는 경우 (예: "밥 먹었어", "잤어", "기저귀 똥", "분유 100ml")
'query': 기존 기록을 조회하는 경우 (예: "마지막으로 언제 먹었어?", "오늘 몇 번 먹었어?", "최근 수면 기록")

activity_type: 활동 종류 (분유, 모유, 수면, 기저귀 등)
amount: 기록할 양이나 시간 (record인 경우)
memo: 추가 메모

모든 값은 한국어로 반환하세요."""
                },
                {"role": "user", "content": user_input}
            ],
            response_format=INTENT_FORMAT
        )

        content = completion.choices[0].message.content
        print(f"Intent Classification: {content}")
        return json.loads(content)

    def handle_record(self, gc, intent_data):
        """Handle recording a new activity"""
        activity_type = intent_data.get('activity_type', '')
        amount = intent_data.get('amount', '')
        memo = intent_data.get('memo', '')

        # Append to sheet
        sh = gc.open("jihoo").sheet1
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sh.append_row([current_time, activity_type, amount, memo])

        return f"네, {activity_type} {amount} 기록했습니다."

    def handle_query(self, client, gc, user_input, intent_data):
        """Handle querying existing records"""
        # Fetch recent records from sheet
        sh = gc.open("jihoo").sheet1
        all_records = sh.get_all_records()  # Uses headers: time, type, value, memo

        print(f"=== QUERY DEBUG ===")
        print(f"User input: {user_input}")
        print(f"Total records in sheet: {len(all_records)}")

        # Get last 50 records (no type filtering - let LLM figure it out)
        recent = all_records[-50:] if len(all_records) > 50 else all_records
        print(f"Sending {len(recent)} records to LLM")

        # Format as table for better structure
        # Calculate column widths for alignment
        max_time_len = max(len(r.get('time', '')) for r in recent) if recent else 10
        max_type_len = max(len(r.get('type', '')) for r in recent) if recent else 4
        max_value_len = max(len(r.get('value', '')) for r in recent) if recent else 5
        max_memo_len = max(len(r.get('memo', '')) for r in recent) if recent else 4

        # Build table
        table_lines = []
        table_lines.append(f"{'시간':<{max_time_len}} | {'종류':<{max_type_len}} | {'양':<{max_value_len}} | {'메모':<{max_memo_len}}")
        table_lines.append("-" * (max_time_len + max_type_len + max_value_len + max_memo_len + 9))

        for r in recent:
            time = r.get('time', '')
            type_ = r.get('type', '')
            value = r.get('value', '')
            memo = r.get('memo', '')
            table_lines.append(f"{time:<{max_time_len}} | {type_:<{max_type_len}} | {value:<{max_value_len}} | {memo:<{max_memo_len}}")

        records_table = "\n".join(table_lines)

        print(f"Records table:\n{records_table}")

        # Ask LLM to generate natural response
        completion = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"""당신은 육아 도우미입니다. 다음 최근 기록을 참고하여 사용자의 질문에 자연스러운 한국어로 답변하세요.

최근 기록 (최신 순):
{records_table}

답변은 간결하고 친근하게 하세요. 시간은 읽기 쉽게 변환하세요."""
                },
                {"role": "user", "content": user_input}
            ]
        )

        return completion.choices[0].message.content

    def do_POST(self):
        try:
            openai_key = os.environ.get('OPENAI_API_KEY')
            google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')

            content_length = self.headers.get('content-length')
            if not content_length:
                self._send_error_response(400, "Missing content-length header")
                return

            length = int(content_length)
            raw_data = self.rfile.read(length)
            request_data = json.loads(raw_data)
            user_input = request_data.get('text', '').strip()

            if not user_input:
                self._send_error_response(400, "Missing 'text' field in request")
                return

            # Initialize clients
            client = OpenAI(api_key=openai_key, base_url="https://ai-gateway.vercel.sh/v1")
            creds_dict = json.loads(google_creds_json)
            gc = gspread.service_account_from_dict(creds_dict)

            # Classify intent
            intent_data = self.classify_intent(client, user_input)

            # Handle based on intent
            intent = intent_data.get('intent', 'record')

            if intent == 'record':
                response_msg = self.handle_record(gc, intent_data)
            else:  # query
                response_msg = self.handle_query(client, gc, user_input, intent_data)

            self._send_response(response_msg)

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

    print("1. Testing OpenAI connection...")
    try:
        client = OpenAI(api_key=openai_key, base_url="https://ai-gateway.vercel.sh/v1")
        completion = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "테스트"}],
            stream=False,
        )
        print(f"   ✓ OpenAI connection successful!\n")
    except Exception as e:
        print(f"   ✗ OpenAI connection failed: {e}\n")
        traceback.print_exc()
        return False

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

    print("=== All tests passed! ===")
    return True


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    openai_key = os.environ.get('OPENAI_API_KEY')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')

    if not openai_key:
        print("OPENAI_API_KEY not found in environment variables.")
        openai_key = input("Enter your OpenAI API Key: ").strip()

    if not google_creds_json:
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

    if test_connections(openai_key, google_creds_json):
        print("\nServer is ready!")
