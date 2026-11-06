# Kubernetes OpenTelemetry 采集平台

换到新公司的生产 Kubernetes 集群，仍用同一套安装流程。按后端实际提供的 OTLP HTTP/gRPC 或 Metrics Remote Write 接口，填写协议、地址和认证，不区分云厂商或后端品牌。

1. 打开 [02-部署文档.md](02-部署文档.md)。
2. 复制 [values-platform.yaml](values-platform.yaml)，填写目标集群和后端连接信息。
3. 按文档安装 Operator、采集平台，灰度接入一个服务并验收三信号。

安装只需两份配置：`values-operator.yaml` 是 Operator 固定预设，通常无需改；`values-platform.yaml` 填写本集群差异。Chart 已内置通用处理、采样、资源和副本默认值，按需在集群配置中覆盖。

默认无 PVC，日志直发，Kafka、增强诊断和 HPA 关闭。需要时在同一份集群配置中开启；自建 Kafka 和云 Kafka 使用相同接入参数。高级说明集中在 [01-架构文档.md](01-架构文档.md)，不用复制其他场景文件。

本项目安装采集和处理层，三个后端写入口需先具备。当前平台包为 `charts/otel-platform-0.7.12.tgz`，官方 Operator 包为 `charts/opentelemetry-operator-0.126.1.tgz`。`charts/otel-platform/` 是维护源码，安装使用现成包，无需生成脚本。

0.7.12 统一日志大小上限，增加独立 requests/limits 覆盖和可配置诊断指标白名单，合并重复 Collector 基础模板。常用资源、副本、日志、存储和 HPA 参数在集群配置入口可见；已有 local values 仍兼容。验证范围见[当前验收状态](01-架构文档.md#当前验收状态)，升级前看[兼容说明](01-架构文档.md#升级兼容)。

业务注入与 Service annotation 指标接入按[部署文档第 5 节](02-部署文档.md#5-接入业务注入-agent-与抓取指标)执行；批量接入可参考[共享指标接入](01-架构文档.md#共享指标接入)，并按[服务身份核对](01-架构文档.md#服务身份审计)检查实际后端数据。
