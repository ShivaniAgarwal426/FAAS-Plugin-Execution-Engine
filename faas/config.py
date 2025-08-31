"""
Configuration Management for FaaS Platform

Handles system-wide and function-specific configuration through environment variables,
YAML files, and runtime parameters.
"""

import os
import yaml
import json
from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass, field
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class SystemConfig:
    """System-level configuration"""
    # Execution settings
    default_mode: str = "process"
    max_concurrent_functions: int = 1000
    cold_start_timeout: int = 30
    warm_instance_ttl: int = 600  # 10 minutes
    
    # Server settings
    api_gateway_port: int = 8000
    api_gateway_host: str = "0.0.0.0"
    worker_start_port: int = 9000
    
    # Storage settings
    function_storage_path: str = "/tmp/faas_functions"
    logs_path: str = "/tmp/faas_logs"
    metrics_path: str = "/tmp/faas_metrics"
    
    # Container settings
    container_runtime: str = "docker"
    base_image: str = "python:3.11-slim"
    container_memory_limit: str = "128Mi"
    container_cpu_limit: str = "100m"
    
    # Process settings
    process_isolation_level: str = "strict"
    namespace_types: List[str] = field(default_factory=lambda: ["pid", "mount", "user"])
    process_memory_limit: str = "128Mi"
    process_cpu_limit: str = "100m"
    
    # Security settings
    enable_user_namespaces: bool = True
    enable_seccomp: bool = True
    drop_capabilities: List[str] = field(default_factory=lambda: [
        "CAP_SYS_ADMIN", "CAP_NET_ADMIN", "CAP_SYS_MODULE"
    ])
    
    # Monitoring settings
    metrics_enabled: bool = True
    logging_level: str = "INFO"
    enable_tracing: bool = True


@dataclass
class FunctionConfig:
    """Function-specific configuration"""
    # Basic settings
    name: str
    runtime: str = "python3.11"
    execution_mode: str = "process"  # or "container"
    handler: str = "handle"
    
    # Resource settings
    timeout: int = 30
    memory: str = "256Mi"
    cpu_limit: str = "100m"
    
    # Code settings
    code_path: str = ""
    dependencies: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    
    # Scaling settings
    min_instances: int = 0
    max_instances: int = 10
    scale_factor: float = 1.5
    
    # Security settings
    isolation_level: str = "default"  # default, strict, minimal
    network_access: bool = True
    filesystem_access: str = "readonly"  # readonly, writable, minimal


