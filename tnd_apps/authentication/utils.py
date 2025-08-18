from django.core.mail import EmailMessage
from django.conf import settings
import requests
import logging

logger = logging.getLogger(__name__)

class Util:
    @staticmethod
    def send_email(data):
        try:
            # Plunk API endpoint
            url = "https://api.useplunk.com/v1/send"
            plunk_api_key = getattr(settings, 'EMAIL_PLUNK_API_KEY')

            # Headers for Plunk API
            headers = {
                "Authorization": f"Bearer {plunk_api_key}",
                "Content-Type": "application/json"
            }

            # Payload for Plunk API
            payload = {
                "to": data['to_email'],
                "subject": data['email_subject'],
                "body": data['email_body'],
                "type": "html"  # or "html" if you want to send HTML emails
            }

            # Send the email via Plunk API
            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                logger.info(f"Email sent successfully to {data['to_email']}")
                return True
            else:
                logger.error(f"Failed to send email. Status: {response.status_code}, Response: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            return False