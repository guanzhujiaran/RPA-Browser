import jwt
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple
from fastapi import HTTPException, status
from app.config import settings

JWT_ALGORITHM = settings.jwt_algorithm
JWT_EXPIRE_MINUTES = settings.jwt_expire_minutes


def create_access_token(browser_token: uuid.UUID) -> str:
    """
    创建JWT访问令牌
    
    Args:
        browser_token: 浏览器令牌UUID
        
    Returns:
        str: JWT令牌
    """
    to_encode = {
        "browser_token": str(browser_token),
        "exp": datetime.now() + timedelta(minutes=JWT_EXPIRE_MINUTES),
        "iat": datetime.now()
    }
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def verify_access_token(token: str) -> uuid.UUID:
    """
    验证JWT访问令牌
    
    Args:
        token: JWT令牌
        
    Returns:
        uuid.UUID: 解析出的浏览器令牌
        
    Raises:
        HTTPException: 当令牌无效或过期时抛出异常
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[JWT_ALGORITHM])
        browser_token_str: str = payload.get("browser_token")
        if browser_token_str is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return uuid.UUID(browser_token_str)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid browser token format",
            headers={"WWW-Authenticate": "Bearer"},
        )


def is_token_expired(token: str) -> bool:
    """
    检查JWT令牌是否过期（非阻塞函数）
    
    Args:
        token: JWT令牌
        
    Returns:
        bool: 如果令牌过期返回True，否则返回False
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
        exp_timestamp = payload.get("exp")
        if exp_timestamp is None:
            return True
            
        exp_datetime = datetime.utcfromtimestamp(exp_timestamp)
        return exp_datetime < datetime.utcnow()
    except Exception:
        # 如果解析失败，认为令牌已过期
        return True


def decode_token_without_verification(token: str) -> dict:
    """
    不验证签名和过期时间的情况下解码JWT令牌
    
    Args:
        token: JWT令牌
        
    Returns:
        dict: 解码后的载荷
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[JWT_ALGORITHM], options={
            "verify_signature": False,
            "verify_exp": False
        })
        return payload
    except Exception:
        return {}