class ConfigManager:
    """Manages system and function configurations"""
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or os.getenv('FAAS_CONFIG_FILE', 'faas_config.yaml')
        self.system_config = SystemConfig()
        self.function_configs: Dict[str, FunctionConfig] = {}
        
        # Load configurations
        self._load_system_config()
        self._load_function_configs()
    
    def _load_system_config(self):
        """Load system configuration from environment and file"""
        # Load from file if exists
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config_data = yaml.safe_load(f)
                    if config_data and 'system' in config_data:
                        self._update_system_config_from_dict(config_data['system'])
                        logger.info(f"Loaded system config from {self.config_file}")
            except Exception as e:
                logger.warning(f"Failed to load config file {self.config_file}: {e}")
        
        # Override with environment variables
        self._load_system_config_from_env()
    
    def _update_system_config_from_dict(self, config_dict: Dict[str, Any]):
        """Update system config from dictionary"""
        for key, value in config_dict.items():
            if hasattr(self.system_config, key):
                setattr(self.system_config, key, value)
    
    def _load_system_config_from_env(self):
        """Load system config from environment variables"""
        env_mappings = {
            'FAAS_DEFAULT_MODE': 'default_mode',
            'FAAS_MAX_CONCURRENT': 'max_concurrent_functions',
            'FAAS_COLD_START_TIMEOUT': 'cold_start_timeout',
            'FAAS_WARM_TTL': 'warm_instance_ttl',
            'FAAS_API_PORT': 'api_gateway_port',
            'FAAS_API_HOST': 'api_gateway_host',
            'FAAS_WORKER_START_PORT': 'worker_start_port',
            'FAAS_FUNCTION_STORAGE': 'function_storage_path',
            'FAAS_LOGS_PATH': 'logs_path',
            'FAAS_METRICS_PATH': 'metrics_path',
            'FAAS_CONTAINER_RUNTIME': 'container_runtime',
            'FAAS_BASE_IMAGE': 'base_image',
            'FAAS_CONTAINER_MEMORY': 'container_memory_limit',
            'FAAS_CONTAINER_CPU': 'container_cpu_limit',
            'FAAS_PROCESS_ISOLATION': 'process_isolation_level',
            'FAAS_PROCESS_MEMORY': 'process_memory_limit',
            'FAAS_PROCESS_CPU': 'process_cpu_limit',
            'FAAS_LOG_LEVEL': 'logging_level'
        }
        
        for env_var, config_attr in env_mappings.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                # Convert types appropriately
                current_value = getattr(self.system_config, config_attr)
                if isinstance(current_value, bool):
                    value = value.lower() in ['true', '1', 'yes', 'on']
                elif isinstance(current_value, int):
                    value = int(value)
                elif isinstance(current_value, float):
                    value = float(value)
                elif isinstance(current_value, list):
                    value = [item.strip() for item in value.split(',')]
                
                setattr(self.system_config, config_attr, value)
                logger.debug(f"Set {config_attr} = {value} from {env_var}")
    
    def _load_function_configs(self):
        """Load function configurations"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config_data = yaml.safe_load(f)
                    if config_data and 'functions' in config_data:
                        for func_name, func_config in config_data['functions'].items():
                            self.function_configs[func_name] = self._create_function_config(
                                func_name, func_config
                            )
                        logger.info(f"Loaded {len(self.function_configs)} function configs")
            except Exception as e:
                logger.warning(f"Failed to load function configs: {e}")
    
    def _create_function_config(self, name: str, config_dict: Dict[str, Any]) -> FunctionConfig:
        """Create function config from dictionary"""
        func_config = FunctionConfig(name=name)
        
        for key, value in config_dict.items():
            if hasattr(func_config, key):
                setattr(func_config, key, value)
        
        return func_config
    
    def get_system_config(self) -> SystemConfig:
        """Get system configuration"""
        return self.system_config
    
    def get_function_config(self, function_name: str) -> Optional[FunctionConfig]:
        """Get function configuration"""
        return self.function_configs.get(function_name)
    
    def add_function_config(self, function_config: FunctionConfig):
        """Add or update function configuration"""
        self.function_configs[function_config.name] = function_config
        logger.info(f"Added function config: {function_config.name}")
    
    def remove_function_config(self, function_name: str):
        """Remove function configuration"""
        if function_name in self.function_configs:
            del self.function_configs[function_name]
            logger.info(f"Removed function config: {function_name}")
    
    def get_runtime_config(self, function_name: str, runtime_id: str) -> Dict[str, str]:
        """Get runtime environment variables for a function"""
        func_config = self.get_function_config(function_name)
        if not func_config:
            func_config = FunctionConfig(name=function_name)
        
        # Base runtime configuration
        runtime_config = {
            'RUNTIME_PORT': str(self._get_next_port()),
            'RUNTIME_HOST': '0.0.0.0',
            'FUNCTION_PATH': func_config.code_path or f'/tmp/{function_name}.py',
            'FUNCTION_NAME': func_config.handler,
            'FUNCTION_TIMEOUT': str(func_config.timeout),
            'EXECUTION_MODE': func_config.execution_mode,
            'RUNTIME_ID': runtime_id,
            'LOG_LEVEL': self.system_config.logging_level
        }
        
        # Add function-specific environment variables
        runtime_config.update(func_config.environment)
        
        # Add resource limits
        runtime_config.update({
            'MEMORY_LIMIT': func_config.memory,
            'CPU_LIMIT': func_config.cpu_limit
        })
        
        return runtime_config
    
    def _get_next_port(self) -> int:
        """Get next available port for runtime host"""
        # Simple implementation - in production would track used ports
        import random
        return random.randint(9000, 9999)
    
    def save_config(self, config_file: Optional[str] = None):
        """Save current configuration to file"""
        config_file = config_file or self.config_file
        
        config_data = {
            'system': self._system_config_to_dict(),
            'functions': {
                name: self._function_config_to_dict(config)
                for name, config in self.function_configs.items()
            }
        }
        
        try:
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
            logger.info(f"Configuration saved to {config_file}")
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            raise
    
    def _system_config_to_dict(self) -> Dict[str, Any]:
        """Convert system config to dictionary"""
        return {
            'default_mode': self.system_config.default_mode,
            'max_concurrent_functions': self.system_config.max_concurrent_functions,
            'cold_start_timeout': self.system_config.cold_start_timeout,
            'warm_instance_ttl': self.system_config.warm_instance_ttl,
            'api_gateway_port': self.system_config.api_gateway_port,
            'api_gateway_host': self.system_config.api_gateway_host,
            'worker_start_port': self.system_config.worker_start_port,
            'function_storage_path': self.system_config.function_storage_path,
            'logs_path': self.system_config.logs_path,
            'metrics_path': self.system_config.metrics_path,
            'container_runtime': self.system_config.container_runtime,
            'base_image': self.system_config.base_image,
            'container_memory_limit': self.system_config.container_memory_limit,
            'container_cpu_limit': self.system_config.container_cpu_limit,
            'process_isolation_level': self.system_config.process_isolation_level,
            'namespace_types': self.system_config.namespace_types,
            'process_memory_limit': self.system_config.process_memory_limit,
            'process_cpu_limit': self.system_config.process_cpu_limit,
            'enable_user_namespaces': self.system_config.enable_user_namespaces,
            'enable_seccomp': self.system_config.enable_seccomp,
            'drop_capabilities': self.system_config.drop_capabilities,
            'metrics_enabled': self.system_config.metrics_enabled,
            'logging_level': self.system_config.logging_level,
            'enable_tracing': self.system_config.enable_tracing
        }
    
    def _function_config_to_dict(self, func_config: FunctionConfig) -> Dict[str, Any]:
        """Convert function config to dictionary"""
        return {
            'runtime': func_config.runtime,
            'execution_mode': func_config.execution_mode,
            'handler': func_config.handler,
            'timeout': func_config.timeout,
            'memory': func_config.memory,
            'cpu_limit': func_config.cpu_limit,
            'code_path': func_config.code_path,
            'dependencies': func_config.dependencies,
            'environment': func_config.environment,
            'min_instances': func_config.min_instances,
            'max_instances': func_config.max_instances,
            'scale_factor': func_config.scale_factor,
            'isolation_level': func_config.isolation_level,
            'network_access': func_config.network_access,
            'filesystem_access': func_config.filesystem_access
        }


def create_sample_config():
    """Create a sample configuration file"""
    config_data = {
        'system': {
            'default_mode': 'process',
            'max_concurrent_functions': 1000,
            'cold_start_timeout': 30,
            'warm_instance_ttl': 600,
            'api_gateway_port': 8000,
            'function_storage_path': '/tmp/faas_functions',
            'container_runtime': 'docker',
            'base_image': 'python:3.11-slim',
            'process_isolation_level': 'strict',
            'logging_level': 'INFO'
        },
        'functions': {
            'example-function': {
                'runtime': 'python3.11',
                'execution_mode': 'process',
                'handler': 'handle',
                'timeout': 30,
                'memory': '256Mi',
                'dependencies': ['requests==2.28.0'],
                'environment': {
                    'API_KEY': 'your-api-key',
                    'DEBUG': 'false'
                }
            }
        }
    }
    
    with open('faas_config.yaml', 'w') as f:
        yaml.dump(config_data, f, default_flow_style=False)
    
    print("Sample configuration created: faas_config.yaml")


if __name__ == '__main__':
    # Create sample config if run directly
    create_sample_config() 