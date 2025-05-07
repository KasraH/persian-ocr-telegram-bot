import logging
import os
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
import smtplib
from email.mime.text import MIMEText
import google.generativeai as genai
import fitz
from PIL import Image
import io
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
USER_EMAIL = os.getenv("USER_EMAIL")

# Convert AUTHORIZED_USERS string to list of integers
AUTHORIZED_USERS_STR = os.getenv("AUTHORIZED_USERS", "")
AUTHORIZED_USERS = [int(user_id)
                    for user_id in AUTHORIZED_USERS_STR.split(",") if user_id]

# Configure Gemini API
genai.configure(api_key=GOOGLE_API_KEY)
gemini_model = genai.GenerativeModel(
    'gemini-1.5-pro')  # Best model for Persian OCR


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    await update.message.reply_text(
        "Welcome to the Persian OCR Bot! Send me images or PDFs containing Persian text, "
        "and I'll extract the text for you.\n"
        "After each extraction, you'll see a button to send the result to your email."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        return

    await update.message.reply_text(
        "Send me an image or PDF containing Persian text and I'll extract it.\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Get help information\n\n"
        "After each extraction, you'll see a button to send the result to your email."
    )

# Function to send email


def send_email(to_email, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = to_email

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())
        logger.info("Email sent successfully!")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Authentication error. Please check your email and password.")
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks for sending emails."""
    query = update.callback_query
    await query.answer()

    # Extract the data from the callback
    callback_data = query.data

    if callback_data.startswith('send_email:'):
        message_id = callback_data.split(':')[1]

        # Get the text from user_data
        if 'extractions' in context.user_data and message_id in context.user_data['extractions']:
            extracted_text = context.user_data['extractions'][message_id]

            # Send email
            if send_email(USER_EMAIL, "Extracted Persian Text", extracted_text):
                # Remove the button
                await query.edit_message_reply_markup(None)
                await query.message.reply_text(f"✅ Text sent to {USER_EMAIL}")
            else:
                await query.message.reply_text("❌ Failed to send email. Please check the logs.")
        else:
            await query.message.reply_text("❌ Could not find the extracted text.")


async def process_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process photos for Persian OCR."""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    # Get the largest photo
    photo = update.message.photo[-1]

    # Inform the user
    processing_message = await update.message.reply_text("Processing your image...")

    try:
        # Get the file and save it
        photo_file = await context.bot.get_file(photo.file_id)
        photo_data = await photo_file.download_as_bytearray()

        # Convert to PIL Image
        image = Image.open(io.BytesIO(photo_data))

        # Extract text using Gemini Pro
        await update.message.reply_text("Extracting Persian text with Gemini...")
        response = gemini_model.generate_content([
            "Extract and transcribe any Persian text in this image. Return ONLY the Persian text, no explanations.",
            image
        ])

        extracted_text = response.text

        # Send the extracted text
        await update.message.reply_text("✅ Extracted Persian Text:")
        result_message = await update.message.reply_text(extracted_text)

        # Create a unique ID for this extraction
        message_id = f"img_{result_message.message_id}"

        # Initialize extractions dict if it doesn't exist
        if 'extractions' not in context.user_data:
            context.user_data['extractions'] = {}

        # Store the extraction for later use
        context.user_data['extractions'][message_id] = extracted_text

        # Create keyboard with email button
        keyboard = [
            [InlineKeyboardButton(
                "Send to Email", callback_data=f"send_email:{message_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Add the button to the message
        await update.message.reply_text("Would you like to send this text to your email?", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        await update.message.reply_text(f"Error processing image: {str(e)}")

    # Delete processing message
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_message.message_id)


async def process_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process PDF documents for Persian OCR."""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    document = update.message.document
    file_name = document.file_name

    if not file_name.lower().endswith('.pdf'):
        await update.message.reply_text("Please send a PDF document.")
        return

    # Inform the user
    processing_message = await update.message.reply_text("Processing your PDF...")

    try:
        # Get the file and save it temporarily
        doc_file = await context.bot.get_file(document.file_id)
        doc_bytes = await doc_file.download_as_bytearray()

        # Create a temporary file for the PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            temp_file.write(doc_bytes)
            pdf_path = temp_file.name

        # Open the PDF with PyMuPDF
        await update.message.reply_text(f"Opening PDF... ({file_name})")
        pdf_document = fitz.open(pdf_path)

        all_text = ""
        page_count = len(pdf_document)

        await update.message.reply_text(f"Found {page_count} pages. Processing...")

        # Process up to the first 5 pages to avoid exceeding limits
        max_pages = min(page_count, 5)
        for page_num in range(max_pages):
            await update.message.reply_text(f"Processing page {page_num + 1}/{max_pages}...")

            # Get page and render to image
            page = pdf_document.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(
                2.0, 2.0))  # 2x zoom for better OCR

            # Convert to PIL Image
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data))

            # Extract text using Gemini Pro
            response = gemini_model.generate_content([
                "Extract and transcribe any Persian text in this image. Return ONLY the Persian text, no explanations.",
                image
            ])

            page_text = response.text.strip()

            # Add page text to total text
            if page_text:
                all_text += f"\n--- Page {page_num + 1} ---\n{page_text}\n"
                await update.message.reply_text(f"Page {page_num + 1} text extracted")
            else:
                all_text += f"\n--- Page {page_num + 1}: No text detected ---\n"
                await update.message.reply_text(f"No text found on page {page_num + 1}")

        # Close the PDF
        pdf_document.close()

        # Delete temporary file
        os.unlink(pdf_path)

        # Send the complete extracted text
        if all_text.strip():
            await update.message.reply_text("✅ Extracted Persian Text from PDF:")

            # Split text into chunks if it's too long
            chunks = [all_text[i:i+4000]
                      for i in range(0, len(all_text), 4000)]

            complete_text = all_text  # Store complete text for email

            for i, chunk in enumerate(chunks):
                result_message = await update.message.reply_text(f"{chunk}")
                if i < len(chunks) - 1:
                    await update.message.reply_text("(continued...)")

            # Create a unique ID for this extraction
            message_id = f"pdf_{result_message.message_id}"

            # Initialize extractions dict if it doesn't exist
            if 'extractions' not in context.user_data:
                context.user_data['extractions'] = {}

            # Store the extraction for later use
            context.user_data['extractions'][message_id] = complete_text

            # Create keyboard with email button
            keyboard = [
                [InlineKeyboardButton(
                    "Send to Email", callback_data=f"send_email:{message_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Add the button to the message
            await update.message.reply_text("Would you like to send this text to your email?", reply_markup=reply_markup)
        else:
            await update.message.reply_text("No Persian text detected in the PDF.")

    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        await update.message.reply_text(f"Error processing PDF: {str(e)}")

    # Delete processing message
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_message.message_id)


def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Add callback query handler for the inline buttons
    application.add_handler(CallbackQueryHandler(handle_button_callback))

    # Add message handlers
    application.add_handler(MessageHandler(filters.PHOTO, process_image))
    application.add_handler(MessageHandler(
        filters.Document.PDF, process_document))

    # Run the bot
    application.run_polling()


if __name__ == "__main__":
    main()
