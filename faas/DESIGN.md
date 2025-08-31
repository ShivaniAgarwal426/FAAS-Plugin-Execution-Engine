# Lightweight FaaS Platform Design

## Executive Summary

This document presents the design for internal Function-as-a-Service (FaaS) platform, capable of handling high-volume concurrent requests (lakhs per day) with dual execution modes. The platform provides both container-based and process-based execution methods, allowing users to choose the optimal approach based on their specific requirements.

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Gateway                              │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │
│  │   Load Balancer │ │   Rate Limiter  │ │   Auth Handler  │   │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘   │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────┴───────────────────────────────────────┐
│                     Orchestrator                                │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │
│  │Function Registry│ │Execution Manager│ │Resource Manager │   │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘   │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          │                               │
┌─────────┴─────────┐           ┌─────────┴─────────┐
│ Container-Based   │           │ Process-Based     │
│ Worker Pool       │           │ Worker Pool       │
│                   │           │                   │
│ ┌───────────────┐ │           │ ┌───────────────┐ │
│ │Runtime Host 1 │ │           │ │Runtime Host 1 │ │
│ │  (Container)  │ │           │ │  (Process)    │ │
│ └───────────────┘ │           │ └───────────────┘ │
│ ┌───────────────┐ │           │ ┌───────────────┐ │
│ │Runtime Host 2 │ │           │ │Runtime Host 2 │ │
│ │  (Container)  │ │           │ │  (Process)    │ │
│ └───────────────┘ │           │ └───────────────┘ │
│ ┌───────────────┐ │           │ ┌───────────────┐ │
│ │Runtime Host N │ │           │ │Runtime Host N │ │
│ │  (Container)  │ │           │ │  (Process)    │ │
│ └───────────────┘ │           │ └───────────────┘ │
└───────────────────┘           └───────────────────┘
```

### Core Components:

#### 1. API Gateway
- **Load Balancer**: Distributes incoming requests across available workers
- **Rate Limiter**: Controls request throttling and prevents abuse
- **Auth Handler**: Manages authentication and authorization
- **Request Router**: Routes requests to appropriate function instances

#### 2. Orchestrator
- **Function Registry**: Stores function metadata, code, and configuration
- **Execution Manager**: Manages function lifecycle and execution mode selection
- **Resource Manager**: Monitors and allocates system resources

#### 3. Dual Worker Pools
- **Container-Based Pool**: Docker/containerd-based isolated execution
- **Process-Based Pool**: Direct process execution with namespace isolation

## 2. Invocation Flow

### 2.1 Cold Start Flow

```
Client Request → API Gateway → Orchestrator → Worker Pool Selection
     ↓
Function Registry Lookup → Resource Allocation → Runtime Initialization
     ↓
Code Loading → Dependency Installation → Function Execution → Response
```

**Detailed Steps:**

1. **Request Arrival**: API Gateway receives HTTP request
2. **Authentication**: Verify request credentials and permissions
3. **Function Resolution**: Look up function metadata in registry
4. **Execution Mode Decision**: Determine container vs process based on configuration
5. **Resource Allocation**: Reserve CPU, memory, and network resources
6. **Runtime Initialization**:
   - **Container Mode**: Pull image, create container, start runtime_host
   - **Process Mode**: Create process, set up namespaces, start runtime_host
7. **Code Loading**: Load user function from storage
8. **Dependency Resolution**: Install required packages (if not cached)
9. **Function Execution**: Execute user's `handle(request)` function
10. **Response Return**: Return result through the chain

**Cold Start Times:**
- **Container Mode**: 100-500ms (depending on image size)
- **Process Mode**: 10-50ms (minimal overhead)

### 2.2 Warm Start Flow

```
Client Request → API Gateway → Orchestrator → Existing Runtime Instance
     ↓
