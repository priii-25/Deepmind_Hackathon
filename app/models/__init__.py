"""
All database models. Imported here so Base.metadata sees them for create_all().
"""

from .base import TenantBase
from .user import User, UserPreferences
from .conversation import Conversation, Message
from .document import Document
from .agent import AgentCatalog, AgentAssignment
from .onboarding import OnboardingState, UserIntegration, BrandRecord
from .agent_session import AgentSession
from .meeting import Call, CalendarEvent
from .ugc import UGCConversation, UGCAsset, Avatar
from .fashion import FashionSession, FashionImage, Apparel
from .social_media import SocialToken, SocialPost
from .presentation import Presentation

__all__ = [
    "TenantBase",
    "User", "UserPreferences",
    "Conversation", "Message",
    "Document",
    "AgentCatalog", "AgentAssignment",
    "OnboardingState", "UserIntegration", "BrandRecord",
    "AgentSession",
    "Call", "CalendarEvent",
    "UGCConversation", "UGCAsset", "Avatar",
    "FashionSession", "FashionImage", "Apparel",
    "SocialToken", "SocialPost",
    "Presentation",
]
