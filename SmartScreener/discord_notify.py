import requests
import os

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Store securely in environment variables

def send_discord_message(message):
    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]  # Leave buffer for formatting
    for chunk in chunks:
        payload = {"content": chunk}
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
            if response.status_code == 204:
                print("[INFO] Discord chunk sent successfully.")
            else:
                print(f"[ERROR] Failed to send chunk: {response.text}")
        except Exception as e:
            print(f"[ERROR] Discord chunk send failed: {e}")

