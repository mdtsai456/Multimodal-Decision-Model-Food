"""簡單管理員驗證：密碼 + 簽章 token。"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret") # 用這個 key 對 過期的admin做 HMAC-SHA256 簽章
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
TOKEN_TTL_SECONDS = 60 * 60 * 12  # token 有效 12 小時

security = HTTPBearer(auto_error=False) # 有帶通行證 → 拿出來給後面檢查 ; 沒帶 → 不直接報錯，再回「需要管理員登入」


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
        role, expires_str, signature = token.split(":")  # token ： admin:過期時間:簽章 
        payload = f"{role}:{expires_str}" # 把前兩段拼回「當初拿去簽名的那串字」

        # 用 SECRET_KEY 重算一次簽章，跟 token 裡的簽章比
        # 不一樣 = 假造或被改過 → 直接 False
        if not hmac.compare_digest(signature, _sign(payload)):
            return False
        # 必須是 admin，而且還沒過期（過期時間 ≥ 現在）
        return role == "admin" and int(expires_str) >= int(time.time())
    except Exception:
        return False


# 比對登入密碼是否與 .env 的 ADMIN_PASSWORD 相同
def check_admin_password(password: str) -> bool:
    
    return hmac.compare_digest(
        password.strip(),
        ADMIN_PASSWORD.strip()
    )
    


# FastAPI 依賴：沒帶有效 token 就回 401，擋住非管理員請求
def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    if credentials is None or not verify_admin_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要管理員登入",
        )
    return credentials.credentials
