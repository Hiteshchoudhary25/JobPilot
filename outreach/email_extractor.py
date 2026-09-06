import re
from typing import List, Set

IGNORED_DOMAINS = {
    'example.com', 'test.com', 'sample.com', 'indeed.com', 'linkedin.com',
    'naukri.com', 'glassdoor.com', 'w3.org', 'schema.org', 'sentry.io',
    'github.com', 'google.com', 'microsoft.com', 'apple.com'
}

IGNORED_PREFIXES = {
    # Generic system addresses
    'noreply', 'no-reply', 'donotreply', 'privacy', 'support', 'help',
    'abuse', 'admin', 'postmaster', 'hostmaster', 'webmaster', 'security',
    'legal', 'billing', 'feedback', 'sales',
    # ADA / accommodation inboxes — NOT recruiters
    'accessibility', 'accommodations', 'accommodation', 'disability',
    'disability_accommodation', 'ada', 'eeo', 'diversity',
    # Other non-recruiter addresses
    'unsubscribe', 'info', 'contact', 'careers-do-not-reply',
    'press', 'media', 'marketing', 'events'
}

# Words anywhere in the local-part that indicate non-recruiter inboxes
BLOCKED_WORDS_IN_LOCAL = [
    'reportfraud', 'fraud', 'report', 'abuse', 'spam',
    'phishing', 'alert', 'noreply', 'donotreply', 'bounce'
]

class EmailExtractor:
    EMAIL_REGEX = re.compile(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        re.IGNORECASE
    )

    @classmethod
    def extract_emails(cls, text: str) -> List[str]:
        if not text:
            return []
        
        matches = cls.EMAIL_REGEX.findall(text)
        valid_emails: Set[str] = set()

        for email in matches:
            # Strip ALL leading non-alphanumeric chars (fixes "-akshata@..." bug)
            email_clean = re.sub(r'^[^a-zA-Z0-9]+', '', email.strip()).lower()
            if '@' not in email_clean:
                continue
            
            user_part, domain_part = email_clean.split('@', 1)
            
            if not user_part or len(user_part) < 2:
                continue

            # Domain blocklist
            if any(domain_part == d or domain_part.endswith('.' + d) for d in IGNORED_DOMAINS):
                continue
            
            # Prefix blocklist (startswith)
            if any(user_part.startswith(prefix) for prefix in IGNORED_PREFIXES):
                continue
            
            # Word blocklist (anywhere in local part)
            if any(w in user_part for w in BLOCKED_WORDS_IN_LOCAL):
                continue
            
            email_clean = email_clean.rstrip('.,;:)')
            valid_emails.add(email_clean)

        return sorted(list(valid_emails))