Function Execution → Response (Sub-10ms execution)
```

**Detailed Steps:**

1. **Request Arrival**: API Gateway receives HTTP request
2. **Instance Lookup**: Find existing warm runtime instance
3. **Direct Execution**: Execute function in pre-warmed environment
4. **Response Return**: Return result immediately

**Warm Start Times:**
- **Both Modes**: 1-10ms (function execution time only)

## 3. Technology Decision Analysis

### 3.1 Container-Based Execution

**Architecture:**
```
Docker/Containerd Runtime
├── Isolated Filesystem (overlayfs)
├── Network Namespace (bridge/host)
├── Process Isolation (PID namespace)
├── Resource Limits (cgroups)
└── Security Context (user namespace)
```

**Advantages:**
- **Maximum Security Isolation**: Complete OS-level isolation
- **Dependency Management**: Self-contained with all dependencies
- **Reproducible Environments**: Identical execution across deployments
- **Resource Control**: Fine-grained CPU/memory/IO limits
- **Multi-language Support**: Easy to extend beyond Python

**Disadvantages:**
- **Higher Cold Start Latency**: 100-500ms container creation time
- **Resource Overhead**: ~10-50MB base memory per container
- **Implementation Complexity**: Container orchestration, image management
- **Storage Requirements**: Images require significant disk space

### 3.2 Process-Based Execution

**Architecture:**
```
OS Process with Namespaces
├── PID Namespace (process isolation)
├── Mount Namespace (filesystem isolation)
├── Network Namespace (optional)
├── User Namespace (privilege isolation)
└── Resource Limits (cgroups v2)
```

**Advantages:**
- **Ultra-Fast Cold Start**: 10-50ms process creation time
- **Low Resource Overhead**: ~2-5MB base memory per process
- **Implementation Simplicity**: Direct process management
- **High Density**: Support more concurrent functions per node

**Disadvantages:**
- **Limited Security Isolation**: Shared kernel, potential escape vectors
- **Dependency Conflicts**: Shared system libraries may cause conflicts
- **Platform Dependency**: Linux-specific namespace features
- **Debugging Complexity**: Shared system state can complicate troubleshooting

### 3.3 Comparative Analysis

| Aspect | Container-Based | Process-Based |
|--------|----------------|---------------|
| **Security Isolation** | ★★★★★ Complete OS isolation | ★★★☆☆ Process-level isolation |
| **Resource Overhead** | ★★☆☆☆ 10-50MB base memory | ★★★★★ 2-5MB base memory |
| **Cold Start Performance** | ★★☆☆☆ 100-500ms | ★★★★★ 10-50ms |
| **Implementation Complexity** | ★★☆☆☆ Container orchestration | ★★★★☆ Process management |
| **Scalability** | ★★★☆☆ Limited by resources | ★★★★★ High density possible |
| **Debugging** | ★★★★☆ Isolated environments | ★★★☆☆ Shared system state |
| **Multi-tenancy** | ★★★★★ Strong isolation | ★★★☆☆ Namespace isolation |

### 3.4 Recommendation

**Primary Recommendation: Dual-Mode Architecture with Smart Defaults**

Given requirements for high-volume concurrent processing (lakhs of requests per day), I recommend implementing both execution modes with intelligent defaults:

**Default Strategy:**
1. **Process-based for latency-sensitive workloads**: Use for functions requiring <100ms response times
2. **Container-based for security-sensitive workloads**: Use for untrusted code or multi-tenant scenarios
3. **Hybrid approach**: Allow users to specify execution mode per function

**Justification:**
1. **Performance at Scale**: Process-based execution provides 5-10x faster cold starts, crucial for high request volumes
2. **Cost Efficiency**: Lower resource overhead enables higher function density and reduced infrastructure costs
3. **Flexibility**: Users can choose optimal execution mode based on their security vs performance requirements
4. **Future-Proof**: Architecture supports both current needs and future security requirements

**Implementation Phases:**
1. **Phase 1**: Implement process-based execution for immediate performance benefits
2. **Phase 2**: Add container-based execution for security-sensitive use cases
3. **Phase 3**: Implement intelligent auto-selection based on function characteristics

## 4. Configuration Architecture

The platform supports comprehensive configuration through environment variables and config files:

### 4.1 System Configuration
```yaml
# System-level configuration
execution:
  default_mode: "process"  # or "container"
  max_concurrent_functions: 1000
  cold_start_timeout: "30s"
  warm_instance_ttl: "10m"

