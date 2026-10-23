# Kubernetes OpenTelemetry 生产采集平台

先按[部署文档](02-部署文档.md)填写集群参数并安装；组件原理、数据处理、容量及运维细节见[架构文档](01-架构文档.md)。

```text
production-observability/
├── 01-架构文档.md
├── 02-部署文档.md
├── values-platform.yaml       # 复制一份，填写每个集群的信息
├── values-telemetry.yaml      # 通用三信号处理及尾采样
├── values-operator.yaml       # Operator配置
├── charts/
│   ├── opentelemetry-operator-0.126.1.tgz
│   ├── otel-platform-0.7.4.tgz
│   ├── SHA256SUMS
│   └── otel-platform/         # 安装包对应源码，日后维护使用
└── new-dev集群验证/            # 当前测试集群配置、安装步骤及访问凭据
```

本包只安装采集和处理层。三个后端的写入口、认证和证书需先准备；普通Elasticsearch的9200接口不能直接填为OTLP日志地址。

当前版本0.7.4已在new-dev完成Java17/21 + Spring Boot3.5.16真实自动注入及Prometheus/ES/Tempo三信号低流量验证，范围见[架构文档](01-架构文档.md#版本验证)。生产仍需确认目标集群条件、实际业务日志格式及容量，再分批接入。

样例、测试工具、原始报告、缓存和旧版安装包已清理。安装不依赖Python生成器或测试目录。new-dev的CA、节点映射、内存队列及访问凭据仅适用于该测试集群，不复制到生产。
