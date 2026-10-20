"""本地问题库公开入口。"""

from .service import IssueStore, IssueStoreError

__all__ = ["IssueStore", "IssueStoreError"]
