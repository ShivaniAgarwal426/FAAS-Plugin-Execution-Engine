#!/usr/bin/env python3
"""
Main Launcher for FaaS Platform

This is the main entry point to start the complete FaaS platform including
the API Gateway, orchestrator, and both execution engines.
"""

import os
import sys
import logging
import argparse
import signal
import time
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api_gateway import create_app
from config import create_sample_config


def setup_logging(log_level: str = "INFO", log_file: str = None):
    """Setup logging configuration"""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            *([] if not log_file else [logging.FileHandler(log_file)])
        ]
    )
    
    # Set specific loggers to appropriate levels
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def check_system_requirements():
    """Check if system requirements are met"""
    issues = []
    warnings = []
    
    # Check Python version
    if sys.version_info < (3, 8):
        issues.append("Python 3.8+ is required")
    
    # Check for required commands
    import subprocess
    import platform
    
    system_os = platform.system()
    
    # Check unshare (for process isolation) - Linux only
    if system_os == "Linux":
        try:
            result = subprocess.run(['unshare', '--help'], capture_output=True, timeout=5)
            if result.returncode != 0:
                issues.append("'unshare' command failed - install util-linux package")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            issues.append("'unshare' command not found - install util-linux package")
    else:
        warnings.append(f"Running on {system_os} - process isolation will use basic mode (no namespaces)")
        warnings.append("For full process isolation features, consider using Linux")
    
    # Check Docker (optional for container mode)
    docker_available = False
    try:
        result = subprocess.run(['docker', 'version'], capture_output=True, timeout=10)
        if result.returncode == 0:
            docker_available = True
            print("✓ Docker is available and running")
        else:
            issues.append("Docker is installed but not running - start Docker Desktop")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        if system_os == "Darwin":  # macOS
            issues.append("Docker not found - install Docker Desktop for Mac")
            issues.append("  Download from: https://docs.docker.com/desktop/mac/install/")
        elif system_os == "Linux":
            issues.append("Docker not found - install Docker")
            issues.append("  Run: curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh")
        else:
            issues.append("Docker not found - install Docker Desktop")
    
    # Check for write permissions in temp directories
    import tempfile
    try:
        with tempfile.NamedTemporaryFile() as f:
            pass
        print("✓ Temporary file creation works")
    except Exception as e:
        issues.append(f"Cannot create temporary files - check permissions: {e}")
    
    # Platform-specific recommendations
    if system_os == "Darwin":  # macOS
        if docker_available:
            warnings.append("Recommendation for macOS: Use container mode as primary execution method")
        warnings.append("For best performance, consider running on Linux in production")
    elif system_os == "Linux":
        if docker_available:
            print("✓ Full dual-mode execution available on Linux")
        else:
            warnings.append("Install Docker for full dual-mode support")
    else:  # Windows or other
        warnings.append(f"Platform {system_os} has limited support - use container mode only")
    
    # Combine issues and warnings for return
    all_issues = issues + [f"Warning: {w}" for w in warnings]
    return all_issues


def create_directories(config_file: str = None):
    """Create necessary directories"""
    from config import ConfigManager
    
    config_manager = ConfigManager(config_file)
    system_config = config_manager.get_system_config()
    
    directories = [
        system_config.function_storage_path,
        system_config.logs_path,
        system_config.metrics_path,
        "/tmp/faas_functions",
        "/tmp/faas_logs",
        "/tmp/faas_metrics"
    ]
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            logging.info(f"Created directory: {directory}")
        except Exception as e:
            logging.warning(f"Failed to create directory {directory}: {e}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='FaaS Platform - Lightweight Function-as-a-Service',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Start with default config
  python main.py -c faas_config.yaml     # Start with custom config
  python main.py --create-config         # Create sample config and exit
  python main.py --check-system          # Check system requirements
        """
    )
    
    parser.add_argument('--config', '-c', 
                       help='Configuration file path (default: faas_config.yaml)')
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host to bind API gateway to (default: 0.0.0.0)')
    parser.add_argument('--port', '-p', type=int, default=8000,
                       help='Port to bind API gateway to (default: 8000)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       default='INFO', help='Logging level (default: INFO)')
    parser.add_argument('--log-file', help='Log to file (default: stdout only)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug mode (implies --log-level DEBUG)')
    parser.add_argument('--create-config', action='store_true',
                       help='Create sample configuration and exit')
    parser.add_argument('--check-system', action='store_true',
                       help='Check system requirements and exit')
    parser.add_argument('--no-container', action='store_true',
                       help='Disable container execution (process-only mode)')
    
    args = parser.parse_args()
    
    # Handle special commands
    if args.create_config:
        create_sample_config()
        print("Sample configuration created: faas_config.yaml")
        print("Edit this file to customize your FaaS platform settings.")
        return 0
    
    if args.check_system:
        print("Checking system requirements...")
        issues = check_system_requirements()
        
        if not issues:
            print("✓ All system requirements are met!")
            return 0
        else:
            print("⚠ Issues found:")
            for issue in issues:
                print(f"  - {issue}")
            print("\nSome features may not work properly.")
            return 1
    
    # Setup logging
    log_level = 'DEBUG' if args.debug else args.log_level
    setup_logging(log_level, args.log_file)
    
    logger = logging.getLogger(__name__)
    logger.info("Starting FaaS Platform")
    
    try:
        # Check system requirements (non-blocking)
        issues = check_system_requirements()
        if issues:
            logger.warning("System requirement issues detected:")
            for issue in issues:
                logger.warning(f"  - {issue}")
        
        # Create necessary directories
        create_directories(args.config)
        
        # Set environment variables
        if args.no_container:
            os.environ['FAAS_DISABLE_CONTAINER'] = 'true'
        
        # Create and configure Flask app
        app = create_app(args.config)
        
        # Setup graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            if hasattr(app, 'orchestrator'):
                app.orchestrator.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Print startup information
        print("\n" + "="*60)
        print("🚀 FaaS Platform Started Successfully!")
        print("="*60)
        print(f"API Gateway: http://{args.host}:{args.port}")
        print(f"Health Check: http://{args.host}:{args.port}/health")
        print(f"Configuration: {args.config or 'faas_config.yaml'}")
        print(f"Log Level: {log_level}")
        print("\nAPI Endpoints:")
        print(f"  POST /invoke/<function_name>  - Invoke a function")
        print(f"  GET  /functions              - List all functions")
        print(f"  POST /functions              - Create new function")
        print(f"  GET  /stats                  - Platform statistics")
        print("\nAuthentication:")
        print(f"  Use 'Authorization: Bearer <api-key>' header")
        print(f"  Demo API Key: demo-key")
        print(f"  Admin API Key: admin-key")
        print("\nExecution Modes:")
        print(f"  Process: Fast cold starts (~25ms)")
        print(f"  Container: Maximum isolation (~200ms)")
        print("="*60)
        
        # Start the server
        logger.info(f"Starting API Gateway on {args.host}:{args.port}")
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            threaded=True,
            use_reloader=False  # Disable reloader to avoid issues with threading
        )
    
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        return 0
    except Exception as e:
        logger.error(f"Failed to start FaaS Platform: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main()) 