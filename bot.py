import logging
import os
import tempfile
import asyncio
import time
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

# Updated Gemini models list - using only 2.0 models since 1.5 will be discontinued
# Models in priority order
MODELS = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]
MODEL_USAGE = {model: {"count": 0, "last_used": 0, "errors": 0}
               for model in MODELS}
current_model_idx = 0

# Configure Gemini API
genai.configure(api_key=GOOGLE_API_KEY)


def get_current_model():
    """Get the current model based on the model index."""
    return MODELS[current_model_idx]


def create_gemini_model():
    """Create and return a Gemini model with the current configuration."""
    model_name = get_current_model()
    logger.info(f"Using model: {model_name}")
    return genai.GenerativeModel(model_name)


async def rotate_model_on_error():
    """Rotate to the next available model on error."""
    global current_model_idx
    # Record the error for the current model
    current_model = get_current_model()
    MODEL_USAGE[current_model]["errors"] += 1

    # Move to the next model
    current_model_idx = (current_model_idx + 1) % len(MODELS)
    logger.warning(f"Rate limit hit. Rotating to model: {get_current_model()}")

    # Add a small delay before trying the next model
    await asyncio.sleep(2)
    return create_gemini_model()


async def extract_text_with_retry(image_or_prompt, prompt="Extract and transcribe any Persian text in this image. Return ONLY the Persian text, no explanations."):
    """Extract text using the current Gemini model with auto-rotation on rate limits."""
    attempts = 0
    model = create_gemini_model()
    max_attempts = 3 * len(MODELS)  # Try each model up to 3 times

    while attempts < max_attempts:
        try:
            # Track usage for the current model
            current_model = get_current_model()
            MODEL_USAGE[current_model]["count"] += 1
            MODEL_USAGE[current_model]["last_used"] = time.time()

            # Log model usage
            logger.info(f"Model usage: {MODEL_USAGE}")

            # Make the API call
            response = await model.generate_content([prompt, image_or_prompt])
            return response

        except Exception as e:
            error_msg = str(e)
            attempts += 1

            # Handle rate limit errors
            if "429" in error_msg or "quota" in error_msg:
                if attempts >= max_attempts:
                    logger.error(
                        f"All models exhausted after {attempts} attempts. Error: {error_msg}")
                    raise Exception(
                        "All models exhausted. Please try again later.")

                # Rotate to the next model
                model = await rotate_model_on_error()
            else:
                # For non-rate limit errors, log and re-raise
                logger.error(f"API error (not rate limit): {error_msg}")
                raise

    raise Exception(f"Failed after {attempts} attempts across all models.")

# Use this function instead of direct Gemini calls


async def gemini_extract_text(image_or_prompt):
    """Wrapper for extract_text_with_retry for OCR tasks."""
    response = await extract_text_with_retry(
        image_or_prompt,
        "Extract and transcribe any Persian text in this image. Return ONLY the Persian text, no explanations."
    )
    return response


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

        # Extract text using Gemini with retry and model rotation
        await update.message.reply_text(f"Extracting Persian text with Gemini {get_current_model()}...")
        try:
            response = await extract_text_with_retry(image)
            extracted_text = response.text
        except Exception as e:
            await update.message.reply_text(f"⚠️ API Error: {str(e)}")
            await update.message.reply_text("Try again in a few minutes or contact the administrator.")
            return

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
        if "429" in str(e):
            await update.message.reply_text("⚠️ Rate limit exceeded. Please try again later.")

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

        # Process up to the first 3 pages to avoid exceeding limits
        max_pages = min(page_count, 3)
        for page_num in range(max_pages):
            await update.message.reply_text(f"Processing page {page_num + 1}/{max_pages}...")

            # Get page and render to image
            page = pdf_document.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(
                2.0, 2.0))  # 2x zoom for better OCR

            # Convert to PIL Image
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data))

            # Add delay between pages to avoid rate limiting
            if page_num > 0:
                await update.message.reply_text("Waiting a moment to avoid rate limits...")
                await asyncio.sleep(3)  # Wait 3 seconds between pages

            # Extract text using Gemini with retry and model rotation
            try:
                response = await extract_text_with_retry(image)
                page_text = response.text.strip()
            except Exception as e:
                await update.message.reply_text(f"⚠️ API Error on page {page_num + 1}: {str(e)}")
                all_text += f"\n--- Page {page_num + 1}: Error processing - {str(e)} ---\n"
                continue

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
        if "429" in str(e):
            await update.message.reply_text("⚠️ Rate limit exceeded. Please try again later.")

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
