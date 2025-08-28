"""
Orchestrator for FaaS Platform

Manages function execution across dual modes (process/container), handles load balancing,
scaling, and provides the main API gateway functionality.
"""

import os
import json
import time
import logging
import asyncio
import threading
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
import uuid
from concurrent.futures import ThreadPoolExecutor
import requests

from config import ConfigManager, FunctionConfig
from process_executor import ProcessExecutor
from container_executor import ContainerExecutor

logger = logging.getLogger(__name__)


@dataclass
class FunctionInstance:
    """Represents an active function instance"""
    runtime_id: str
    function_name: str
    execution_mode: str
    port: int
    created_at: float
    last_used: float
    request_count: int = 0
    error_count: int = 0


@dataclass
class LoadBalancingState:
    """State for load balancing across instances"""
    instances: List[FunctionInstance] = field(default_factory=list)
    round_robin_index: int = 0
    last_scale_event: float = 0


class FunctionRegistry:
    """Registry for storing and managing function metadata"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.functions: Dict[str, FunctionConfig] = {}
        self.function_code: Dict[str, str] = {}
        
        # Load functions from config
        self._load_functions_from_config()
    
    def _load_functions_from_config(self):
        """Load functions from configuration manager"""
        for name, config in self.config_manager.function_configs.items():
            self.functions[name] = config
            
            # Load function code if exists
            if config.code_path and os.path.exists(config.code_path):
                try:
                    with open(config.code_path, 'r') as f:
                        self.function_code[name] = f.read()
                    logger.info(f"Loaded function code for {name}")
                except Exception as e:
                    logger.warning(f"Failed to load function code for {name}: {e}")
    
    def register_function(self, name: str, config: FunctionConfig, code: str = None):
        """Register a new function"""
        self.functions[name] = config
        if code:
            self.function_code[name] = code
        
        # Add to config manager
        self.config_manager.add_function_config(config)
        
        logger.info(f"Registered function: {name}")
    
    def get_function(self, name: str) -> Optional[FunctionConfig]:
        """Get function configuration"""
        return self.functions.get(name)
    
    def get_function_code(self, name: str) -> Optional[str]:
        """Get function source code"""
        return self.function_code.get(name)
    
    def list_functions(self) -> List[str]:
        """List all registered functions"""
        return list(self.functions.keys())
    
    def remove_function(self, name: str) -> bool:
        """Remove a function"""
        if name in self.functions:
            del self.functions[name]
            if name in self.function_code:
                del self.function_code[name]
            
            self.config_manager.remove_function_config(name)
            logger.info(f"Removed function: {name}")
            return True
        return False


class AutoScaler:
    """Handles automatic scaling of function instances"""
    
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.scale_up_threshold = 0.8  # Scale up if 80% of instances are busy
        self.scale_down_threshold = 0.3  # Scale down if <30% of instances are busy
        self.min_scale_interval = 30  # Minimum seconds between scaling events
        self.running = False
        self.scale_thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start auto-scaling"""
        if not self.running:
            self.running = True
            self.scale_thread = threading.Thread(target=self._scale_loop, daemon=True)
            self.scale_thread.start()
            logger.info("Auto-scaler started")
    
    def stop(self):
        """Stop auto-scaling"""
        self.running = False
        if self.scale_thread:
            self.scale_thread.join(timeout=5)
        logger.info("Auto-scaler stopped")
    
    def _scale_loop(self):
        """Main scaling loop"""
        while self.running:
            try:
                self._check_and_scale_functions()
                time.sleep(10)  # Check every 10 seconds
            except Exception as e:
                logger.error(f"Error in scaling loop: {e}")
                time.sleep(30)  # Wait longer on error
    
    def _check_and_scale_functions(self):
        """Check and scale functions as needed"""
        current_time = time.time()
        
        for function_name in self.orchestrator.registry.list_functions():
            load_state = self.orchestrator.load_balancer.get(function_name)
            if not load_state or len(load_state.instances) == 0:
                continue
            
            # Skip if scaled recently
            if current_time - load_state.last_scale_event < self.min_scale_interval:
                continue
            
            function_config = self.orchestrator.registry.get_function(function_name)
            if not function_config:
                continue
            
            # Calculate current load
            active_instances = len([inst for inst in load_state.instances 
                                 if self._is_instance_healthy(inst)])
            total_instances = len(load_state.instances)
            
            # Scale up if needed
            if (active_instances > 0 and 
                active_instances / total_instances > self.scale_up_threshold and
                total_instances < function_config.max_instances):
                
                self._scale_up(function_name, function_config)
                load_state.last_scale_event = current_time
            
            # Scale down if needed
            elif (total_instances > function_config.min_instances and
                  active_instances / total_instances < self.scale_down_threshold):
                
                self._scale_down(function_name, function_config)
                load_state.last_scale_event = current_time
    
    def _scale_up(self, function_name: str, function_config: FunctionConfig):
        """Scale up function instances"""
        try:
            logger.info(f"Scaling up {function_name}")
            self.orchestrator._create_function_instance(function_name, function_config)
        except Exception as e:
            logger.error(f"Failed to scale up {function_name}: {e}")
    
    def _scale_down(self, function_name: str, function_config: FunctionConfig):
        """Scale down function instances"""
        try:
            load_state = self.orchestrator.load_balancer.get(function_name)
            if load_state and len(load_state.instances) > function_config.min_instances:
                # Remove least recently used instance
                oldest_instance = min(load_state.instances, key=lambda x: x.last_used)
                logger.info(f"Scaling down {function_name}, removing instance {oldest_instance.runtime_id}")
                self.orchestrator._stop_function_instance(oldest_instance.runtime_id)
        except Exception as e:
            logger.error(f"Failed to scale down {function_name}: {e}")
    
    def _is_instance_healthy(self, instance: FunctionInstance) -> bool:
        """Check if instance is healthy and responsive"""
        try:
            response = requests.get(f"http://localhost:{instance.port}/health", timeout=2)
            return response.status_code == 200
        except Exception:
            return False


