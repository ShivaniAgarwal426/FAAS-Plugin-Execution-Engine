"""
Example Function for FaaS Platform

This demonstrates how to write functions for the FaaS platform.
The function must have a 'handle(request)' signature.
"""

import json
import time
from typing import Dict, Any


def handle(request) -> Dict[str, Any]:
    """
    Main function handler - this is the entry point for the FaaS platform
    
    Args:
        request: RuntimeRequest object with the following attributes:
            - method: HTTP method (GET, POST, etc.)
            - path: Request path
            - headers: Dict of HTTP headers
            - body: Request body as bytes
            - query_params: Dict of query parameters
            - get_json(): Parse body as JSON
            - get_text(): Get body as text
            - get_header(name): Get specific header value
    
    Returns:
        Dict containing the response data (will be JSON serialized)
    """
    
    try:
        # Get request information
        method = request.method
        path = request.path
        headers = request.headers
        
        # Parse request body based on content type
        content_type = request.get_header('content-type', '')
        
        if 'application/json' in content_type:
            try:
                data = request.get_json()
            except ValueError as e:
                return {
                    "error": f"Invalid JSON: {e}",
                    "status": "error"
                }
        else:
            data = request.get_text()
        
        # Process the request
        response = {
            "message": "Hello from FaaS!",
            "timestamp": time.time(),
            "request_info": {
                "method": method,
                "path": path,
                "content_type": content_type,
                "user_agent": request.get_header('user-agent', 'unknown')
            },
            "received_data": data,
            "status": "success"
        }
        
        # Add some processing logic
        if isinstance(data, dict):
            if 'name' in data:
                response["greeting"] = f"Hello, {data['name']}!"
            
            if 'compute' in data:
                # Simulate some computation
                start_time = time.time()
                result = sum(i * i for i in range(1000))
                end_time = time.time()
                
                response["computation"] = {
                    "result": result,
                    "execution_time_ms": (end_time - start_time) * 1000
                }
        
        return response
    
    except Exception as e:
        # Error handling
        return {
            "error": str(e),
            "status": "error",
            "timestamp": time.time()
        }


# Additional example functions to demonstrate different use cases

def handle_echo(request) -> Dict[str, Any]:
    """Simple echo function"""
    return {
        "echo": request.get_text(),
        "method": request.method,
        "timestamp": time.time()
    }


def handle_math(request) -> Dict[str, Any]:
    """Math computation function"""
    try:
        data = request.get_json()
        
        if 'operation' not in data:
            return {"error": "Missing 'operation' field"}
        
        operation = data['operation']
        a = data.get('a', 0)
        b = data.get('b', 0)
        
        if operation == 'add':
            result = a + b
        elif operation == 'subtract':
            result = a - b
        elif operation == 'multiply':
            result = a * b
        elif operation == 'divide':
            if b == 0:
                return {"error": "Division by zero"}
            result = a / b
        else:
            return {"error": f"Unknown operation: {operation}"}
        
        return {
            "operation": operation,
            "inputs": {"a": a, "b": b},
            "result": result,
            "timestamp": time.time()
        }
    
    except Exception as e:
        return {"error": str(e)}


def handle_async_task(request) -> Dict[str, Any]:
    """Simulate an async task"""
    import time
    import random
    
    try:
        data = request.get_json()
        duration = data.get('duration', 1)  # Default 1 second
        
        # Simulate work
        time.sleep(min(duration, 5))  # Cap at 5 seconds
        
        return {
            "task": "completed",
            "duration": duration,
            "random_value": random.randint(1, 100),
            "timestamp": time.time()
        }
    
    except Exception as e:
        return {"error": str(e)} 