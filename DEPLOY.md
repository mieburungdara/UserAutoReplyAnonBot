# Deployment Instructions

## Prerequisites
- Shared hosting with Python support (3.7+)
- SSH access to terminal
- Telegram API credentials from my.telegram.org
- Bot username (@chatbot)

## Steps

1. **Local Setup for Session String**
   - Edit config.json with your api_id, api_hash, phone.
   - Run `python main.py` locally.
   - Follow the login prompts to authenticate.
   - The session_string will be saved to config.json.

2. **Upload to Hosting**
   - Upload requirements.txt, config.json, main.py to your hosting directory.
   - Ensure config.json has the generated session_string.

3. **Install Dependencies**
   - In terminal: `python -m pip install --user -r requirements.txt` (or use hosting's package manager if pip not available).

4. **Run the Userbot**
   - `python main.py` for testing.
   - For continuous running: `nohup python main.py &`

## Notes
- Shared hosting may kill long-running processes; use nohup/screen if available.
- Monitor logs for errors.
- For security, protect config.json.