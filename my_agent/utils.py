def parse_gmail(email_input: dict) -> tuple[str, str, str, str, str]:
    """Parse an email input dictionary for Gmail, including the email ID."""
    if not email_input:
        return ("", "", "", "", "")
    
    return (
        email_input.get("from", ""),
        email_input.get("to", ""),
        email_input.get("subject", ""),
        email_input.get("body_clean") or email_input.get("body", ""),
        email_input.get("id", ""),
    )
#to get better content i am using body_clean after souping the html