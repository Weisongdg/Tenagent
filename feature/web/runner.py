"""供 app.py 调用的本地 HTTP 服务入口。"""

import uvicorn

from .api import create_app


def run() -> int:
    uvicorn.run(create_app(), host="127.0.0.1", port=8501)
    return 0
