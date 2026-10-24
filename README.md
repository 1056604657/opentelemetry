# Kubernetes OpenTelemetry 生产采集平台

面向 200 微服务的 Kubernetes 三信号（traces / metrics / logs）采集与处理平台：OpenTelemetry Operator + 自研 `otel-platform` chart（只描述 Collector CR 与支撑资源，由 Operator 生成工作负载）。**本包只安装采集和处理层**，三个后端的写入口、认证和证书需先准备；普通 Elasticsearch 的 9200 接口不能直接填为 OTLP 日志地址。

完整安装步骤见[部署文档](02-部署文档.md)；组件原理、数据处理、容量及运维细节见[架构文档](01-架构文档.md)。

```text
production-observability/
├── 01-架构文档.md
├── 02-部署文档.md
├── values-platform.yaml       # 复制一份，填写每个集群的信息
├── values-telemetry.yaml      # 通用三信号处理及尾采样
├── values-operator.yaml       # Operator配置
├── charts/
│   ├── opentelemetry-operator-0.126.1.tgz
│   ├── otel-platform-0.7.5.tgz
│   ├── SHA256SUMS
│   └── otel-platform/         # 安装包对应源码，日后维护使用
└── new-dev集群验证/            # 当前测试集群配置、安装步骤及访问凭据
```

## 快速开始

```bash
# 1. 校验安装包完整性
shasum -a 256 -c charts/SHA256SUMS

# 2. 复制生产模板并按集群填写（cluster_name / storage_class / 三个后端 endpoint / 三个 token / 私有 CA）
cp values-platform.yaml values-<集群名>.local.yaml

# 3. 安装 Operator（确认集群内没有已存在的 Operator 或 CRD）
helm upgrade --install otel-operator ./charts/opentelemetry-operator-0.126.1.tgz \
  --kube-context <context> --namespace opentelemetry-operator-system --create-namespace \
  -f values-operator.yaml --wait --timeout 10m

# 4. 安装采集平台（telemetry values 在前、集群 values 在后，后者覆盖前者）
helm upgrade --install otel-platform ./charts/otel-platform-0.7.5.tgz \
  --kube-context <context> --namespace observability --create-namespace \
  -f values-telemetry.yaml -f values-<集群名>.local.yaml --wait --timeout 10m
```

字段含义、后端地址格式、 rollout 验收命令见[部署文档](02-部署文档.md)；接入第一个 Java 服务、灰度与推广流程见同文档第 5 节起。

## new-dev 已验证结论（2026-10-24 压测）

| 链路 | 结论 |
|---|---|
| traces | 6,405 traces/s 满载下保留率与采样策略精确一致（旧配置 667 traces/s 即断崖，修复 9.6 倍）；gateway 双副本 32,024 spans/s 零拒绝 |
| logs | 导出吞吐 ~95 → 3,877 条/s（41 倍）、全链零丢失；**后端持续摄入上限 ~3.8k 条/s**，200 微服务上线前需按实际日志量复核后端容量 |
| 注入 | Java 17/21 + Spring Boot 3.5.16 真实自动注入，Prometheus / ES / Tempo 三信号低流量验证通过 |

完整数据、复现步骤与边界见[压测报告](04-压测报告.md)；上线前风险清单（P0/P1/P2 分级）见[生产审查报告](03-生产审查报告.md)。

## 新集群安装前必读

1. **`values-platform.yaml` 是模板不是默认值**：`cluster_name` 等字段留空即被 `validate.yaml` 拒绝安装，必须复制为 `values-<集群名>.local.yaml` 填写；该文件含真实凭据，**不要入库或分享**。
2. **`num_traces` 按集群速率与副本数核算**：公式 `num_traces ≥ 1.5 × 集群 traces/s × decision_wait / sampler_replicas`，当前 200000 对应 4,000 traces/s 目标（chart 默认值与随包 `values-telemetry.yaml` 一致，均为 200000 / 300000×2）；超限是静默断崖（无报错、保留归零），务必按告警 `sampled/new_trace_id_received` 监控。
3. **`log_namespaces` / `otlp_namespace_selector` / `monitor_namespace_selector`**：new-dev 的收紧值只适用于测试集群，生产留默认（全集群 + opt-in 注解过滤），照抄会只采 2 个 namespace。
4. **`collector_memory_mib`**：与节点/集群实际可调度资源核对后再调整，memory_limiter 与 GOMEMLIMIT 会自动联动。
5. **new-dev 专属参数不复制到生产**：CA、节点 hostAliases 映射、内存队列、访问凭据仅适用于该测试集群（见[生产审查报告](03-生产审查报告.md) A 类 8 项）。

## 维护说明

- chart 源码在 `charts/otel-platform/`，与 `otel-platform-0.7.5.tgz` 对应；**修改源码后必须重新打包并更新 `charts/SHA256SUMS`**，否则 `shasum -c` 校验失败。
- Operator chart（`opentelemetry-operator-0.126.1.tgz`）保持官方原样，未做任何修改；平台差异全部通过 `values-*.yaml` 覆盖。
- 升级用 `helm upgrade`（命令同快速开始第 4 步）；collector `terminationGracePeriodSeconds=120`，滚动更新旧实例排水最长约 2 分钟，`rollout status` 超时建议设 ≥240s。
- 样例、测试工具、原始报告、缓存和旧版安装包已清理。安装不依赖 Python 生成器或测试目录。
