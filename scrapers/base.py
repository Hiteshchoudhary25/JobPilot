from abc import ABC, abstractmethod
from typing import List, Dict, Any
from core.models import JobPosting
from core.db import Database
from core.llm import LLMAssistant
from outreach.email_extractor import EmailExtractor

class BaseScraper(ABC):
    def __init__(self, config: Dict[str, Any], db: Database, llm: LLMAssistant):
        self.config = config
        self.db = db
        self.llm = llm
        self.email_extractor = EmailExtractor()

    @abstractmethod
    def search_jobs(self, criteria: Dict[str, Any], limit: int = 15) -> List[JobPosting]:
        pass

    @abstractmethod
    def apply_job(self, job: JobPosting, user_profile: Dict[str, Any]) -> bool:
        pass