container:
  runtime: "docker"  # or "containerd"
  base_image: "python:3.11-slim"
  memory_limit: "128Mi"
  cpu_limit: "100m"

process:
  isolation_level: "strict"  # or "lightweight"
  namespace_types: ["pid", "mount", "user"]
  memory_limit: "128Mi"
  cpu_limit: "100m"
```

### 4.2 Function-Level Configuration
```yaml
# Per-function configuration
function:
  name: "my-function"
  runtime: "python3.11"
  execution_mode: "process"  # or "container"
  timeout: "30s"
  memory: "256Mi"
  environment:
    - "API_KEY=secret"
  dependencies:
    - "requests==2.28.0"
    - "numpy==1.21.0"
```

## 5. Security Considerations

### 5.1 Process-Based Security
- **Namespace Isolation**: PID, mount, user, network namespaces
- **Resource Limits**: cgroups v2 for CPU, memory, IO limits
- **Capability Dropping**: Remove unnecessary Linux capabilities
- **Seccomp Filters**: Restrict system call access
- **User Isolation**: Run functions as non-root users

### 5.2 Container-Based Security
- **Image Security**: Scan images for vulnerabilities
- **Runtime Security**: Use security profiles (AppArmor/SELinux)
- **Network Policies**: Restrict network access per function
- **Resource Quotas**: Enforce strict resource limits
- **Image Verification**: Verify image signatures and provenance

## 6. Monitoring and Observability

### 6.1 Metrics
- **Function Metrics**: Invocation count, duration, error rate
- **System Metrics**: CPU, memory, network utilization
- **Cold Start Metrics**: Initialization time, warm-up duration
- **Resource Metrics**: Function density, resource efficiency

### 6.2 Logging
- **Structured Logging**: JSON-formatted logs with correlation IDs
- **Function Logs**: Capture stdout/stderr from user functions
- **System Logs**: Platform component logs
- **Audit Logs**: Function deployment and management activities

### 6.3 Tracing
- **Distributed Tracing**: OpenTelemetry-based request tracing
- **Function Tracing**: Track execution flow within functions
- **Performance Profiling**: Identify bottlenecks and optimization opportunities

## 7. Scalability and Performance

### 7.1 Horizontal Scaling
- **Worker Pool Scaling**: Dynamic scaling based on request load
- **Multi-Node Deployment**: Distribute across multiple machines
- **Load Balancing**: Intelligent request distribution

### 7.2 Vertical Scaling
- **Resource Optimization**: Right-size function resources
- **Memory Management**: Efficient memory allocation and cleanup
- **CPU Optimization**: Optimize function execution efficiency

### 7.3 Performance Targets
- **Cold Start**: <50ms (process), <200ms (container)
- **Warm Invocation**: <10ms
- **Throughput**: >10,000 requests/second/node
- **Concurrency**: >1,000 concurrent functions/node

## 8. Implementation Roadmap

### Phase 1: Core Runtime (Weeks 1-2)
- [ ] Implement `runtime_host.py` with dual-mode support
- [ ] Create process-based execution system
- [ ] Basic function loading and execution
- [ ] Configuration management

### Phase 2: Container Support (Weeks 3-4)
- [ ] Implement container-based execution
- [ ] Docker/containerd integration
- [ ] Image management and caching
- [ ] Security hardening

### Phase 3: Orchestration (Weeks 5-6)
- [ ] API Gateway implementation
- [ ] Function registry and storage
- [ ] Load balancing and routing
- [ ] Resource management

### Phase 4: Production Features (Weeks 7-8)
- [ ] Monitoring and logging
- [ ] Auto-scaling
- [ ] Performance optimization
- [ ] Comprehensive testing

## 9. Conclusion

The dual-mode FaaS platform provides with the flexibility to optimize for both performance and security based on specific use case requirements. The process-based execution mode delivers ultra-fast cold starts essential for high-volume concurrent processing, while the container-based mode provides maximum isolation for security-sensitive workloads.

This architecture positions to handle current scale requirements while providing a foundation for future growth and diverse use case requirements. 