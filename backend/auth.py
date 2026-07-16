"""簡單管理員驗證：密碼 + 簽章 token。"""
import hashlib
import hmac
import os
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
TOKEN_TTL_SECONDS = 60 * 60 * 12  # token 有效 12 小時

security = HTTPBearer(auto_error=False)


# 用密鑰對字串做簽章，產生不可偽造的驗證碼
def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


# 登入成功後建立 token（內容：角色 + 過期時間 + 簽章）
def create_admin_token() -> str:
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"admin:{expires}"
    return f"{payload}:{_sign(payload)}"


# 檢查 token 簽章是否正確，以及是否仍在有效期限內
def verify_admin_token(token: str) -> bool:
    try:
        role, expires_str, signature = token.split(":")
        payload = f"{role}:{expires_str}"
        if not hmac.compare_digest(signature, _sign(payload)):
            return False
        return role == "admin" and int(expires_str) >= int(time.time())
    except Exception:
        return False


# 比對登入密碼是否與 .env 的 ADMIN_PASSWORD 相同
def check_admin_password(password: str) -> bool:
    return hmac.compare_digest(password, ADMIN_PASSWORD)


# FastAPI 依賴：沒帶有效 token 就回 401，擋住非管理員請求
def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    if credentials is None or not verify_admin_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要管理員登入",
        )
    return credentials.credentials
