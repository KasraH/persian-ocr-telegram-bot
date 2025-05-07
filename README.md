# Persian OCR Telegram Bot

A Telegram bot that extracts Persian text from images and PDFs using Google's Gemini 2.0 models.

## Features

- 📷 Extract Persian text from images
- 📄 Extract Persian text from PDF documents (up to 5 pages)
- 📧 Email extracted text directly from the bot
- 🔄 Smart model rotation to handle API rate limits
- 🔒 User authorization system to restrict access

## Technologies

- Python 3.9+
- python-telegram-bot library
- Google Gemini 2.0 AI models (flash and flash-lite)
- Docker for containerization

## Setup

### Prerequisites

- Python 3.9 or higher
- Docker (optional, for containerized deployment)
- A Telegram Bot Token (from BotFather)
- Google Gemini API key

### Environment Variables

Create a `.env` file with the following variables:

```
TELEGRAM_TOKEN=your_telegram_bot_token
GOOGLE_API_KEY=your_google_gemini_api_key
AUTHORIZED_USERS=comma_separated_user_ids
EMAIL_ADDRESS=your_sender_email
EMAIL_PASSWORD=your_email_app_password
USER_EMAIL=recipient_email
```

### Local Development

1. Clone the repository:

   ```bash
   git clone https://github.com/yourusername/persian-ocr-telegram-bot.git
   cd persian-ocr-telegram-bot
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the bot:
   ```bash
   python bot.py
   ```

### Docker Deployment

1. Build and run with Docker Compose:
   ```bash
   docker-compose up -d
   ```

## How It Works

1. User sends an image or PDF to the bot
2. Bot processes the content using Google Gemini 2.0 models
3. Extracted Persian text is returned to the user
4. User can choose to email the result
