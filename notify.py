"""Send milestone e-mails via the scrib-r SMTP configuration.

Reads SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/MAIL_FROM from the scrib-r
config.env (same account the scrib-r service mails with) and sends plain-text
mail. Usage:  python3 notify.py "subject" "body"
"""
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

SCRIB_R_ENV = Path.home() / "git" / "scrib-r" / "config.env"
TO_ADDR = "sayf@multicode.nl"


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def send_mail(subject: str, body: str, to_addr: str = TO_ADDR) -> None:
    env = load_env(SCRIB_R_ENV)
    host, user = env["SMTP_HOST"], env["SMTP_USER"]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = env.get("MAIL_FROM", user)
    msg["To"] = to_addr
    with smtplib.SMTP_SSL(host, int(env.get("SMTP_PORT", "465"))) as smtp:
        smtp.login(user, env["SMTP_PASSWORD"])
        smtp.send_message(msg)
    print(f"mail sent to {to_addr}: {subject}")


if __name__ == "__main__":
    send_mail(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
