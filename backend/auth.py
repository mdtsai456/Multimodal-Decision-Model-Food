"""管理員密碼與 token 簽章。"""
from typing import Optional
import hashlib
import hmac
import os
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
TOKEN_TTL_SECONDS = 60 * 60 * 12  # token 在 12 小時後失效。

# 缺少 token 時不在此處回 403。require_admin 回 401。
security = HTTPBearer(auto_error=False)


def _sign(payload: str) -> str:
    """以 SECRET_KEY 對 payload 做 HMAC-SHA256。"""
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_admin_token() -> str:
    """token 格式為 admin:到期時間:簽章。"""
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"admin:{expires}"
    return f"{payload}:{_sign(payload)}"


def verify_admin_token(token: str) -> bool:
    """簽章必須相符。角色必須是 admin。token 必須未過期。"""
    try:
        role, expires_str, signature = token.split(":")
        payload = f"{role}:{expires_str}"
        if not hmac.compare_digest(signature, _sign(payload)):
            return False
        return role == "admin" and int(expires_str) >= int(time.time())
    except Exception:
        return False


def check_admin_password(password: str) -> bool:
    """密碼必須與 ADMIN_PASSWORD 相同。"""
    
    return hmac.compare_digest(
        password.strip(),
        ADMIN_PASSWORD.strip()
    )
    


def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """沒有有效 token 時回 401。"""
    if credentials is None or not verify_admin_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要管理員登入",
        )
    return credentials.credentials
