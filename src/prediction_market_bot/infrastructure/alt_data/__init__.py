from .adapters import (
    GoogleNewsRssAdapter,
    NewsSourceFetchError,
    RedditOAuthAdapter,
    RedditSourceFetchError,
    XApiAdapter,
    XSourceFetchError,
)
from .base import (
    AltDataAdapterError,
    AltDataCapabilityError,
    AltDataCredentialError,
    ConfiguredAltDataSourceAdapter,
)
from .capabilities import SourceCapabilities, SourceOperation
from .models import AltDataSourceRegistration
from .news_models import NewsArticleRecord, NewsFetchBatch, NewsQuery, NewsQueryKind
from .reddit_models import (
    RedditEvidenceKind,
    RedditEvidenceRecord,
    RedditFetchPage,
    RedditQuery,
    RedditQueryKind,
)
from .x_models import XFetchPage, XPostRecord, XQuery, XQueryKind
from .registry import AltDataAdapterRegistry, CapabilityValidationIssue, build_credential_resolver_from_mapping

__all__ = [
    "GoogleNewsRssAdapter",
    "NewsSourceFetchError",
    "RedditOAuthAdapter",
    "RedditSourceFetchError",
    "XApiAdapter",
    "XSourceFetchError",
    "AltDataAdapterError",
    "AltDataCapabilityError",
    "AltDataCredentialError",
    "ConfiguredAltDataSourceAdapter",
    "SourceCapabilities",
    "SourceOperation",
    "AltDataSourceRegistration",
    "AltDataAdapterRegistry",
    "CapabilityValidationIssue",
    "build_credential_resolver_from_mapping",
    "NewsArticleRecord",
    "NewsFetchBatch",
    "NewsQuery",
    "NewsQueryKind",
    "RedditEvidenceKind",
    "RedditEvidenceRecord",
    "RedditFetchPage",
    "RedditQuery",
    "RedditQueryKind",
    "XFetchPage",
    "XPostRecord",
    "XQuery",
    "XQueryKind",
]
