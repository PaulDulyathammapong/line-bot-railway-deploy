# app.py (Final Version with Multi-Sheet Knowledge Base - Updated)

import os
import json
import re
import gspread
import base64
from flask import Flask, request, abort
from oauth2client.service_account import ServiceAccountCredentials
from urllib.parse import quote

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, ImageMessage,
    TemplateMessage, ButtonsTemplate, URIAction
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent
)

# --- Configuration ---
app = Flask(__name__)

# Load secrets from Railway environment variables
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
sheet_url = os.getenv('SHEET_URL', None)
gspread_credentials_b64 = os.getenv('GSPREAD_CREDENTIALS', None)

# Initialize clients
configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

# --- Google Sheets Setup ---
gs_client = None
simple_qna_sheet = None
digital_sheet = None
general_sheet = None

if gspread_credentials_b64 and sheet_url:
    try:
        creds_json_str = base64.b64decode(gspread_credentials_b64).decode('utf-8')
        creds_json = json.loads(creds_json_str)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        gs_client = gspread.authorize(creds)
        spreadsheet = gs_client.open_by_url(sheet_url)
        
        # Connect to each knowledge base sheet by name
        simple_qna_sheet = spreadsheet.worksheet('SimpleQnA')
        digital_sheet = spreadsheet.worksheet('DigitalQnA')
        general_sheet = spreadsheet.worksheet('GeneralQnA')
        
        app.logger.info("Successfully connected to all Google Sheets.")
    except gspread.exceptions.WorksheetNotFound:
        app.logger.error("A worksheet was not found. Please check your sheet names: SimpleQnA, DigitalQnA, GeneralQnA.")
        gs_client = None
    except Exception as e:
        app.logger.error(f"Error connecting to Google Sheets: {e}")
        gs_client = None

# --- Helper Function to Search a Sheet ---
def find_reply_in_sheet(sheet, user_message, event):
    try:
        qna_data = sheet.get_all_records()
        for row in qna_data:
            keyword_pattern = row.get('Keyword')
            if not keyword_pattern:
                continue

            match = re.search(keyword_pattern, user_message, re.IGNORECASE)
            if match:
                response_type = row.get('ResponseType')
                messages_to_reply = []

                if response_type == 'combo':
                    if row.get('TextReply'):
                        messages_to_reply.append(TextMessage(text=row.get('TextReply')))
                    
                    for i in range(1, 5):
                        if row.get(f'ImageURL{i}'):
                            img_url = row.get(f'ImageURL{i}')
                            messages_to_reply.append(ImageMessage(original_content_url=img_url, preview_image_url=img_url))

                    if row.get('RedirectOA_ID') or row.get('RedirectURL'):
                         button_label = row.get('ButtonLabel', 'คลิกที่นี่')
                         text_above_button = row.get('TextReply', 'กรุณากดปุ่มด้านล่าง')
                         
                         redirect_uri = ""
                         if row.get('RedirectOA_ID'):
                             encoded_message = quote(user_message)
                             redirect_uri = f"https://line.me/R/oaMessage/{row.get('RedirectOA_ID')}/?{encoded_message}"
                         elif row.get('RedirectURL'):
                             redirect_uri = row.get('RedirectURL')

                         messages_to_reply.append(TemplateMessage(
                            alt_text='Information',
                            template=ButtonsTemplate(
                                text=text_above_button,
                                actions=[URIAction(label=button_label, uri=redirect_uri)]
                            )
                         ))
                    return messages_to_reply

                elif response_type == 'text':
                    return [TextMessage(text=row.get('TextReply', ''))]
                elif response_type == 'image':
                    img_url = row.get('ImageURL1')
                    return [ImageMessage(original_content_url=img_url, preview_image_url=img_url)]
                elif response_type == 'redirect':
                    button_label = row.get('ButtonLabel', 'คลิกที่นี่')
                    redirect_uri = row.get('RedirectURL') or f"https://line.me/R/ti/p/{row.get('RedirectOA_ID')}"
                    return [TemplateMessage(
                        alt_text='Information',
                        template=ButtonsTemplate(
                            text=row.get('TextReply', ''),
                            actions=[URIAction(label=button_label, uri=redirect_uri)]
                        )
                    )]
        return None
    except Exception as e:
        app.logger.error(f"Error reading sheet {sheet.title}: {e}")
        return [TextMessage(text="Sorry, there was an error reading the database.")]

# --- Webhook Route ---
@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- Text Message Handler (Upgraded with Category Logic) ---
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_message = event.message.text.strip()
    reply_messages = None
    
    # --- Search Logic with Priority ---
    # Define the order of sheets to search
    sheets_to_search = [simple_qna_sheet, digital_sheet, general_sheet]

    for sheet in sheets_to_search:
        if sheet:
            reply_messages = find_reply_in_sheet(sheet, user_message, event)
            if reply_messages:
                break

    if not reply_messages:
        reply_messages = [TextMessage(text="ขออภัยค่ะ ไม่พบข้อมูลที่ท่านสอบถาม")]

    # Send the final reply
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=reply_messages
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

