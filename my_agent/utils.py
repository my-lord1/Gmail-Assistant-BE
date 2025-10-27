def parse_gmail(email_input: dict) -> tuple[str, str, str, str, str]:
    """Parse an email input dictionary for Gmail, including the email ID.
    This function extends parse_email by also returning the email ID,
    which is used specifically in the Gmail integration.
    Args:
        email_input (dict): Dictionary containing email fields in any of these formats:
            Gmail schema:
                - From: Sender's email
                - To: Recipient's email
                - Subject: Email subject line
                - Body: Full email content
                - Id: Gmail message ID
    Returns:
        tuple[str, str, str, str, str]: Tuple containing:
            - author: Sender's name and email
            - to: Recipient's name and email
            - subject: Email subject line
            - email_thread: Full email content
            - email_id: Email ID (or None if not available)
    """

    print("!Email_input from Gmail!")
    print(email_input)

    # Gmail schema
    return (
        email_input["from"],
        email_input["to"],
        email_input["subject"],
        email_input["body"], 
        email_input["id"],
    )
#to get better content i am using body_clean after souping the html