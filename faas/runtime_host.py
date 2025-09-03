#!/usr/bin/env python3
"""
Runtime Host - Core execution wrapper for FaaS Platform

This module provides the generic runtime wrapper that executes user code in both
container-based and process-based execution modes. It starts an HTTP server and
dynamically loads user functions from specified paths.
"""

import os
import sys
import json
import logging
import importlib.util
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Dict, Optional, Callable
import threading
import signal
import time
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FunctionExecutionError(Exception):
    """Custom exception for function execution errors"""
    pass


class UserFunctionLoader:
    """Handles dynamic loading of user functions"""
    
    def __init__(self, function_path: str, function_name: str = "handle"):
        self.function_path = function_path
        self.function_name = function_name
        self._loaded_function: Optional[Callable] = None
        self._last_modified: Optional[float] = None
        
    def load_function(self) -> Callable:
        """Load or reload user function if file has changed"""
        try:
            current_modified = os.path.getmtime(self.function_path)
            
            # Check if we need to reload
            if (self._loaded_function is None or 
                self._last_modified is None or 
                current_modified > self._last_modified):
                
                logger.info(f"Loading function from {self.function_path}")
                
                # Create module spec and load
                spec = importlib.util.spec_from_file_location("user_function", self.function_path)
                if spec is None or spec.loader is None:
                    raise FunctionExecutionError(f"Could not create module spec for {self.function_path}")
                
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # Get the function
                if not hasattr(module, self.function_name):
                    raise FunctionExecutionError(f"Function '{self.function_name}' not found in {self.function_path}")
                
                self._loaded_function = getattr(module, self.function_name)
                self._last_modified = current_modified
                
                logger.info(f"Successfully loaded function '{self.function_name}'")
            
            return self._loaded_function
        
        except Exception as e:
            logger.error(f"Failed to load function: {e}")
            raise FunctionExecutionError(f"Function loading error: {e}")


