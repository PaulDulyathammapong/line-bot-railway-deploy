# app.py (Final Version with Ultimate Combo Support)

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
    TemplateMessage, ButtonsTemplate, URIAction,
    VideoMessage, AudioMessage
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, FollowEvent
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
        
        simple_qna_sheet = spreadsheet.worksheet('SimpleQnA')
        digital_sheet = spreadsheet.worksheet('DigitalQnA')
        general_sheet = spreadsheet.worksheet('GeneralQnA')
        
        app.logger.info("Successfully connected to all Google Sheets.")
    except gspread.exceptions.WorksheetNotFound as e:
        app.logger.error(f"A worksheet was not found. Please check your sheet names. Error: {e}")
        gs_client = None
    except Exception as e:
        app.logger.error(f"Error connecting to Google Sheets: {e}")
        gs_client = None

# --- Helper Function to Search a Sheet ---
def find_reply_in_sheet(sheet, user_message):
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

                if response_type == 'text':
                    reply_template = row.get('TextReply', '')
                    if '{num}' in reply_template and match.groups():
                        reply_text = reply_template.format(num=match.group(1))
                    else:
                        reply_text = reply_template
                    return [TextMessage(text=reply_text)]

                elif response_type == 'image':
                    img_url = row.get('ImageURL1')
                    return [ImageMessage(original_content_url=img_url, preview_image_url=img_url)]

                elif response_type == 'video':
                    video_url = row.get('VideoURL')
                    preview_url = row.get('PreviewImageURL')
                    return [VideoMessage(original_content_url=video_url, preview_image_url=preview_url)]
                
                elif response_type == 'audio':
                    audio_url = row.get('AudioURL')
                    duration = int(row.get('DurationMillis', 60000))
                    return [AudioMessage(original_content_url=audio_url, duration=duration)]

                elif response_type == 'redirect':
                    button_label = row.get('ButtonLabel', 'คลิกที่นี่')
                    text_above_button = row.get('TextReply', 'กรุณากดปุ่มด้านล่างเพื่อดำเนินการต่อ')
                    redirect_uri = row.get('RedirectURL') or f"https://line.me/R/ti/p/{row.get('RedirectOA_ID')}"
                    
                    return [TemplateMessage(
                        alt_text='Information',
                        template=ButtonsTemplate(
                            text=text_above_button,
                            actions=[URIAction(label=button_label, uri=redirect_uri)]
                        )
                    )]
                
                elif response_type == 'combo':
                    # 1. Add Text if it exists
                    if row.get('TextReply'):
                        messages_to_reply.append(TextMessage(text=row.get('TextReply')))
                    
                    # 2. Add Images
                    for i in range(1, 5):
                        if len(messages_to_reply) < 5 and row.get(f'ImageURL{i}'):
                            img_url = row.get(f'ImageURL{i}')
                            messages_to_reply.append(ImageMessage(original_content_url=img_url, preview_image_url=img_url))
                    
                    # 3. Add Video
                    if len(messages_to_reply) < 5 and row.get('VideoURL') and row.get('PreviewImageURL'):
                        video_url = row.get('VideoURL')
                        preview_url = row.get('PreviewImageURL')
                        messages_to_reply.append(VideoMessage(original_content_url=video_url, preview_image_url=preview_url))

                    # 4. Add Button
                    if len(messages_to_reply) < 5 and (row.get('RedirectOA_ID') or row.get('RedirectURL')):
                        button_label = row.get('ButtonLabel', 'คลิกที่นี่')
                        text_above_button = row.get('ButtonLabel', 'ดำเนินการต่อ') 
                        redirect_uri = row.get('RedirectURL') or f"https://line.me/R/ti/p/{row.get('RedirectOA_ID')}"
                        
                        messages_to_reply.append(TemplateMessage(
                            alt_text='Information',
                            template=ButtonsTemplate(
                                text=text_above_button,
                                actions=[URIAction(label=button_label, uri=redirect_uri)]
                            )
                        ))
                    return messages_to_reply
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

# --- Greeting Message Handler ---
@handler.add(FollowEvent)
def handle_follow(event):
    reply_messages = None
    if general_sheet:
        reply_messages = find_reply_in_sheet(general_sheet, "@follow")
    
    if not reply_messages:
        reply_messages = [TextMessage(text="สวัสดีค่ะ! ยินดีต้อนรับสู่ Work Inn AI ค่ะ")]
        
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=reply_messages
            )
        )

# --- Text Message Handler ---
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_message = event.message.text.strip()
    reply_messages = None
    
    sheets_to_search = [simple_qna_sheet, digital_sheet, general_sheet]

    for sheet in sheets_to_search:
        if sheet:
            reply_messages = find_reply_in_sheet(sheet, user_message)
            if reply_messages:
                break

    if not reply_messages:
        reply_messages = [TextMessage(text="ขออภัยค่ะ ไม่พบข้อมูลที่ท่านสอบถาม")]

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

