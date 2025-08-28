"""
API Gateway for FaaS Platform

Provides REST API endpoints for function invocation, management, and monitoring.
Handles authentication, rate limiting, and request routing.
"""

import os
import json
import time
import logging
from typing import Dict, Any, Optional
from flask import Flask, request, jsonify, Response
from werkzeug.exceptions import BadRequest
import threading
from functools import wraps

from orchestrator import FaaSOrchestrator
from config import ConfigManager, FunctionConfig

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter implementation"""
    
    def __init__(self):
        self.clients: Dict[str, Dict[str, Any]] = {}
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
    
    def is_allowed(self, client_id: str, max_requests: int = 100, window_seconds: int = 60) -> bool:
        """Check if client is within rate limits"""
        current_time = time.time()
        
        if client_id not in self.clients:
            self.clients[client_id] = {
                'requests': [current_time],
                'window_start': current_time
            }
            return True
        
        client_data = self.clients[client_id]
        
        # Reset window if needed
        if current_time - client_data['window_start'] >= window_seconds:
            client_data['requests'] = [current_time]
            client_data['window_start'] = current_time
            return True
        
        # Remove old requests
        cutoff_time = current_time - window_seconds
        client_data['requests'] = [req_time for req_time in client_data['requests'] 
                                  if req_time >= cutoff_time]
        
        # Check limit
        if len(client_data['requests']) >= max_requests:
            return False
        
        # Add current request
        client_data['requests'].append(current_time)
        return True
    
    def _cleanup_loop(self):
        """Cleanup old client data"""
        while True:
            try:
                current_time = time.time()
                expired_clients = []
                
                for client_id, client_data in self.clients.items():
                    if current_time - client_data['window_start'] > 300:  # 5 minutes
                        expired_clients.append(client_id)
                
                for client_id in expired_clients:
                    del self.clients[client_id]
                
                time.sleep(60)  # Cleanup every minute
            except Exception as e:
                logger.error(f"Error in rate limiter cleanup: {e}")
                time.sleep(60)


class AuthManager:
    """Simple authentication manager"""
    
    def __init__(self):
        # In production, this would use a proper auth system
        self.api_keys = {
            "demo-key": {"name": "demo", "permissions": ["invoke", "manage"]},
            "admin-key": {"name": "admin", "permissions": ["invoke", "manage", "admin"]}
        }
    
    def authenticate(self, api_key: str) -> Optional[Dict[str, Any]]:
        """Authenticate API key"""
        return self.api_keys.get(api_key)
    
    def has_permission(self, user_info: Dict[str, Any], permission: str) -> bool:
        """Check if user has permission"""
        return permission in user_info.get("permissions", [])


def rate_limit(max_requests: int = 100, window_seconds: int = 60):
    """Rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            client_ip = request.remote_addr
            if not rate_limiter.is_allowed(client_ip, max_requests, window_seconds):
                return jsonify({"error": "Rate limit exceeded"}), 429
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_auth(permission: str = "invoke"):
    """Authentication decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                return jsonify({"error": "Missing or invalid authorization header"}), 401
            
            api_key = auth_header[7:]  # Remove 'Bearer ' prefix
            user_info = auth_manager.authenticate(api_key)
            
            if not user_info:
                return jsonify({"error": "Invalid API key"}), 401
            
            if not auth_manager.has_permission(user_info, permission):
                return jsonify({"error": "Insufficient permissions"}), 403
            
            # Add user info to request context
            request.user_info = user_info
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Initialize global components
rate_limiter = RateLimiter()
auth_manager = AuthManager()


def create_app(config_file: str = None) -> Flask:
    """Create Flask application"""
    app = Flask(__name__)
    app.config['JSON_SORT_KEYS'] = False
    
    # Initialize orchestrator
    orchestrator = FaaSOrchestrator(config_file)
    orchestrator.start()
    app.orchestrator = orchestrator
    
    @app.teardown_appcontext
    def cleanup(error):
        """Cleanup on app teardown"""
        if hasattr(app, 'orchestrator'):
            app.orchestrator.stop()
    
    # Health check endpoint
    @app.route('/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({
            "status": "healthy",
            "timestamp": time.time(),
            "version": "1.0.0"
        })
    
    # Function invocation endpoint
    @app.route('/invoke/<function_name>', methods=['POST'])
    @rate_limit(max_requests=1000, window_seconds=60)
    @require_auth("invoke")
    def invoke_function(function_name: str):
        """Invoke a function"""
        try:
            # Get request data
            if request.is_json:
                request_data = request.get_json()
            else:
                request_data = {"body": request.get_data().decode('utf-8')}
            
            # Add request metadata
            request_data.update({
                "method": request.method,
                "path": request.path,
                "headers": dict(request.headers),
                "query_params": dict(request.args)
            })
            
            # Invoke function
            status_code, response_data = app.orchestrator.invoke_function(
                function_name, request_data, dict(request.headers)
            )
            
            return jsonify(response_data), status_code
        
        except Exception as e:
            logger.error(f"Error invoking function {function_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Function management endpoints
    @app.route('/functions', methods=['GET'])
    @require_auth("manage")
    def list_functions():
        """List all functions"""
        try:
            functions = []
            for func_name in app.orchestrator.registry.list_functions():
                func_info = app.orchestrator.get_function_info(func_name)
                functions.append(func_info)
            
            return jsonify({"functions": functions})
        
        except Exception as e:
            logger.error(f"Error listing functions: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route('/functions/<function_name>', methods=['GET'])
    @require_auth("manage")
    def get_function(function_name: str):
        """Get function details"""
        try:
            func_info = app.orchestrator.get_function_info(function_name)
            if "error" in func_info:
                return jsonify(func_info), 404
            
            return jsonify(func_info)
        
        except Exception as e:
            logger.error(f"Error getting function {function_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route('/functions', methods=['POST'])
    @require_auth("manage")
    def create_function():
        """Create a new function"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"error": "Missing request body"}), 400
            
            # Validate required fields
            required_fields = ['name', 'code']
            for field in required_fields:
                if field not in data:
                    return jsonify({"error": f"Missing required field: {field}"}), 400
            
            # Create function config
            config_data = data.get('config', {})
            function_config = FunctionConfig(
                name=data['name'],
                runtime=config_data.get('runtime', 'python3.11'),
                execution_mode=config_data.get('execution_mode', 'process'),
                handler=config_data.get('handler', 'handle'),
                timeout=config_data.get('timeout', 30),
                memory=config_data.get('memory', '256Mi'),
                cpu_limit=config_data.get('cpu_limit', '100m'),
                dependencies=config_data.get('dependencies', []),
                environment=config_data.get('environment', {}),
                min_instances=config_data.get('min_instances', 0),
                max_instances=config_data.get('max_instances', 10)
            )
            
            # Register function
            app.orchestrator.registry.register_function(
                data['name'], function_config, data['code']
            )
            
            return jsonify({"message": f"Function {data['name']} created successfully"}), 201
        
        except Exception as e:
            logger.error(f"Error creating function: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route('/functions/<function_name>', methods=['PUT'])
    @require_auth("manage")
    def update_function(function_name: str):
        """Update an existing function"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"error": "Missing request body"}), 400
            
            # Check if function exists
            existing_config = app.orchestrator.registry.get_function(function_name)
            if not existing_config:
                return jsonify({"error": "Function not found"}), 404
            
            # Update function code if provided
            if 'code' in data:
                app.orchestrator.registry.function_code[function_name] = data['code']
            
            # Update configuration if provided
            if 'config' in data:
                config_data = data['config']
                for key, value in config_data.items():
                    if hasattr(existing_config, key):
                        setattr(existing_config, key, value)
            
            return jsonify({"message": f"Function {function_name} updated successfully"})
        
        except Exception as e:
            logger.error(f"Error updating function {function_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route('/functions/<function_name>', methods=['DELETE'])
    @require_auth("manage")
    def delete_function(function_name: str):
        """Delete a function"""
        try:
            # Stop all instances of this function
            load_state = app.orchestrator.load_balancer.get(function_name)
            if load_state:
                for instance in load_state.instances[:]:  # Copy list to avoid modification during iteration
                    app.orchestrator._stop_function_instance(instance.runtime_id)
            
            # Remove from registry
            success = app.orchestrator.registry.remove_function(function_name)
            if not success:
                return jsonify({"error": "Function not found"}), 404
            
            return jsonify({"message": f"Function {function_name} deleted successfully"})
        
        except Exception as e:
            logger.error(f"Error deleting function {function_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Platform statistics endpoints
    @app.route('/stats', methods=['GET'])
    @require_auth("manage")
    def get_stats():
        """Get platform statistics"""
        try:
            stats = app.orchestrator.get_stats()
            return jsonify(stats)
        
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route('/stats/functions/<function_name>', methods=['GET'])
    @require_auth("manage")
    def get_function_stats(function_name: str):
        """Get function-specific statistics"""
        try:
            func_info = app.orchestrator.get_function_info(function_name)
            if "error" in func_info:
                return jsonify(func_info), 404
            
            return jsonify(func_info['stats'])
        
        except Exception as e:
            logger.error(f"Error getting function stats {function_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Instance management endpoints
    @app.route('/instances', methods=['GET'])
    @require_auth("manage")
    def list_instances():
        """List all function instances"""
        try:
            instances = []
            for runtime_id, instance in app.orchestrator.all_instances.items():
                instances.append({
                    'runtime_id': instance.runtime_id,
                    'function_name': instance.function_name,
                    'execution_mode': instance.execution_mode,
                    'port': instance.port,
                    'created_at': instance.created_at,
                    'last_used': instance.last_used,
                    'request_count': instance.request_count,
                    'error_count': instance.error_count
                })
            
            return jsonify({"instances": instances})
        
        except Exception as e:
            logger.error(f"Error listing instances: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route('/instances/<runtime_id>', methods=['DELETE'])
    @require_auth("manage")
    def stop_instance(runtime_id: str):
        """Stop a specific instance"""
        try:
            success = app.orchestrator._stop_function_instance(runtime_id)
            if not success:
                return jsonify({"error": "Instance not found"}), 404
            
            return jsonify({"message": f"Instance {runtime_id} stopped successfully"})
        
        except Exception as e:
            logger.error(f"Error stopping instance {runtime_id}: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Configuration endpoints
    @app.route('/config', methods=['GET'])
    @require_auth("admin")
    def get_config():
        """Get system configuration"""
        try:
            system_config = app.orchestrator.system_config
            config_dict = {
                'default_mode': system_config.default_mode,
                'max_concurrent_functions': system_config.max_concurrent_functions,
                'cold_start_timeout': system_config.cold_start_timeout,
                'warm_instance_ttl': system_config.warm_instance_ttl,
                'api_gateway_port': system_config.api_gateway_port,
                'container_runtime': system_config.container_runtime,
                'process_isolation_level': system_config.process_isolation_level
            }
            
            return jsonify({"config": config_dict})
        
        except Exception as e:
            logger.error(f"Error getting config: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Error handlers
    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({"error": "Bad request"}), 400
    
    @app.errorhandler(401)
    def unauthorized(error):
        return jsonify({"error": "Unauthorized"}), 401
    
    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({"error": "Forbidden"}), 403
    
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Not found"}), 404
    
    @app.errorhandler(429)
    def rate_limit_exceeded(error):
        return jsonify({"error": "Rate limit exceeded"}), 429
    
    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({"error": "Internal server error"}), 500
    
    return app


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='FaaS Platform API Gateway')
    parser.add_argument('--config', '-c', help='Configuration file path')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', '-p', type=int, default=8000, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create and run app
    app = create_app(args.config)
    
    logger.info(f"Starting FaaS API Gateway on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main() 