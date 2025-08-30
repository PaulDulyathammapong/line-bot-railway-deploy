# app.py (Final Stable Version with reliable redirect)

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
if gspread_credentials_b64 and sheet_url:
    try:
        creds_json_str = base64.b64decode(gspread_credentials_b64).decode('utf-8')
        creds_json = json.loads(creds_json_str)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        gs_client = gspread.authorize(creds)
        spreadsheet = gs_client.open_by_url(sheet_url)
        qna_sheet = spreadsheet.worksheet('SimpleQnA')
        app.logger.info("Successfully connected to Google Sheets.")
    except Exception as e:
        app.logger.error(f"Error connecting to Google Sheets: {e}")
        gs_client = None

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

# --- Text Message Handler ---
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_message = event.message.text.strip()
    reply_messages = []

    default_reply = [TextMessage(text="ขออภัยค่ะ ไม่พบข้อมูลที่ท่านสอบถาม")]

    if gs_client:
        try:
            qna_data = qna_sheet.get_all_records()
            for row in qna_data:
                keyword_pattern = row.get('Keyword')
                if not keyword_pattern:
                    continue

                match = re.search(keyword_pattern, user_message, re.IGNORECASE)

                if match:
                    response_type = row.get('ResponseType')
                    
                    if response_type == 'text':
                        reply_template = row.get('TextReply', '')
                        if '{num}' in reply_template and match.groups():
                            extracted_num = match.group(1)
                            reply_text = reply_template.format(num=extracted_num)
                        else:
                            reply_text = reply_template
                        reply_messages.append(TextMessage(text=reply_text))

                    elif response_type == 'image':
                        if row.get('ImageURL'):
                            reply_messages.append(ImageMessage(original_content_url=row.get('ImageURL'), preview_image_url=row.get('ImageURL')))

                    elif response_type == 'combo':
                        # 1. Add Text Reply (if it exists)
                        if row.get('TextReply'):
                            reply_messages.append(TextMessage(text=row.get('TextReply')))
                        
                        # 2. Add multiple images (up to 4)
                        for i in range(1, 5):
                            if len(reply_messages) >= 5: break
                            image_url = row.get(f'ImageURL{i}')
                            if image_url:
                                reply_messages.append(ImageMessage(original_content_url=image_url, preview_image_url=image_url))

                        # 3. Add Redirect Button (if it exists and there's space)
                        if len(reply_messages) < 5:
                            button_label = row.get('ButtonLabel')
                            redirect_uri = ""
                            oa_id = row.get('RedirectOA_ID')
                            std_url = row.get('RedirectURL')

                            # UPDATED: Use the more stable "add friend" link for LINE OAs
                            if oa_id:
                                redirect_uri = f"https://line.me/R/ti/p/{oa_id}"
                            elif std_url:
                                redirect_uri = std_url

                            if button_label and redirect_uri:
                                button_text = row.get('TextReply', 'กรุณาเลือกเมนูด้านล่าง')
                                if row.get('TextReply') and any(isinstance(msg, TextMessage) for msg in reply_messages):
                                    button_text = "ตัวเลือกเพิ่มเติม"

                                reply_messages.append(TemplateMessage(
                                    alt_text='Information',
                                    template=ButtonsTemplate(
                                        text=button_text,
                                        actions=[
                                            URIAction(
                                                label=button_label,
                                                uri=redirect_uri
                                            )
                                        ]
                                    )
                                ))

                    if reply_messages:
                        break
        except Exception as e:
            app.logger.error(f"Error processing QnA sheet: {e}")
            reply_messages = [TextMessage(text="Sorry, there was an error processing your request.")]

    final_reply = reply_messages if reply_messages else default_reply
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=final_reply
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

