"""
HTTPX asynchronous client wrapper module
Provides encapsulated asynchronous HTTP request functionality
if headers is None, provide default headers with random fingerprint
"""
import httpx
import asyncio
from typing import Optional, Dict, Any, Union
from loguru import logger

from app.utils.http.rand_headers_gen import rand_fingerprint_generator


class AsyncHttpClient:
    """
    Encapsulated HTTPX asynchronous client for making HTTP requests
    Provides commonly used HTTP methods with built-in error handling and logging
    """

    def __init__(self, 
                 timeout: float = 30.0,
                 headers: Optional[Dict[str, str]] = None,
                 base_url: Optional[str] = None,
                 **kwargs):
        """
        Initialize the HTTP client
        
        Args:
            timeout: Request timeout in seconds
            headers: Default headers to send with each request
            base_url: Base URL for all requests
            **kwargs: Additional arguments passed to httpx.AsyncClient
        """
        self.timeout = timeout
        self.headers = headers or {}
        self.base_url = base_url
        self.kwargs = kwargs
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

    async def start(self):
        """Initialize the HTTP client"""
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=self.headers,
                base_url=self.base_url or "",
                **self.kwargs
            )
            logger.debug("HTTP client initialized")

    async def close(self):
        """Close the HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.debug("HTTP client closed")

    async def request(self, 
                      method: str,
                      url: str,
                      *,
                      content: Optional[Union[str, bytes]] = None,
                      data: Optional[Dict[str, Any]] = None,
                      json: Optional[Any] = None,
                      params: Optional[Dict[str, Any]] = None,
                      headers: Optional[Dict[str, str]] = None,
                      **kwargs) -> httpx.Response:
        """
        Make an HTTP request
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            url: Request URL
            content: Raw content to send
            data: Form data to send
            json: JSON data to send
            params: Query parameters
            headers: Request headers
            **kwargs: Additional arguments passed to httpx.request
            
        Returns:
            httpx.Response object
            
        Raises:
            httpx.HTTPError: For HTTP errors
            httpx.RequestError: For request errors
        """
        if not self.client:
            await self.start()

        try:
            response = await self.client.request(
                method=method,
                url=url,
                content=content,
                data=data,
                json=json,
                params=params,
                headers=headers,
                **kwargs
            )
            
            logger.debug(f"{method.upper()} {url} - Status: {response.status_code}")
            return response
            
        except httpx.RequestError as e:
            logger.error(f"Request error for {method.upper()} {url}: {str(e)}")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {method.upper()} {url}: {str(e)}")
            raise

    async def get(self, 
                  url: str,
                  *,
                  params: Optional[Dict[str, Any]] = None,
                  headers: Optional[Dict[str, str]] = None,
                  **kwargs) -> httpx.Response:
        """
        Make a GET request
        
        Args:
            url: Request URL
            params: Query parameters
            headers: Request headers
            **kwargs: Additional arguments passed to httpx.request
            
        Returns:
            httpx.Response object
        """
        return await self.request("GET", url, params=params, headers=headers, **kwargs)

    async def post(self,
                   url: str,
                   *,
                   content: Optional[Union[str, bytes]] = None,
                   data: Optional[Dict[str, Any]] = None,
                   json: Optional[Any] = None,
                   params: Optional[Dict[str, Any]] = None,
                   headers: Optional[Dict[str, str]] = None,
                   **kwargs) -> httpx.Response:
        """
        Make a POST request
        
        Args:
            url: Request URL
            content: Raw content to send
            data: Form data to send
            json: JSON data to send
            params: Query parameters
            headers: Request headers
            **kwargs: Additional arguments passed to httpx.request
            
        Returns:
            httpx.Response object
        """
        return await self.request(
            "POST", url, 
            content=content, 
            data=data, 
            json=json, 
            params=params, 
            headers=headers, 
            **kwargs
        )

    async def put(self,
                  url: str,
                  *,
                  content: Optional[Union[str, bytes]] = None,
                  data: Optional[Dict[str, Any]] = None,
                  json: Optional[Any] = None,
                  params: Optional[Dict[str, Any]] = None,
                  headers: Optional[Dict[str, str]] = None,
                  **kwargs) -> httpx.Response:
        """
        Make a PUT request
        
        Args:
            url: Request URL
            content: Raw content to send
            data: Form data to send
            json: JSON data to send
            params: Query parameters
            headers: Request headers
            **kwargs: Additional arguments passed to httpx.request
            
        Returns:
            httpx.Response object
        """
        return await self.request(
            "PUT", url,
            content=content,
            data=data,
            json=json,
            params=params,
            headers=headers,
            **kwargs
        )

    async def delete(self,
                     url: str,
                     *,
                     params: Optional[Dict[str, Any]] = None,
                     headers: Optional[Dict[str, str]] = None,
                     **kwargs) -> httpx.Response:
        """
        Make a DELETE request
        
        Args:
            url: Request URL
            params: Query parameters
            headers: Request headers
            **kwargs: Additional arguments passed to httpx.request
            
        Returns:
            httpx.Response object
        """
        return await self.request("DELETE", url, params=params, headers=headers, **kwargs)


# Global client instance for simple use cases
_default_http_client: Optional[AsyncHttpClient] = None


def get_global_http_client() -> AsyncHttpClient:
    """
    Get a global HTTP client instance
    
    Returns:
        AsyncHttpClient instance
    """
    global _default_http_client
    if _default_http_client is None:
        _default_http_client = AsyncHttpClient()
    return _default_http_client


async def request(method: str,
                  url: str,
                  *,
                  content: Optional[Union[str, bytes]] = None,
                  data: Optional[Dict[str, Any]] = None,
                  json: Optional[Any] = None,
                  params: Optional[Dict[str, Any]] = None,
                  headers: Optional[Dict[str, str]] = None,
                  timeout: Optional[float] = None,
                  **kwargs) -> httpx.Response:
    """
    Make an HTTP request using the global client
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        url: Request URL
        content: Raw content to send
        data: Form data to send
        json: JSON data to send
        params: Query parameters
        headers: Request headers
        timeout: Request timeout in seconds
        **kwargs: Additional arguments passed to httpx.request
        
    Returns:
        httpx.Response object
    """
    if headers is None:
        fp_rand = rand_fingerprint_generator.generate()
        headers = fp_rand.headers
    client = get_global_http_client()
    if timeout:
        # Create a temporary client with custom timeout
        temp_client = AsyncHttpClient(timeout=timeout)
        try:
            await temp_client.start()
            return await temp_client.request(
                method, url,
                content=content,
                data=data,
                json=json,
                params=params,
                headers=headers,
                **kwargs
            )
        finally:
            await temp_client.close()
    else:
        return await client.request(
            method, url,
            content=content,
            data=data,
            json=json,
            params=params,
            headers=headers,
            **kwargs
        )


async def get(url: str,
              *,
              params: Optional[Dict[str, Any]] = None,
              headers: Optional[Dict[str, str]] = None,
              timeout: Optional[float] = None,
              **kwargs) -> httpx.Response:
    """
    Make a GET request using the global client
    
    Args:
        url: Request URL
        params: Query parameters
        headers: Request headers
        timeout: Request timeout in seconds
        **kwargs: Additional arguments passed to httpx.request
        
    Returns:
        httpx.Response object
    """
    return await request("GET", url, params=params, headers=headers, timeout=timeout, **kwargs)


async def post(url: str,
               *,
               content: Optional[Union[str, bytes]] = None,
               data: Optional[Dict[str, Any]] = None,
               json: Optional[Any] = None,
               params: Optional[Dict[str, Any]] = None,
               headers: Optional[Dict[str, str]] = None,
               timeout: Optional[float] = None,
               **kwargs) -> httpx.Response:
    """
    Make a POST request using the global client
    
    Args:
        url: Request URL
        content: Raw content to send
        data: Form data to send
        json: JSON data to send
        params: Query parameters
        headers: Request headers
        timeout: Request timeout in seconds
        **kwargs: Additional arguments passed to httpx.request
        
    Returns:
        httpx.Response object
    """
    return await request(
        "POST", url,
        content=content,
        data=data,
        json=json,
        params=params,
        headers=headers,
        timeout=timeout,
        **kwargs
    )


async def put(url: str,
              *,
              content: Optional[Union[str, bytes]] = None,
              data: Optional[Dict[str, Any]] = None,
              json: Optional[Any] = None,
              params: Optional[Dict[str, Any]] = None,
              headers: Optional[Dict[str, str]] = None,
              timeout: Optional[float] = None,
              **kwargs) -> httpx.Response:
    """
    Make a PUT request using the global client
    
    Args:
        url: Request URL
        content: Raw content to send
        data: Form data to send
        json: JSON data to send
        params: Query parameters
        headers: Request headers
        timeout: Request timeout in seconds
        **kwargs: Additional arguments passed to httpx.request
        
    Returns:
        httpx.Response object
    """
    return await request(
        "PUT", url,
        content=content,
        data=data,
        json=json,
        params=params,
        headers=headers,
        timeout=timeout,
        **kwargs
    )


async def delete(url: str,
                 *,
                 params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: Optional[float] = None,
                 **kwargs) -> httpx.Response:
    """
    Make a DELETE request using the global client
    
    Args:
        url: Request URL
        params: Query parameters
        headers: Request headers
        timeout: Request timeout in seconds
        **kwargs: Additional arguments passed to httpx.request
        
    Returns:
        httpx.Response object
    """
    return await request("DELETE", url, params=params, headers=headers, timeout=timeout, **kwargs)


__all__ = [
    "AsyncHttpClient",
    "get_global_http_client",
    "request",
    "get",
    "post",
    "put",
    "delete"
]