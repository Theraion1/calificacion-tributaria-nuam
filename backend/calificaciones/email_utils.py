from threading import Thread
import logging
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger(__name__)

def send_email_async(subject, message, recipient_list, html_template=None, context=None):
    html_message = None
    if html_template and context is not None:
        try:
            html_message = render_to_string(html_template, context)
        except Exception:
            logger.exception("Error renderizando plantilla de email: %s", html_template)

    def _send():
        try:
            send_mail(
                subject,
                message,
                getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
                recipient_list,
                fail_silently=False,
                html_message=html_message,
            )
            logger.info("Email enviado a %s (subject=%s)", recipient_list, subject)
        except Exception:
            logger.exception("Error enviando email a %s (subject=%s)", recipient_list, subject)

    Thread(target=_send, daemon=True).start()