class RuntimeRequest:
    """Wrapper for HTTP request to provide consistent interface to user functions"""
    
    def __init__(self, method: str, path: str, headers: Dict[str, str], 
                 body: bytes, query_params: Dict[str, str]):
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body
        self.query_params = query_params
        
    def get_json(self) -> Dict[str, Any]:
        """Parse request body as JSON"""
        try:
            return json.loads(self.body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Invalid JSON in request body: {e}")
    
    def get_text(self) -> str:
        """Get request body as text"""
        return self.body.decode('utf-8')
    
    def get_header(self, name: str, default: str = None) -> Optional[str]:
        """Get header value (case-insensitive)"""
        return self.headers.get(name.lower(), default)


class FunctionRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for function execution"""
    
    def __init__(self, request, client_address, server):
        self.function_loader: UserFunctionLoader = server.function_loader
        self.timeout: int = server.timeout
        super().__init__(request, client_address, server)
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.client_address[0]} - {format % args}")
    
    def do_POST(self):
        """Handle POST requests - main function execution endpoint"""
        try:
            # Parse request
            content_length = int(self.headers.get('content-length', 0))
            body = self.rfile.read(content_length)
            
            # Parse query parameters
            query_params = {}
            if '?' in self.path:
                _, query_string = self.path.split('?', 1)
                for param in query_string.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        query_params[key] = value
            
            # Create request object
            request = RuntimeRequest(
                method=self.command,
                path=self.path.split('?')[0],
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
                query_params=query_params
            )
            
            # Execute function with timeout
            result = self._execute_with_timeout(request)
            
            # Send response
            self._send_response(200, result)
            
        except FunctionExecutionError as e:
            logger.error(f"Function execution error: {e}")
            self._send_error_response(500, str(e))
        
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            logger.error(traceback.format_exc())
            self._send_error_response(500, f"Internal server error: {e}")
    
    def do_GET(self):
        """Handle GET requests - health check and status"""
        if self.path == '/health' or self.path == '/':
            response = {
                'status': 'healthy',
                'runtime': 'python',
                'version': '1.0.0',
                'timestamp': time.time()
            }
            self._send_response(200, response)
        else:
            self._send_error_response(404, "Not Found")
    
    def _execute_with_timeout(self, request: RuntimeRequest) -> Any:
        """Execute user function with timeout protection"""
        result = [None]  # Use list for mutable reference
        exception = [None]
        
        def target():
            try:
                user_function = self.function_loader.load_function()
                result[0] = user_function(request)
            except Exception as e:
                exception[0] = e
        
        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(timeout=self.timeout)
        
        if thread.is_alive():
            # Timeout occurred
            raise FunctionExecutionError(f"Function execution timeout ({self.timeout}s)")
        
        if exception[0]:
            raise FunctionExecutionError(f"User function error: {exception[0]}")
        
        return result[0]
    
    def _send_response(self, status_code: int, data: Any):
        """Send successful response"""
        if isinstance(data, (dict, list)):
            response_body = json.dumps(data).encode('utf-8')
            content_type = 'application/json'
        elif isinstance(data, str):
            response_body = data.encode('utf-8')
            content_type = 'text/plain'
        elif isinstance(data, bytes):
            response_body = data
            content_type = 'application/octet-stream'
        else:
            response_body = str(data).encode('utf-8')
            content_type = 'text/plain'
        
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)
    
    def _send_error_response(self, status_code: int, message: str):
        """Send error response"""
        error_response = {
            'error': message,
            'status': status_code,
            'timestamp': time.time()
        }
        
        response_body = json.dumps(error_response).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Thread-per-request HTTP server"""
    allow_reuse_address = True
    daemon_threads = True
    
    def __init__(self, server_address, RequestHandlerClass, function_loader, timeout=30):
        self.function_loader = function_loader
        self.timeout = timeout
        super().__init__(server_address, RequestHandlerClass)


class RuntimeHost:
    """Main runtime host class"""
    
    def __init__(self):
        self.server: Optional[ThreadedHTTPServer] = None
        self.running = False
        
        # Configuration from environment variables
        self.port = int(os.getenv('RUNTIME_PORT', '8080'))
        self.host = os.getenv('RUNTIME_HOST', '0.0.0.0')
        self.function_path = os.getenv('FUNCTION_PATH', '/tmp/user_function.py')
        self.function_name = os.getenv('FUNCTION_NAME', 'handle')
        self.timeout = int(os.getenv('FUNCTION_TIMEOUT', '30'))
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        
        # Execution mode info
        self.execution_mode = os.getenv('EXECUTION_MODE', 'unknown')
        self.runtime_id = os.getenv('RUNTIME_ID', 'unknown')
        
        # Set log level
        logging.getLogger().setLevel(getattr(logging, self.log_level.upper()))
        
        logger.info(f"Runtime Host initialized:")
        logger.info(f"  - Port: {self.port}")
        logger.info(f"  - Host: {self.host}")
        logger.info(f"  - Function Path: {self.function_path}")
        logger.info(f"  - Function Name: {self.function_name}")
        logger.info(f"  - Timeout: {self.timeout}s")
        logger.info(f"  - Execution Mode: {self.execution_mode}")
        logger.info(f"  - Runtime ID: {self.runtime_id}")
    
    def validate_function_path(self):
        """Validate that function path exists and is readable"""
        if not os.path.exists(self.function_path):
            raise RuntimeError(f"Function file not found: {self.function_path}")
        
        if not os.path.isfile(self.function_path):
            raise RuntimeError(f"Function path is not a file: {self.function_path}")
        
        if not os.access(self.function_path, os.R_OK):
            raise RuntimeError(f"Function file is not readable: {self.function_path}")
    
    def setup_signal_handlers(self):
        """Setup graceful shutdown signal handlers"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            self.stop()
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    def start(self):
        """Start the runtime host server"""
        try:
            # Validate function path
            self.validate_function_path()
            
            # Create function loader
            function_loader = UserFunctionLoader(self.function_path, self.function_name)
            
            # Test load the function to catch errors early
            try:
                function_loader.load_function()
                logger.info("Function validation successful")
            except Exception as e:
                logger.error(f"Function validation failed: {e}")
                raise
            
            # Setup signal handlers
            self.setup_signal_handlers()
            
            # Create and start server
            self.server = ThreadedHTTPServer(
                (self.host, self.port), 
                FunctionRequestHandler,
                function_loader,
                self.timeout
            )
            
            self.running = True
            logger.info(f"Runtime Host started on {self.host}:{self.port}")
            
            # Server main loop
            self.server.serve_forever()
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Runtime Host error: {e}")
            raise
        finally:
            self.stop()
    
    def stop(self):
        """Stop the runtime host server"""
        if self.running and self.server:
            logger.info("Stopping Runtime Host...")
            self.server.shutdown()
            self.server.server_close()
            self.running = False
            logger.info("Runtime Host stopped")


def main():
    """Main entry point"""
    try:
        runtime_host = RuntimeHost()
        runtime_host.start()
    except Exception as e:
        logger.error(f"Failed to start Runtime Host: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main() 