# System Setup Guide for FaaS Platform

This guide will help you resolve system requirements and get the FaaS platform running on different operating systems.

## 🚨 Common Issues & Solutions

### Issue 1: 'unshare' command not found (macOS/Windows)

The `unshare` command is Linux-specific and provides namespace isolation for the process-based execution mode.

#### On macOS (Your Current System)
```bash
# Option 1: Install via Homebrew (if available)
brew install util-linux

# Option 2: Use Docker Desktop's Linux VM
# Docker Desktop provides a Linux environment where unshare is available

# Option 3: Run in container mode only
python main.py --no-container  # This disables container mode, not process mode
```

#### On Linux (Ubuntu/Debian)
```bash
sudo apt-get update
sudo apt-get install util-linux
```

#### On Linux (CentOS/RHEL)
```bash
sudo yum install util-linux
# or on newer versions:
sudo dnf install util-linux
```

### Issue 2: Docker is not running

#### On macOS
```bash
# Install Docker Desktop
# 1. Download from: https://docs.docker.com/desktop/mac/install/
# 2. Install and start Docker Desktop
# 3. Verify installation:
docker --version
docker info

# Start Docker if not running
open -a Docker
```

#### On Linux
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group (optional, avoids sudo)
sudo usermod -aG docker $USER
# Log out and back in for group changes to take effect
```

#### On Windows
```bash
# Install Docker Desktop for Windows
# Download from: https://docs.docker.com/desktop/windows/install/
```

## 🛠️ Platform-Specific Setup

### macOS Setup (Your System)

Since you're on macOS, here's the recommended setup approach:

1. **Install Docker Desktop**
   ```bash
   # Download and install Docker Desktop for Mac
   # https://docs.docker.com/desktop/mac/install/
   ```

2. **Enable Docker Desktop's Linux VM**
   ```bash
   # Docker Desktop provides a Linux environment
   # where both unshare and container features work
   ```

3. **Alternative: Use container-only mode**
   ```bash
   # Run the platform with container execution only
   # Process mode will fall back to basic process isolation
   python main.py
   ```

4. **Test the setup**
   ```bash
   python main.py --check-system
   ```

### Linux Setup (Production Recommended)

For full feature support, Linux is recommended:

1. **Install dependencies**
   ```bash
   sudo apt-get update
   sudo apt-get install -y util-linux docker.io python3 python3-pip
   
   # Start Docker
   sudo systemctl start docker
   sudo systemctl enable docker
   ```

2. **Install Python dependencies**
   ```bash
   pip3 install -r requirements.txt
   ```

3. **Test the setup**
   ```bash
   python3 main.py --check-system
   ```

## 🚀 Quick Start Options

### Option 1: Docker-First Approach (Recommended for macOS)
```bash
# Ensure Docker Desktop is running
docker --version

# Start the platform with container mode as primary
python main.py

# The platform will automatically handle the mixed environment
```

### Option 2: Process-Only Mode (Limited Features)
```bash
# Run without container support
# Process mode will use basic isolation instead of namespaces
FAAS_DISABLE_CONTAINER=true python main.py
```

### Option 3: Development Mode
```bash
# Run with debug logging to see what's happening
python main.py --debug --log-level DEBUG
```

## 🔧 Configuration for macOS

Create a macOS-specific configuration:

```yaml
# faas_config_macos.yaml
system:
  default_mode: container  # Use container as default on macOS
  max_concurrent_functions: 500  # Lower for macOS
  cold_start_timeout: 45  # Longer timeout for container starts
  warm_instance_ttl: 300  # Shorter TTL for development
  api_gateway_port: 8000
  container_runtime: docker
  base_image: python:3.11-slim
  process_isolation_level: lightweight  # Reduced isolation on macOS
  logging_level: DEBUG

functions:
  example-function:
    runtime: python3.11
    execution_mode: container  # Force container mode
    handler: handle
    timeout: 30
    memory: 256Mi
    dependencies:
      - requests==2.28.0
    environment:
      DEBUG: 'true'
```

Then run with:
```bash
python main.py -c faas_config_macos.yaml
```

## ⚡ Performance Notes

### On macOS:
- **Container mode**: Full functionality, ~300-500ms cold start
- **Process mode**: Limited isolation, ~50ms cold start
- **Recommendation**: Use container mode for security, process mode for speed

### On Linux:
- **Container mode**: Full functionality, ~200ms cold start  
- **Process mode**: Full isolation, ~25ms cold start
- **Recommendation**: Process mode for production, container for security-critical functions

## 🧪 Testing Your Setup

After setting up, test with these commands:

```bash
# 1. Check system requirements
python main.py --check-system

# 2. Start the platform
python main.py &

# 3. Test basic functionality
curl http://localhost:8000/health

# 4. Create and test a function
curl -X POST http://localhost:8000/functions \
  -H "Authorization: Bearer demo-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-mac",
    "code": "def handle(request):\n    import platform\n    return {\"platform\": platform.system(), \"message\": \"Hello from macOS!\"}",
    "config": {"execution_mode": "container"}
  }'

curl -X POST http://localhost:8000/invoke/test-mac \
  -H "Authorization: Bearer demo-key" \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
```

## 🐛 Troubleshooting

### Docker Issues
```bash
# Check Docker status
docker info

# Restart Docker Desktop
# Use Docker Desktop GUI or:
pkill Docker && open -a Docker

# Check Docker permissions
docker run hello-world
```

### Process Isolation Issues
```bash
# Check if running as root (needed for some isolation features)
whoami

# Test basic process creation
python -c "import subprocess; print(subprocess.run(['echo', 'test'], capture_output=True).stdout)"
```

### Port Conflicts
```bash
# Check if port 8000 is in use
lsof -i :8000

# Use different port
python main.py --port 8080
```

## 📚 Additional Resources

- [Docker Desktop for Mac](https://docs.docker.com/desktop/mac/install/)
- [Linux Namespaces Guide](https://man7.org/linux/man-pages/man7/namespaces.7.html)
- [Container Security Best Practices](https://docs.docker.com/engine/security/)

---

**Note**: For production deployment, Linux is strongly recommended for full feature support and optimal performance. 