class FaaSOrchestrator:
    """Main orchestrator managing the FaaS platform"""
    
    def __init__(self, config_file: str = None):
        # Initialize configuration
        self.config_manager = ConfigManager(config_file)
        self.system_config = self.config_manager.get_system_config()
        
        # Initialize components
        self.registry = FunctionRegistry(self.config_manager)
        self.process_executor = ProcessExecutor(self.system_config)
        self.container_executor = ContainerExecutor(self.system_config)
        self.autoscaler = AutoScaler(self)
        
        # Load balancing state
        self.load_balancer: Dict[str, LoadBalancingState] = {}
        self.all_instances: Dict[str, FunctionInstance] = {}
        
        # Threading
        self.executor_pool = ThreadPoolExecutor(max_workers=50)
        self.cleanup_thread: Optional[threading.Thread] = None
        self.running = False
        
        logger.info("FaaS Orchestrator initialized")
    
    def start(self):
        """Start the orchestrator"""
        self.running = True
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        
        # Start auto-scaler
        self.autoscaler.start()
        
        # Pre-warm functions if configured
        self._pre_warm_functions()
        
        logger.info("FaaS Orchestrator started")
    
    def stop(self):
        """Stop the orchestrator"""
        logger.info("Stopping FaaS Orchestrator")
        
        self.running = False
        
        # Stop auto-scaler
        self.autoscaler.stop()
        
        # Stop all instances
        instance_ids = list(self.all_instances.keys())
        for runtime_id in instance_ids:
            self._stop_function_instance(runtime_id)
        
        # Shutdown executors
        self.process_executor.shutdown()
        self.container_executor.shutdown()
        
        # Shutdown thread pool
        self.executor_pool.shutdown(wait=True)
        
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        logger.info("FaaS Orchestrator stopped")
    
    def invoke_function(self, function_name: str, request_data: Dict[str, Any], 
                       headers: Dict[str, str] = None) -> Tuple[int, Dict[str, Any]]:
        """Invoke a function and return response"""
        try:
            # Get function configuration
            function_config = self.registry.get_function(function_name)
            if not function_config:
                return 404, {"error": f"Function {function_name} not found"}
            
            # Get or create instance
            instance = self._get_or_create_instance(function_name, function_config)
            if not instance:
                return 500, {"error": "Failed to create function instance"}
            
            # Make request to instance
            response_code, response_data = self._call_instance(instance, request_data, headers)
            
            # Update instance stats
            instance.request_count += 1
            instance.last_used = time.time()
            if response_code >= 400:
                instance.error_count += 1
            
            return response_code, response_data
        
        except Exception as e:
            logger.error(f"Error invoking function {function_name}: {e}")
            return 500, {"error": str(e)}
    
    def _get_or_create_instance(self, function_name: str, function_config: FunctionConfig) -> Optional[FunctionInstance]:
        """Get existing instance or create new one"""
        # Get load balancing state
        if function_name not in self.load_balancer:
            self.load_balancer[function_name] = LoadBalancingState()
        
        load_state = self.load_balancer[function_name]
        
        # Check if we have healthy instances
        healthy_instances = [inst for inst in load_state.instances 
                           if self._is_instance_available(inst)]
        
        if healthy_instances:
            # Use round-robin load balancing
            instance = healthy_instances[load_state.round_robin_index % len(healthy_instances)]
            load_state.round_robin_index = (load_state.round_robin_index + 1) % len(healthy_instances)
            return instance
        
        # Create new instance if none available and under max limit
        if len(load_state.instances) < function_config.max_instances:
            return self._create_function_instance(function_name, function_config)
        
        # All instances are busy, wait for one to become available
        for _ in range(10):  # Try for up to 1 second
            healthy_instances = [inst for inst in load_state.instances 
                               if self._is_instance_available(inst)]
            if healthy_instances:
                instance = healthy_instances[0]
                return instance
            time.sleep(0.1)
        
        return None
    
    def _create_function_instance(self, function_name: str, function_config: FunctionConfig) -> Optional[FunctionInstance]:
        """Create a new function instance"""
        try:
            runtime_id = str(uuid.uuid4())
            
            # Get runtime configuration
            runtime_config = self.config_manager.get_runtime_config(function_name, runtime_id)
            
            # Choose executor based on execution mode
            if function_config.execution_mode == "container":
                executor = self.container_executor
                logger.info(f"Creating container instance for {function_name}")
            else:
                executor = self.process_executor
                logger.info(f"Creating process instance for {function_name}")
            
            # Create instance
            runtime_id = executor.create_instance(function_name, function_config, runtime_config)
            
            # Create instance record
            instance = FunctionInstance(
                runtime_id=runtime_id,
                function_name=function_name,
                execution_mode=function_config.execution_mode,
                port=int(runtime_config['RUNTIME_PORT']),
                created_at=time.time(),
                last_used=time.time()
            )
            
            # Add to tracking
            self.all_instances[runtime_id] = instance
            
            if function_name not in self.load_balancer:
                self.load_balancer[function_name] = LoadBalancingState()
            self.load_balancer[function_name].instances.append(instance)
            
            logger.info(f"Created {function_config.execution_mode} instance {runtime_id} for {function_name}")
            return instance
        
        except Exception as e:
            logger.error(f"Failed to create instance for {function_name}: {e}")
            return None
    
    def _stop_function_instance(self, runtime_id: str) -> bool:
        """Stop a function instance"""
        instance = self.all_instances.get(runtime_id)
        if not instance:
            return False
        
        try:
            # Choose executor
            if instance.execution_mode == "container":
                executor = self.container_executor
            else:
                executor = self.process_executor
            
            # Stop instance
            success = executor.stop_instance(runtime_id)
            
            if success:
                # Remove from tracking
                if runtime_id in self.all_instances:
                    del self.all_instances[runtime_id]
                
                # Remove from load balancer
                load_state = self.load_balancer.get(instance.function_name)
                if load_state:
                    load_state.instances = [inst for inst in load_state.instances 
                                          if inst.runtime_id != runtime_id]
                
                logger.info(f"Stopped instance {runtime_id}")
            
            return success
        
        except Exception as e:
            logger.error(f"Failed to stop instance {runtime_id}: {e}")
            return False
    
    def _call_instance(self, instance: FunctionInstance, request_data: Dict[str, Any], 
                      headers: Dict[str, str] = None) -> Tuple[int, Dict[str, Any]]:
        """Make HTTP call to function instance"""
        try:
            url = f"http://localhost:{instance.port}/"
            
            # Prepare headers
            req_headers = {"Content-Type": "application/json"}
            if headers:
                req_headers.update(headers)
            
            # Make request
            response = requests.post(url, json=request_data, headers=req_headers, timeout=30)
            
            # Parse response
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"result": response.text}
            
            return response.status_code, response_data
        
        except requests.exceptions.Timeout:
            return 408, {"error": "Function timeout"}
        except requests.exceptions.ConnectionError:
            return 503, {"error": "Function instance unavailable"}
        except Exception as e:
            return 500, {"error": f"Request failed: {str(e)}"}
    
    def _is_instance_available(self, instance: FunctionInstance) -> bool:
        """Check if instance is available for requests"""
        # Simple availability check - in production would be more sophisticated
        try:
            response = requests.get(f"http://localhost:{instance.port}/health", timeout=2)
            return response.status_code == 200
        except Exception:
            return False
    
    def _pre_warm_functions(self):
        """Pre-warm functions with min_instances > 0"""
        for function_name in self.registry.list_functions():
            function_config = self.registry.get_function(function_name)
            if function_config and function_config.min_instances > 0:
                logger.info(f"Pre-warming {function_name} with {function_config.min_instances} instances")
                
                for _ in range(function_config.min_instances):
                    try:
                        self._create_function_instance(function_name, function_config)
                    except Exception as e:
                        logger.error(f"Failed to pre-warm {function_name}: {e}")
                        break
    
    def _cleanup_loop(self):
        """Cleanup expired instances"""
        while self.running:
            try:
                # Cleanup expired instances
                ttl = self.system_config.warm_instance_ttl
                self.process_executor.cleanup_expired_instances(ttl)
                self.container_executor.cleanup_expired_instances(ttl)
                
                # Cleanup orphaned tracking
                self._cleanup_orphaned_tracking()
                
                time.sleep(60)  # Cleanup every minute
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                time.sleep(60)
    
    def _cleanup_orphaned_tracking(self):
        """Remove orphaned instance tracking"""
        orphaned_ids = []
        
        for runtime_id, instance in self.all_instances.items():
            if instance.execution_mode == "container":
                if not self.container_executor.get_instance(runtime_id):
                    orphaned_ids.append(runtime_id)
            else:
                if not self.process_executor.get_instance(runtime_id):
                    orphaned_ids.append(runtime_id)
        
        for runtime_id in orphaned_ids:
            logger.info(f"Cleaning up orphaned tracking for {runtime_id}")
            instance = self.all_instances.get(runtime_id)
            if instance:
                # Remove from load balancer
                load_state = self.load_balancer.get(instance.function_name)
                if load_state:
                    load_state.instances = [inst for inst in load_state.instances 
                                          if inst.runtime_id != runtime_id]
                # Remove from all instances
                del self.all_instances[runtime_id]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get platform statistics"""
        process_stats = self.process_executor.get_stats()
        container_stats = self.container_executor.get_stats()
        
        function_stats = {}
        for function_name, load_state in self.load_balancer.items():
            function_stats[function_name] = {
                'instances': len(load_state.instances),
                'total_requests': sum(inst.request_count for inst in load_state.instances),
                'total_errors': sum(inst.error_count for inst in load_state.instances)
            }
        
        return {
            'platform': {
                'total_functions': len(self.registry.list_functions()),
                'total_instances': len(self.all_instances),
                'uptime': time.time() - (self.start_time if hasattr(self, 'start_time') else time.time())
            },
            'executors': {
                'process': process_stats,
                'container': container_stats
            },
            'functions': function_stats
        }
    
    def get_function_info(self, function_name: str) -> Dict[str, Any]:
        """Get detailed information about a function"""
        function_config = self.registry.get_function(function_name)
        if not function_config:
            return {"error": "Function not found"}
        
        load_state = self.load_balancer.get(function_name, LoadBalancingState())
        
        return {
            'name': function_name,
            'config': {
                'execution_mode': function_config.execution_mode,
                'memory': function_config.memory,
                'timeout': function_config.timeout,
                'min_instances': function_config.min_instances,
                'max_instances': function_config.max_instances
            },
            'instances': [{
                'runtime_id': inst.runtime_id,
                'execution_mode': inst.execution_mode,
                'port': inst.port,
                'created_at': inst.created_at,
                'last_used': inst.last_used,
                'request_count': inst.request_count,
                'error_count': inst.error_count
            } for inst in load_state.instances],
            'stats': {
                'total_instances': len(load_state.instances),
                'total_requests': sum(inst.request_count for inst in load_state.instances),
                'total_errors': sum(inst.error_count for inst in load_state.instances),
                'avg_response_time': 0  # TODO: Implement response time tracking
            }
        } 